"""Trader quote/execution helpers for lag strategy."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Sequence

import httpx

logger = logging.getLogger(__name__)

# Native SOL mint address used by Jupiter / DexScreener
SOL_NATIVE_MINT = "So11111111111111111111111111111111111111112"

# Default Solana RPC endpoint
_DEFAULT_RPC_URL = "https://api.mainnet-beta.solana.com"

# In-memory cache: mint address → decimals (immutable on-chain, safe to cache forever)
_decimals_cache: Dict[str, int] = {
    SOL_NATIVE_MINT: 9,
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": 6,  # USDC
}

_SPL_DEFAULT_DECIMALS = 9


async def get_token_decimals(
    mint_address: str,
    rpc_url: str = _DEFAULT_RPC_URL,
) -> int:
    """Fetch SPL token decimals from Solana RPC with in-memory caching.

    Decimals are immutable after mint creation, so results are cached forever.
    Falls back to 9 (the SPL default) on any failure.
    """
    if mint_address in _decimals_cache:
        return _decimals_cache[mint_address]

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                rpc_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getAccountInfo",
                    "params": [mint_address, {"encoding": "jsonParsed"}],
                },
            )
            resp.raise_for_status()
            data = resp.json()
            decimals = (
                data.get("result", {})
                .get("value", {})
                .get("data", {})
                .get("parsed", {})
                .get("info", {})
                .get("decimals")
            )
            if isinstance(decimals, int):
                _decimals_cache[mint_address] = decimals
                return decimals
    except Exception:
        logger.warning("Failed to fetch decimals for %s; defaulting to %d", mint_address, _SPL_DEFAULT_DECIMALS)

    _decimals_cache[mint_address] = _SPL_DEFAULT_DECIMALS
    return _SPL_DEFAULT_DECIMALS


@dataclass
class TraderMethodSet:
    """Resolved trader tool names for quote + execution."""

    quote_method: str
    execute_method: str
    buy_method: str = ""
    sell_method: str = ""

    def execute_for_side(self, side: str) -> str:
        """Return the best execute method for the given trade side."""
        if side == "buy" and self.buy_method:
            return self.buy_method
        if side == "sell" and self.sell_method:
            return self.sell_method
        return self.execute_method


@dataclass
class TradeQuote:
    """Normalized executable quote."""

    price: float
    method: str
    raw: Any
    liquidity_usd: Optional[float] = None


@dataclass
class TradeExecution:
    """Normalized execution response."""

    success: bool
    method: Optional[str]
    raw: Any
    tx_hash: Optional[str] = None
    executed_price: Optional[float] = None
    quantity_token: Optional[float] = None
    error: Optional[str] = None


class TraderExecutionService:
    """Handles trader tool discovery, quote retrieval, and trade execution."""

    def __init__(
        self,
        mcp_manager: Any,
        chain: str,
        max_slippage_bps: int,
        quote_method_override: str = "",
        execute_method_override: str = "",
        quote_mint: str = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        rpc_url: str = _DEFAULT_RPC_URL,
    ) -> None:
        self.mcp_manager = mcp_manager
        self.chain = chain.lower()
        self.max_slippage_bps = max_slippage_bps
        self.quote_method_override = quote_method_override.strip()
        self.execute_method_override = execute_method_override.strip()
        self.quote_mint = quote_mint
        self.rpc_url = rpc_url
        self._method_cache: Optional[TraderMethodSet] = None

    def _get_trader_client(self) -> Any:
        trader = self.mcp_manager.get_client("trader")
        if trader is None:
            raise RuntimeError("Trader MCP client is not configured")
        return trader

    def _resolve_methods(self) -> TraderMethodSet:
        if self._method_cache is not None:
            return self._method_cache

        trader = self._get_trader_client()
        tool_names = [tool.get("name", "") for tool in (trader.tools or []) if tool.get("name")]
        if not tool_names:
            raise RuntimeError("Trader MCP client has no tools")

        quote_method = self.quote_method_override or self._pick_method(
            tool_names,
            exact_candidates=(
                "get_quote",
                "quote",
                "getQuote",
                "quote_swap",
                "swap_quote",
                "jupiter_quote",
            ),
            contains_candidates=("quote",),
        )
        execute_method = self.execute_method_override or self._pick_method(
            tool_names,
            exact_candidates=(
                "swap",
                "execute_swap",
                "trade",
                "execute_trade",
                "place_order",
            ),
            contains_candidates=("swap", "trade", "order"),
        )

        # Side-specific execute methods for traders that expose buy/sell separately
        buy_method = self._pick_method(
            tool_names,
            exact_candidates=("buy_token", "buy", "buyToken"),
            contains_candidates=("buy",),
        )
        sell_method = self._pick_method(
            tool_names,
            exact_candidates=("sell_token", "sell", "sellToken"),
            contains_candidates=("sell",),
        )

        if not quote_method:
            raise RuntimeError(f"Unable to resolve trader quote method from tools: {tool_names}")
        if not execute_method and not (buy_method and sell_method):
            raise RuntimeError(f"Unable to resolve trader execute method from tools: {tool_names}")

        self._method_cache = TraderMethodSet(
            quote_method=quote_method,
            execute_method=execute_method,
            buy_method=buy_method,
            sell_method=sell_method,
        )
        return self._method_cache

    @staticmethod
    def _pick_method(
        tool_names: Sequence[str],
        exact_candidates: Sequence[str],
        contains_candidates: Sequence[str],
    ) -> str:
        exact_lookup = {name.lower(): name for name in tool_names}
        for candidate in exact_candidates:
            hit = exact_lookup.get(candidate.lower())
            if hit:
                return hit

        for name in tool_names:
            lower_name = name.lower()
            if any(token in lower_name for token in contains_candidates):
                return name

        return ""

    def _get_tool_schema(self, method_name: str) -> Dict[str, Any]:
        trader = self._get_trader_client()
        for tool in trader.tools or []:
            if tool.get("name") == method_name:
                return tool
        return {}

    async def get_quote(
        self,
        token_address: str,
        notional_usd: float,
        side: str = "buy",
        input_price_usd: Optional[float] = None,
        token_decimals: Optional[int] = None,
        quantity_token: Optional[float] = None,
    ) -> TradeQuote:
        """Fetch executable quote from trader MCP."""
        if token_decimals is None:
            token_decimals = await get_token_decimals(token_address, self.rpc_url)
        trader = self._get_trader_client()
        method = self._resolve_methods().quote_method
        tool_schema = self._get_tool_schema(method)
        args = self._build_tool_args(
            tool_schema=tool_schema,
            token_address=token_address,
            notional_usd=notional_usd,
            side=side,
            quantity_token=quantity_token,
            quote_payload=None,
            input_price_usd=input_price_usd,
            token_decimals=token_decimals,
        )
        result = await trader.call_tool(method, args)
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except (json.JSONDecodeError, TypeError):
                pass
        price = self._extract_price(
            result, side=side, native_price_usd=input_price_usd,
            token_decimals=token_decimals,
        )
        if price is None or price <= 0:
            logger.warning("Trader quote response has no valid price: %s", result)
            raise RuntimeError(f"Trader quote did not include a valid price (method: {method})")

        liquidity = self._extract_first_float(
            result,
            ("liquidityUsd", "liquidity_usd", "liquidity", "liquidityUSD"),
        )
        return TradeQuote(price=price, method=method, raw=result, liquidity_usd=liquidity)

    async def get_wallet_token_balance(self, token_address: str) -> Optional[float]:
        """Query actual token balance from the trader wallet via get_balance.

        Returns the human-readable ``uiAmount`` if available, or ``None``
        when the MCP doesn't support ``get_balance`` or the call fails.
        """
        trader = self.mcp_manager.get_client("trader")
        if trader is None:
            return None
        tool_names = [t.get("name", "") for t in (trader.tools or []) if t.get("name")]
        if "get_balance" not in tool_names:
            return None
        try:
            result = await trader.call_tool("get_balance", {"token_address": token_address})
            if isinstance(result, str):
                try:
                    result = json.loads(result)
                except (json.JSONDecodeError, TypeError):
                    pass
            if isinstance(result, dict):
                tb = result.get("tokenBalance")
                if isinstance(tb, dict):
                    ui = tb.get("uiAmount")
                    if ui is not None:
                        return float(ui)
        except Exception as exc:
            logger.debug("get_balance failed for %s: %s", token_address, exc)
        return None

    async def execute_trade(
        self,
        token_address: str,
        notional_usd: float,
        side: str,
        quantity_token: Optional[float],
        dry_run: bool,
        quote: Optional[TradeQuote],
        input_price_usd: Optional[float] = None,
        token_decimals: Optional[int] = None,
    ) -> TradeExecution:
        """Execute trade through trader MCP or simulate in dry-run mode."""
        if token_decimals is None:
            token_decimals = await get_token_decimals(token_address, self.rpc_url)

        if dry_run:
            executed_price = quote.price if quote else None
            quantity = quantity_token
            if quantity is None and executed_price and executed_price > 0:
                quantity = notional_usd / executed_price
            return TradeExecution(
                success=True,
                method=None,
                raw={"dry_run": True},
                tx_hash=None,
                executed_price=executed_price,
                quantity_token=quantity,
                error=None,
            )

        trader = self._get_trader_client()
        methods = self._resolve_methods()
        method = methods.execute_for_side(side)
        tool_schema = self._get_tool_schema(method)
        args = self._build_tool_args(
            tool_schema=tool_schema,
            token_address=token_address,
            notional_usd=notional_usd,
            side=side,
            quantity_token=quantity_token,
            quote_payload=quote.raw if quote else None,
            input_price_usd=input_price_usd,
            token_decimals=token_decimals,
        )
        result = await trader.call_tool(method, args)

        success = self._extract_success(result)
        error = self._extract_error(result)
        tx_hash = self._extract_tx_hash(result)
        executed_price = self._extract_price(
            result, side=side, native_price_usd=input_price_usd,
            token_decimals=token_decimals,
        )
        executed_qty = self._extract_first_float(
            result,
            (
                "quantity",
                "quantityToken",
                "qty",
                "filledAmount",
                "tokenSold",
                "token_sold",
            ),
        )
        if executed_qty is None:
            # tokenReceived from buy_token is raw smallest units — convert
            raw_received = self._extract_first_float(
                result,
                ("tokenReceived", "token_received", "outputAmount", "outAmount", "amountOut"),
            )
            if raw_received is not None and raw_received > 0:
                executed_qty = raw_received / (10 ** token_decimals)

        if success and executed_qty is None and executed_price and executed_price > 0:
            executed_qty = notional_usd / executed_price

        # Live trades must have a tx_hash to be considered successful
        if success and tx_hash is None:
            success = False
            if error is None:
                error = "No transaction hash in trader response"

        if not success and error is None:
            error = f"Trader execute method '{method}' returned unsuccessful response"

        return TradeExecution(
            success=success,
            method=method,
            raw=result,
            tx_hash=tx_hash,
            executed_price=executed_price,
            quantity_token=executed_qty,
            error=error,
        )

    def _build_tool_args(
        self,
        tool_schema: Dict[str, Any],
        token_address: str,
        notional_usd: float,
        side: str,
        quantity_token: Optional[float],
        quote_payload: Optional[Any],
        input_price_usd: Optional[float] = None,
        token_decimals: int = _SPL_DEFAULT_DECIMALS,
    ) -> Dict[str, Any]:
        input_schema = tool_schema.get("inputSchema", {})
        properties = input_schema.get("properties", {})
        required = input_schema.get("required", [])

        args: Dict[str, Any] = {}

        for key in properties.keys():
            value = self._value_for_param(
                param_name=key,
                token_address=token_address,
                notional_usd=notional_usd,
                side=side,
                quantity_token=quantity_token,
                quote_payload=quote_payload,
                input_price_usd=input_price_usd,
                token_decimals=token_decimals,
            )
            if value is not None:
                args[key] = value

        for key in required:
            if key in args:
                continue
            value = self._value_for_param(
                param_name=key,
                token_address=token_address,
                notional_usd=notional_usd,
                side=side,
                quantity_token=quantity_token,
                quote_payload=quote_payload,
                input_price_usd=input_price_usd,
                token_decimals=token_decimals,
            )
            if value is None:
                raise ValueError(f"Unable to infer required trader argument: {key}")
            args[key] = value

        return args

    def _value_for_param(
        self,
        param_name: str,
        token_address: str,
        notional_usd: float,
        side: str,
        quantity_token: Optional[float],
        quote_payload: Optional[Any],
        input_price_usd: Optional[float] = None,
        token_decimals: int = _SPL_DEFAULT_DECIMALS,
    ) -> Any:
        key = param_name.lower()

        if key in {"chain", "network", "chainid"}:
            return self.chain
        if key in {"side", "action", "direction", "trade_side"}:
            return side
        if "dry" in key and "run" in key:
            return False

        if quote_payload is not None:
            if key in {"quote", "quote_response", "route", "route_plan", "swap_quote"}:
                return quote_payload

        is_tokenish = any(token in key for token in ("mint", "token", "address"))
        is_amount_like = any(token in key for token in ("amount", "size", "qty", "quantity", "decimal"))
        if is_tokenish and not is_amount_like:
            is_input = any(
                token in key
                for token in ("input", "from", "source", "sell", "inmint", "tokenin", "in_token")
            )
            is_output = any(
                token in key
                for token in ("output", "to", "destination", "buy", "outmint", "tokenout", "out_token")
            )
            if is_input:
                # buy_token always uses SOL as input; match that for quotes
                return SOL_NATIVE_MINT if side == "buy" else token_address
            if is_output:
                return token_address if side == "buy" else SOL_NATIVE_MINT
            return token_address

        if "slippage" in key:
            if "bps" in key:
                return int(self.max_slippage_bps)
            return round(self.max_slippage_bps / 100, 4)

        if any(token in key for token in ("notional", "usd")):
            return float(notional_usd)

        if "lamport" in key:
            if input_price_usd and input_price_usd > 0:
                return int((notional_usd / input_price_usd) * 1_000_000_000)
            logger.warning("No input_price_usd for lamport conversion; falling back to raw notional")
            return int(notional_usd * 1_000_000_000)

        if "amount" in key or "size" in key or "qty" in key or "quantity" in key:
            if quantity_token is not None and side == "sell":
                return float(quantity_token)
            if input_price_usd and input_price_usd > 0:
                return float(notional_usd / input_price_usd)
            return float(notional_usd)

        if "decimal" in key:
            is_input_dec = "input" in key or "in_" in key
            if is_input_dec:
                # input_decimals: buy → native (SOL=9), sell → token
                return 9 if side == "buy" else token_decimals
            return token_decimals

        if "symbol" in key:
            return "USDC" if side == "buy" else "TOKEN"

        return None

    @classmethod
    def _extract_success(cls, payload: Any) -> bool:
        if isinstance(payload, dict):
            if "success" in payload:
                return bool(payload["success"])
            if "ok" in payload:
                return bool(payload["ok"])
            status = payload.get("status")
            if isinstance(status, str):
                status_l = status.lower()
                if status_l in {"success", "succeeded", "confirmed", "completed"}:
                    return True
                if status_l in {"failed", "error", "rejected"}:
                    return False
            err = payload.get("error")
            if err:
                return False
        return True

    @classmethod
    def _extract_error(cls, payload: Any) -> Optional[str]:
        if isinstance(payload, dict):
            err = payload.get("error")
            if isinstance(err, str):
                return err
            if isinstance(err, dict):
                message = err.get("message")
                if isinstance(message, str):
                    return message
        return None

    @classmethod
    def _extract_tx_hash(cls, payload: Any) -> Optional[str]:
        value = cls._extract_first_value(
            payload,
            ("txHash", "tx_hash", "signature", "transactionHash", "transaction", "txid", "hash"),
        )
        return value if isinstance(value, str) and value else None

    @classmethod
    def _extract_price(
        cls,
        payload: Any,
        side: str,
        native_price_usd: Optional[float] = None,
        native_decimals: int = 9,
        token_decimals: int = 9,
    ) -> Optional[float]:
        """Extract a USD-denominated token price from a trader response.

        When the response only contains raw in/out amounts (lamports / smallest
        token units), ``native_price_usd`` is used to convert to USD.
        """
        # 1. Direct USD price field
        direct_price = cls._extract_first_float(
            payload,
            (
                "price",
                "priceUsd",
                "price_usd",
                "executionPrice",
                "executedPrice",
                "fillPrice",
                "estimatedPrice",
                "estimated_price",
                "expectedPrice",
                "expected_price",
                "quotePrice",
                "quote_price",
                "swapPrice",
                "swap_price",
            ),
        )
        if direct_price and direct_price > 0:
            return direct_price

        # 2. Derive from SOL spent/received (human-readable) fields
        sol_spent = cls._extract_first_float(payload, ("solSpent", "sol_spent"))
        token_received = cls._extract_first_float(
            payload, ("tokenReceived", "token_received"),
        )
        sol_received = cls._extract_first_float(
            payload, ("solReceived", "sol_received"),
        )
        token_sold = cls._extract_first_float(payload, ("tokenSold", "token_sold"))

        if native_price_usd and native_price_usd > 0:
            if side == "buy" and sol_spent and token_received:
                # token_received from buy_token is raw (smallest units)
                token_human = token_received / (10 ** token_decimals)
                if token_human > 0:
                    return (sol_spent * native_price_usd) / token_human
            if side == "sell" and sol_received and token_sold:
                # token_sold is human-readable, sol_received is human-readable
                if token_sold > 0:
                    return (sol_received * native_price_usd) / token_sold

        # 3. Derive from raw in/out amounts
        in_amount = cls._extract_first_float(
            payload,
            (
                "inAmount",
                "inputAmount",
                "amountIn",
                "fromAmount",
                "input_amount",
                "amount_in",
            ),
        )
        out_amount = cls._extract_first_float(
            payload,
            (
                "outAmount",
                "outputAmount",
                "amountOut",
                "toAmount",
                "output_amount",
                "amount_out",
            ),
        )
        if not in_amount or not out_amount:
            return None
        if in_amount <= 0 or out_amount <= 0:
            return None

        if native_price_usd and native_price_usd > 0:
            # Convert raw amounts to human-readable, then to USD per token
            if side == "buy":
                # in = native (SOL lamports), out = token smallest units
                native_human = in_amount / (10 ** native_decimals)
                token_human = out_amount / (10 ** token_decimals)
                if token_human > 0:
                    return (native_human * native_price_usd) / token_human
            else:
                # in = token smallest units, out = native (SOL lamports)
                token_human = in_amount / (10 ** token_decimals)
                native_human = out_amount / (10 ** native_decimals)
                if token_human > 0:
                    return (native_human * native_price_usd) / token_human

        # Fallback: raw ratio (no USD context available)
        if side == "buy":
            return in_amount / out_amount
        return out_amount / in_amount

    @classmethod
    def _extract_first_float(
        cls,
        payload: Any,
        keys: Sequence[str],
    ) -> Optional[float]:
        value = cls._extract_first_value(payload, keys)
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            cleaned = value.replace(",", "").strip()
            try:
                return float(cleaned)
            except ValueError:
                return None
        return None

    @classmethod
    def _extract_first_value(
        cls,
        payload: Any,
        keys: Sequence[str],
    ) -> Optional[Any]:
        key_lookup = {key.lower() for key in keys}
        for found_key, found_value in cls._walk_items(payload):
            if found_key.lower() in key_lookup:
                return found_value
        return None

    @classmethod
    def _walk_items(cls, payload: Any) -> Iterable[tuple[str, Any]]:
        if isinstance(payload, dict):
            for key, value in payload.items():
                yield str(key), value
                yield from cls._walk_items(value)
        elif isinstance(payload, list):
            for item in payload:
                yield from cls._walk_items(item)
