"""Tests for lag strategy trader execution helpers."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import pytest

from app.lag_execution import TraderExecutionService, get_token_decimals, _decimals_cache


def test_extract_price_alternative_keys() -> None:
    """_extract_price finds prices under alternative key names."""
    extract = TraderExecutionService._extract_price
    assert extract({"estimatedPrice": "1.5"}, side="buy") == pytest.approx(1.5)
    assert extract({"quotePrice": 0.003}, side="buy") == pytest.approx(0.003)
    assert extract({"swap_price": "42.1"}, side="sell") == pytest.approx(42.1)
    assert extract({"expected_price": 100}, side="buy") == pytest.approx(100.0)
    assert extract({}, side="buy") is None
    assert extract({"unrelated": "data"}, side="buy") is None


def test_extract_success_string_error() -> None:
    """_extract_success returns False for string error responses from MCP."""
    extract = TraderExecutionService._extract_success
    assert extract("Error: Jupiter quote failed (400)") is False
    assert extract("error: something went wrong") is False
    assert extract({"status": "success"}) is True
    assert extract("All good") is True


def test_extract_error_string_payload() -> None:
    """_extract_error captures error message from string payloads."""
    extract = TraderExecutionService._extract_error
    assert extract("Error: InsufficientFunds") == "Error: InsufficientFunds"
    assert extract("All good") is None
    assert extract({"error": "bad request"}) == "bad request"


class MockTraderClient:
    """Minimal trader MCP client mock."""

    def __init__(self) -> None:
        self.tools: List[Dict[str, Any]] = [
            {
                "name": "getQuote",
                "inputSchema": {
                    "type": "object",
                    "required": ["chain", "inputMint", "outputMint", "amountUsd", "slippageBps"],
                    "properties": {
                        "chain": {"type": "string"},
                        "inputMint": {"type": "string"},
                        "outputMint": {"type": "string"},
                        "amountUsd": {"type": "number"},
                        "slippageBps": {"type": "integer"},
                        "side": {"type": "string"},
                    },
                },
            },
            {
                "name": "swap",
                "inputSchema": {
                    "type": "object",
                    "required": ["chain", "inputMint", "outputMint", "amountUsd", "slippageBps", "side"],
                    "properties": {
                        "chain": {"type": "string"},
                        "inputMint": {"type": "string"},
                        "outputMint": {"type": "string"},
                        "amountUsd": {"type": "number"},
                        "slippageBps": {"type": "integer"},
                        "side": {"type": "string"},
                    },
                },
            },
        ]
        self.calls: List[Tuple[str, Dict[str, Any]]] = []

    async def call_tool(self, method: str, arguments: Dict[str, Any]) -> Any:
        self.calls.append((method, arguments))
        if method == "getQuote":
            return {"priceUsd": "1.05", "liquidityUsd": 250000}
        if method == "swap":
            return {
                "success": True,
                "txHash": "mock-tx-hash",
                "executedPrice": "1.02",
                "quantity": "25",
            }
        raise ValueError(f"Unknown method: {method}")


class MockMCPManager:
    """Minimal MCP manager mock exposing trader only."""

    def __init__(self, trader: MockTraderClient) -> None:
        self._trader = trader

    def get_client(self, name: str) -> Any:
        if name == "trader":
            return self._trader
        return None


@pytest.mark.asyncio
async def test_get_quote_auto_detects_method_and_maps_args() -> None:
    trader = MockTraderClient()
    service = TraderExecutionService(
        mcp_manager=MockMCPManager(trader),
        chain="solana",
        max_slippage_bps=120,
    )

    quote = await service.get_quote(
        token_address="TokenAddress1111111111111111111111111111111111",
        notional_usd=50,
        side="buy",
    )

    assert quote.price == pytest.approx(1.05)
    assert quote.method == "getQuote"
    method, args = trader.calls[0]
    assert method == "getQuote"
    assert args["chain"] == "solana"
    assert args["amountUsd"] == pytest.approx(50.0)
    assert args["slippageBps"] == 120
    assert args["inputMint"] != args["outputMint"]


@pytest.mark.asyncio
async def test_execute_trade_dry_run() -> None:
    trader = MockTraderClient()
    service = TraderExecutionService(
        mcp_manager=MockMCPManager(trader),
        chain="solana",
        max_slippage_bps=100,
    )
    quote = await service.get_quote(
        token_address="TokenAddress1111111111111111111111111111111111",
        notional_usd=25,
        side="buy",
    )
    result = await service.execute_trade(
        token_address="TokenAddress1111111111111111111111111111111111",
        notional_usd=25,
        side="buy",
        quantity_token=None,
        dry_run=True,
        quote=quote,
    )

    assert result.success is True
    assert result.method is None
    assert result.tx_hash is None
    assert result.executed_price == pytest.approx(quote.price)


@pytest.mark.asyncio
async def test_execute_trade_live() -> None:
    trader = MockTraderClient()
    service = TraderExecutionService(
        mcp_manager=MockMCPManager(trader),
        chain="solana",
        max_slippage_bps=80,
    )
    quote = await service.get_quote(
        token_address="TokenAddress1111111111111111111111111111111111",
        notional_usd=25,
        side="buy",
    )
    result = await service.execute_trade(
        token_address="TokenAddress1111111111111111111111111111111111",
        notional_usd=25,
        side="buy",
        quantity_token=None,
        dry_run=False,
        quote=quote,
    )

    assert result.success is True
    assert result.method == "swap"
    assert result.tx_hash == "mock-tx-hash"
    assert result.executed_price == pytest.approx(1.02)


class MockDirectionalTraderClient:
    """Trader MCP client mock with buy_token/sell_token tools."""

    def __init__(self) -> None:
        self.tools: list[dict[str, Any]] = [
            {
                "name": "get_quote",
                "inputSchema": {
                    "type": "object",
                    "required": ["chain", "inputMint", "outputMint", "amountUsd"],
                    "properties": {
                        "chain": {"type": "string"},
                        "inputMint": {"type": "string"},
                        "outputMint": {"type": "string"},
                        "amountUsd": {"type": "number"},
                    },
                },
            },
            {
                "name": "buy_token",
                "inputSchema": {
                    "type": "object",
                    "required": ["chain", "inputMint", "outputMint", "amountUsd", "slippageBps"],
                    "properties": {
                        "chain": {"type": "string"},
                        "inputMint": {"type": "string"},
                        "outputMint": {"type": "string"},
                        "amountUsd": {"type": "number"},
                        "slippageBps": {"type": "integer"},
                    },
                },
            },
            {
                "name": "sell_token",
                "inputSchema": {
                    "type": "object",
                    "required": ["chain", "inputMint", "outputMint", "amountUsd", "slippageBps"],
                    "properties": {
                        "chain": {"type": "string"},
                        "inputMint": {"type": "string"},
                        "outputMint": {"type": "string"},
                        "amountUsd": {"type": "number"},
                        "slippageBps": {"type": "integer"},
                    },
                },
            },
            {
                "name": "get_balance",
                "inputSchema": {
                    "type": "object",
                    "required": ["chain"],
                    "properties": {"chain": {"type": "string"}},
                },
            },
        ]
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, method: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((method, arguments))
        if method == "get_quote":
            return {"priceUsd": "0.000025", "liquidityUsd": 100000}
        if method == "buy_token":
            return {"success": True, "txHash": "buy-tx-hash", "executedPrice": "0.000025", "quantity": "1000000"}
        if method == "sell_token":
            return {"success": True, "txHash": "sell-tx-hash", "executedPrice": "0.000026", "quantity": "1000000"}
        raise ValueError(f"Unknown method: {method}")


@pytest.mark.asyncio
async def test_directional_tools_resolve_buy_token() -> None:
    """buy_token/sell_token tools resolve correctly for buy side."""
    trader = MockDirectionalTraderClient()
    service = TraderExecutionService(
        mcp_manager=MockMCPManager(trader),
        chain="solana",
        max_slippage_bps=100,
    )
    quote = await service.get_quote(
        token_address="BonkMint111111111111111111111111111111111111",
        notional_usd=25,
        side="buy",
    )
    result = await service.execute_trade(
        token_address="BonkMint111111111111111111111111111111111111",
        notional_usd=25,
        side="buy",
        quantity_token=None,
        dry_run=False,
        quote=quote,
    )
    assert result.success is True
    assert result.method == "buy_token"
    assert result.tx_hash == "buy-tx-hash"


@pytest.mark.asyncio
async def test_directional_tools_resolve_sell_token() -> None:
    """buy_token/sell_token tools resolve correctly for sell side."""
    trader = MockDirectionalTraderClient()
    service = TraderExecutionService(
        mcp_manager=MockMCPManager(trader),
        chain="solana",
        max_slippage_bps=100,
    )
    quote = await service.get_quote(
        token_address="BonkMint111111111111111111111111111111111111",
        notional_usd=25,
        side="sell",
    )
    result = await service.execute_trade(
        token_address="BonkMint111111111111111111111111111111111111",
        notional_usd=25,
        side="sell",
        quantity_token=1000000,
        dry_run=False,
        quote=quote,
    )
    assert result.success is True
    assert result.method == "sell_token"
    assert result.tx_hash == "sell-tx-hash"


class MockRealTraderClient:
    """Mock matching the actual dex-trader-mcp tool schemas (sol_amount, token_amount).

    Responses mirror the real trader MCP: raw amounts, no priceUsd field.
    """

    def __init__(self) -> None:
        self.tools: list[dict[str, Any]] = [
            {
                "name": "get_quote",
                "inputSchema": {
                    "type": "object",
                    "required": ["input_mint", "output_mint", "amount"],
                    "properties": {
                        "input_mint": {"type": "string"},
                        "output_mint": {"type": "string"},
                        "amount": {"type": "number"},
                        "input_decimals": {"type": "integer"},
                        "slippage_bps": {"type": "integer"},
                    },
                },
            },
            {
                "name": "buy_token",
                "inputSchema": {
                    "type": "object",
                    "required": ["token_address", "sol_amount"],
                    "properties": {
                        "token_address": {"type": "string"},
                        "sol_amount": {"type": "number"},
                        "slippage_bps": {"type": "integer"},
                    },
                },
            },
            {
                "name": "sell_token",
                "inputSchema": {
                    "type": "object",
                    "required": ["token_address", "token_amount", "token_decimals"],
                    "properties": {
                        "token_address": {"type": "string"},
                        "token_amount": {"type": "number"},
                        "token_decimals": {"type": "integer"},
                        "slippage_bps": {"type": "integer"},
                    },
                },
            },
            {
                "name": "get_balance",
                "inputSchema": {
                    "type": "object",
                    "required": [],
                    "properties": {
                        "token_address": {"type": "string"},
                    },
                },
            },
        ]
        self.calls: list[tuple[str, dict[str, Any]]] = []
        # Token price: $0.000025 USD, SOL price: $200 USD
        # 1 token costs $0.000025, so 1 SOL ($200) buys 8_000_000 tokens
        # For a $0.50 trade: 0.0025 SOL → 20_000 tokens
        self.sol_price_usd = 200.0
        self.token_price_usd = 0.000025

    async def call_tool(self, method: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((method, arguments))
        if method == "get_quote":
            # Real trader returns raw lamport/smallest-unit amounts
            sol_amount = arguments.get("amount", 0.0025)
            sol_lamports = int(sol_amount * 1_000_000_000)
            token_raw = int(sol_amount * self.sol_price_usd / self.token_price_usd * 1_000_000_000)
            return {
                "inputAmount": str(sol_lamports),
                "outputAmount": str(token_raw),
                "priceImpact": "0.01",
                "slippageBps": 100,
                "route": "Jupiter",
            }
        if method == "buy_token":
            sol_amount = arguments.get("sol_amount", 0.0025)
            token_raw = int(sol_amount * self.sol_price_usd / self.token_price_usd * 1_000_000_000)
            return {
                "status": "success",
                "transaction": "buy-tx-real",
                "solSpent": sol_amount,
                "tokenReceived": str(token_raw),
                "tokenMint": arguments.get("token_address", ""),
                "explorer": "https://solscan.io/tx/buy-tx-real",
            }
        if method == "sell_token":
            token_amount = arguments.get("token_amount", 20000.0)
            sol_received = token_amount * self.token_price_usd / self.sol_price_usd
            return {
                "status": "success",
                "transaction": "sell-tx-real",
                "tokenSold": token_amount,
                "tokenMint": arguments.get("token_address", ""),
                "solReceived": sol_received,
                "explorer": "https://solscan.io/tx/sell-tx-real",
            }
        if method == "get_balance":
            return {
                "wallet": "FakeWallet111111111111111111111111111111111",
                "solBalance": 1.5,
                "tokenBalance": {
                    "mint": arguments.get("token_address", ""),
                    "amount": "19800000000",
                    "decimals": 9,
                    "uiAmount": 19800.0,
                },
            }
        raise ValueError(f"Unknown method: {method}")


@pytest.mark.asyncio
async def test_buy_converts_usd_to_sol_amount() -> None:
    """buy_token with sol_amount param converts USD to SOL using input_price_usd."""
    trader = MockRealTraderClient()
    service = TraderExecutionService(
        mcp_manager=MockMCPManager(trader),
        chain="solana",
        max_slippage_bps=100,
    )
    sol_price = 200.0
    notional_usd = 0.50

    quote = await service.get_quote(
        token_address="BonkMint111111111111111111111111111111111111",
        notional_usd=notional_usd,
        side="buy",
        input_price_usd=sol_price,
    )

    # Quote price should be in USD per token, not raw ratio
    assert quote.price == pytest.approx(0.000025, rel=1e-2)

    result = await service.execute_trade(
        token_address="BonkMint111111111111111111111111111111111111",
        notional_usd=notional_usd,
        side="buy",
        quantity_token=None,
        dry_run=False,
        quote=quote,
        input_price_usd=sol_price,
    )

    assert result.success is True
    assert result.method == "buy_token"
    assert result.tx_hash == "buy-tx-real"

    # Verify the buy_token call received SOL amount, not USD
    buy_call = [c for c in trader.calls if c[0] == "buy_token"][0]
    sol_amount = buy_call[1]["sol_amount"]
    expected_sol = notional_usd / sol_price  # 0.50 / 200 = 0.0025
    assert sol_amount == pytest.approx(expected_sol, rel=1e-6)
    # Must NOT be the raw USD value
    assert sol_amount != pytest.approx(notional_usd)

    # Executed price should be in USD per token
    assert result.executed_price == pytest.approx(0.000025, rel=1e-2)
    # Quantity should be human-readable token count
    assert result.quantity_token is not None
    assert result.quantity_token == pytest.approx(notional_usd / 0.000025, rel=1e-2)


@pytest.mark.asyncio
async def test_sell_passes_quantity_not_usd() -> None:
    """sell_token with token_amount param uses quantity_token on sell side."""
    trader = MockRealTraderClient()
    service = TraderExecutionService(
        mcp_manager=MockMCPManager(trader),
        chain="solana",
        max_slippage_bps=100,
    )
    sol_price = 200.0
    quantity = 20000.0

    quote = await service.get_quote(
        token_address="BonkMint111111111111111111111111111111111111",
        notional_usd=0.50,
        side="sell",
        input_price_usd=sol_price,
    )
    result = await service.execute_trade(
        token_address="BonkMint111111111111111111111111111111111111",
        notional_usd=0.50,
        side="sell",
        quantity_token=quantity,
        dry_run=False,
        quote=quote,
        input_price_usd=sol_price,
    )

    assert result.success is True
    assert result.method == "sell_token"
    assert result.tx_hash == "sell-tx-real"
    sell_call = [c for c in trader.calls if c[0] == "sell_token"][0]
    assert sell_call[1]["token_amount"] == pytest.approx(quantity)
    assert sell_call[1]["token_decimals"] == 9

    # Executed price should be in USD per token
    assert result.executed_price == pytest.approx(0.000025, rel=1e-2)


@pytest.mark.asyncio
async def test_quote_converts_usd_to_input_token_amount() -> None:
    """get_quote with generic 'amount' param converts USD to input token units."""
    trader = MockRealTraderClient()
    service = TraderExecutionService(
        mcp_manager=MockMCPManager(trader),
        chain="solana",
        max_slippage_bps=100,
    )
    sol_price = 200.0
    notional_usd = 10.0

    await service.get_quote(
        token_address="BonkMint111111111111111111111111111111111111",
        notional_usd=notional_usd,
        side="buy",
        input_price_usd=sol_price,
    )

    quote_call = trader.calls[0]
    assert quote_call[0] == "get_quote"
    amount = quote_call[1]["amount"]
    expected = notional_usd / sol_price  # 10 / 200 = 0.05 SOL
    assert amount == pytest.approx(expected, rel=1e-6)


@pytest.mark.asyncio
async def test_amount_without_price_falls_back_to_raw_usd() -> None:
    """When input_price_usd is not provided, amount falls back to raw notional_usd."""
    trader = MockRealTraderClient()
    service = TraderExecutionService(
        mcp_manager=MockMCPManager(trader),
        chain="solana",
        max_slippage_bps=100,
    )
    notional_usd = 0.50

    await service.get_quote(
        token_address="BonkMint111111111111111111111111111111111111",
        notional_usd=notional_usd,
        side="buy",
        # No input_price_usd provided
    )

    quote_call = trader.calls[0]
    assert quote_call[1]["amount"] == pytest.approx(notional_usd)


@pytest.mark.asyncio
async def test_quote_price_is_usd_from_raw_amounts() -> None:
    """get_quote converts raw inputAmount/outputAmount to USD price per token."""
    trader = MockRealTraderClient()
    service = TraderExecutionService(
        mcp_manager=MockMCPManager(trader),
        chain="solana",
        max_slippage_bps=100,
    )
    sol_price = 200.0
    quote = await service.get_quote(
        token_address="BonkMint111111111111111111111111111111111111",
        notional_usd=0.50,
        side="buy",
        input_price_usd=sol_price,
    )

    # Price must be USD per token, not raw lamport ratio
    assert quote.price == pytest.approx(0.000025, rel=1e-2)
    # Sanity: price should NOT be a tiny raw ratio like 1e-10
    assert quote.price > 1e-6


@pytest.mark.asyncio
async def test_pnl_calculation_is_sane_for_small_trade() -> None:
    """End-to-end: $0.50 buy+sell should produce PnL near $0, not $400+."""
    trader = MockRealTraderClient()
    service = TraderExecutionService(
        mcp_manager=MockMCPManager(trader),
        chain="solana",
        max_slippage_bps=100,
    )
    sol_price = 200.0
    notional_usd = 0.50

    # Simulate buy
    buy_quote = await service.get_quote(
        token_address="BonkMint111111111111111111111111111111111111",
        notional_usd=notional_usd,
        side="buy",
        input_price_usd=sol_price,
    )
    buy_result = await service.execute_trade(
        token_address="BonkMint111111111111111111111111111111111111",
        notional_usd=notional_usd,
        side="buy",
        quantity_token=None,
        dry_run=False,
        quote=buy_quote,
        input_price_usd=sol_price,
    )
    entry_price = buy_result.executed_price or buy_quote.price
    quantity = buy_result.quantity_token

    # Simulate sell at same price
    sell_result = await service.execute_trade(
        token_address="BonkMint111111111111111111111111111111111111",
        notional_usd=notional_usd,
        side="sell",
        quantity_token=quantity,
        dry_run=False,
        quote=buy_quote,
        input_price_usd=sol_price,
    )
    exit_price = sell_result.executed_price or buy_quote.price

    # PnL for a round-trip at same price should be near $0
    realized_pnl = (exit_price - entry_price) * quantity
    assert abs(realized_pnl) < 1.0, (
        f"PnL ${realized_pnl:.2f} is unreasonably large for a $0.50 trade"
    )


def test_extract_price_with_sol_spent_token_received() -> None:
    """_extract_price computes USD price from solSpent/tokenReceived."""
    extract = TraderExecutionService._extract_price
    # solSpent=0.0025 SOL, tokenReceived=20000*1e9 raw, SOL=$200
    # Price = (0.0025 * 200) / (20000e9 / 1e9) = 0.50 / 20000 = 0.000025
    price = extract(
        {"solSpent": 0.0025, "tokenReceived": str(20_000_000_000_000)},
        side="buy",
        native_price_usd=200.0,
    )
    assert price == pytest.approx(0.000025, rel=1e-2)


def test_extract_price_with_sol_received_token_sold() -> None:
    """_extract_price computes USD price from solReceived/tokenSold."""
    extract = TraderExecutionService._extract_price
    # tokenSold=20000, solReceived=0.0025, SOL=$200
    # Price = (0.0025 * 200) / 20000 = 0.000025
    price = extract(
        {"solReceived": 0.0025, "tokenSold": 20000.0},
        side="sell",
        native_price_usd=200.0,
    )
    assert price == pytest.approx(0.000025, rel=1e-2)


def test_extract_price_raw_amounts_converted_to_usd() -> None:
    """_extract_price converts raw inputAmount/outputAmount to USD using native_price_usd."""
    extract = TraderExecutionService._extract_price
    # inputAmount = 2500000 lamports (0.0025 SOL), outputAmount = 20000e9 raw tokens
    # USD price = (2500000/1e9 * 200) / (20000e9/1e9) = 0.50/20000 = 0.000025
    price = extract(
        {"inputAmount": "2500000", "outputAmount": "20000000000000"},
        side="buy",
        native_price_usd=200.0,
    )
    assert price == pytest.approx(0.000025, rel=1e-2)


def test_extract_price_raw_amounts_without_native_price_falls_back() -> None:
    """Without native_price_usd, _extract_price returns raw ratio as fallback."""
    extract = TraderExecutionService._extract_price
    price = extract(
        {"inputAmount": "2500000", "outputAmount": "20000000000000"},
        side="buy",
    )
    # Fallback: raw ratio = 2500000 / 20000000000000
    assert price == pytest.approx(2500000 / 20000000000000, rel=1e-6)


@pytest.mark.asyncio
async def test_live_trade_without_tx_hash_is_failure() -> None:
    """Live trade that returns no tx_hash should be marked as failed."""

    class NoTxHashTrader(MockRealTraderClient):
        async def call_tool(self, method: str, arguments: dict[str, Any]) -> Any:
            self.calls.append((method, arguments))
            if method == "get_quote":
                return await super().call_tool(method, arguments)
            # buy_token returns success but no transaction hash
            return {"status": "success", "message": "Something went wrong silently"}

    trader = NoTxHashTrader()
    service = TraderExecutionService(
        mcp_manager=MockMCPManager(trader),
        chain="solana",
        max_slippage_bps=100,
    )
    quote = await service.get_quote(
        token_address="BonkMint111111111111111111111111111111111111",
        notional_usd=0.50,
        side="buy",
        input_price_usd=200.0,
    )
    result = await service.execute_trade(
        token_address="BonkMint111111111111111111111111111111111111",
        notional_usd=0.50,
        side="buy",
        quantity_token=None,
        dry_run=False,
        quote=quote,
        input_price_usd=200.0,
    )

    assert result.success is False
    assert result.error == "No transaction hash in trader response"
    assert result.tx_hash is None


# --- Token decimals tests ---


class Mock6DecimalTraderClient(MockRealTraderClient):
    """Mock for a 6-decimal token (like WIF, PYTH, USDC)."""

    def __init__(self) -> None:
        super().__init__()
        self.token_decimals = 6
        self.token_price_usd = 2.50  # WIF-like price

    async def call_tool(self, method: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((method, arguments))
        if method == "get_quote":
            sol_amount = arguments.get("amount", 0.0025)
            sol_lamports = int(sol_amount * 1_000_000_000)
            # 6-decimal token: raw units = human * 10^6
            token_human = sol_amount * self.sol_price_usd / self.token_price_usd
            token_raw = int(token_human * 10 ** self.token_decimals)
            return {
                "inputAmount": str(sol_lamports),
                "outputAmount": str(token_raw),
                "priceImpact": "0.01",
                "slippageBps": 100,
                "route": "Jupiter",
            }
        if method == "buy_token":
            sol_amount = arguments.get("sol_amount", 0.0025)
            token_human = sol_amount * self.sol_price_usd / self.token_price_usd
            token_raw = int(token_human * 10 ** self.token_decimals)
            return {
                "status": "success",
                "transaction": "buy-tx-6dec",
                "solSpent": sol_amount,
                "tokenReceived": str(token_raw),
                "tokenMint": arguments.get("token_address", ""),
                "explorer": "https://solscan.io/tx/buy-tx-6dec",
            }
        if method == "sell_token":
            token_amount = arguments.get("token_amount", 0)
            sol_received = token_amount * self.token_price_usd / self.sol_price_usd
            return {
                "status": "success",
                "transaction": "sell-tx-6dec",
                "tokenSold": token_amount,
                "tokenMint": arguments.get("token_address", ""),
                "solReceived": sol_received,
                "explorer": "https://solscan.io/tx/sell-tx-6dec",
            }
        raise ValueError(f"Unknown method: {method}")


@pytest.mark.asyncio
async def test_buy_6_decimal_token_quantity_is_correct() -> None:
    """tokenReceived for a 6-decimal token should be divided by 10^6, not 10^9."""
    trader = Mock6DecimalTraderClient()
    service = TraderExecutionService(
        mcp_manager=MockMCPManager(trader),
        chain="solana",
        max_slippage_bps=100,
    )
    sol_price = 200.0
    notional_usd = 0.50
    token_price = 2.50
    token_decimals = 6

    quote = await service.get_quote(
        token_address="WifMint111111111111111111111111111111111111",
        notional_usd=notional_usd,
        side="buy",
        input_price_usd=sol_price,
        token_decimals=token_decimals,
    )
    result = await service.execute_trade(
        token_address="WifMint111111111111111111111111111111111111",
        notional_usd=notional_usd,
        side="buy",
        quantity_token=None,
        dry_run=False,
        quote=quote,
        input_price_usd=sol_price,
        token_decimals=token_decimals,
    )

    assert result.success is True
    # Expected: $0.50 / $2.50 = 0.2 tokens
    expected_qty = notional_usd / token_price
    assert result.quantity_token == pytest.approx(expected_qty, rel=0.01)


@pytest.mark.asyncio
async def test_sell_6_decimal_token_passes_correct_decimals() -> None:
    """sell_token should pass the actual token_decimals, not hardcoded 9."""
    trader = Mock6DecimalTraderClient()
    service = TraderExecutionService(
        mcp_manager=MockMCPManager(trader),
        chain="solana",
        max_slippage_bps=100,
    )
    sol_price = 200.0
    quantity = 0.2
    token_decimals = 6

    quote = await service.get_quote(
        token_address="WifMint111111111111111111111111111111111111",
        notional_usd=0.50,
        side="sell",
        input_price_usd=sol_price,
        token_decimals=token_decimals,
    )
    result = await service.execute_trade(
        token_address="WifMint111111111111111111111111111111111111",
        notional_usd=0.50,
        side="sell",
        quantity_token=quantity,
        dry_run=False,
        quote=quote,
        input_price_usd=sol_price,
        token_decimals=token_decimals,
    )

    assert result.success is True
    # Verify token_decimals=6 was passed, not 9
    sell_call = [(m, a) for m, a in trader.calls if m == "sell_token"][0]
    assert sell_call[1]["token_decimals"] == 6
    assert sell_call[1]["token_amount"] == pytest.approx(quantity)


@pytest.mark.asyncio
async def test_extract_price_uses_correct_decimals_for_buy() -> None:
    """_extract_price should use token_decimals to convert tokenReceived correctly."""
    extract = TraderExecutionService._extract_price
    # 6-decimal token: 0.0025 SOL buys 0.2 tokens → tokenReceived = 200_000 (raw)
    payload = {"solSpent": 0.0025, "tokenReceived": "200000"}
    price = extract(payload, side="buy", native_price_usd=200.0, token_decimals=6)
    # 0.0025 SOL * $200 = $0.50 spent, 200_000 / 10^6 = 0.2 tokens
    # price = $0.50 / 0.2 = $2.50
    assert price == pytest.approx(2.50)

    # Same payload with wrong decimals (9) gives wrong price
    wrong_price = extract(payload, side="buy", native_price_usd=200.0, token_decimals=9)
    # 200_000 / 10^9 = 0.0002 tokens → price = $0.50 / 0.0002 = $2500 (wrong!)
    assert wrong_price == pytest.approx(2500.0)
    assert wrong_price != pytest.approx(2.50)


def test_decimals_cache_returns_known_values() -> None:
    """Pre-seeded cache entries should return immediately."""
    # SOL native mint
    assert _decimals_cache.get("So11111111111111111111111111111111111111112") == 9
    # USDC
    assert _decimals_cache.get("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v") == 6


@pytest.mark.asyncio
async def test_buy_quote_passes_native_input_decimals_not_token() -> None:
    """get_quote for a buy should pass input_decimals=9 (SOL), not the token's decimals."""
    trader = Mock6DecimalTraderClient()
    service = TraderExecutionService(
        mcp_manager=MockMCPManager(trader),
        chain="solana",
        max_slippage_bps=100,
    )
    await service.get_quote(
        token_address="WifMint111111111111111111111111111111111111",
        notional_usd=0.50,
        side="buy",
        input_price_usd=200.0,
        token_decimals=6,
    )
    quote_call = [(m, a) for m, a in trader.calls if m == "get_quote"][0]
    # input_decimals should be 9 (SOL) on buy side, not 6 (token)
    assert quote_call[1]["input_decimals"] == 9
    # input_mint should be SOL native mint to match buy_token execution path
    assert quote_call[1]["input_mint"] == "So11111111111111111111111111111111111111112"


@pytest.mark.asyncio
async def test_sell_quote_passes_token_input_decimals() -> None:
    """get_quote for a sell should pass input_decimals matching the token's decimals."""
    trader = Mock6DecimalTraderClient()
    service = TraderExecutionService(
        mcp_manager=MockMCPManager(trader),
        chain="solana",
        max_slippage_bps=100,
    )
    await service.get_quote(
        token_address="WifMint111111111111111111111111111111111111",
        notional_usd=0.50,
        side="sell",
        input_price_usd=200.0,
        token_decimals=6,
    )
    quote_call = [(m, a) for m, a in trader.calls if m == "get_quote"][0]
    # input_decimals should be 6 (token) on sell side
    assert quote_call[1]["input_decimals"] == 6


@pytest.mark.asyncio
async def test_get_wallet_token_balance() -> None:
    """get_wallet_token_balance returns uiAmount from get_balance response."""
    trader = MockRealTraderClient()
    service = TraderExecutionService(
        mcp_manager=MockMCPManager(trader),
        chain="solana",
        max_slippage_bps=100,
    )
    balance = await service.get_wallet_token_balance("SomeMint111111111111111111111111111111111111")
    assert balance == pytest.approx(19800.0)
    assert any(m == "get_balance" for m, _ in trader.calls)


@pytest.mark.asyncio
async def test_get_wallet_token_balance_no_tool() -> None:
    """Returns None when trader MCP has no get_balance tool."""
    trader = MockTraderClient()  # simple mock without get_balance
    service = TraderExecutionService(
        mcp_manager=MockMCPManager(trader),
        chain="solana",
        max_slippage_bps=100,
    )
    balance = await service.get_wallet_token_balance("SomeMint111111111111111111111111111111111111")
    assert balance is None


@pytest.mark.asyncio
async def test_sell_token_gets_token_address_not_sol() -> None:
    """sell_token must receive actual token address, not SOL native mint."""
    from app.lag_execution import SOL_NATIVE_MINT

    trader = MockRealTraderClient()
    service = TraderExecutionService(
        mcp_manager=MockMCPManager(trader),
        chain="solana",
        max_slippage_bps=100,
    )
    token = "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"
    execution = await service.execute_trade(
        token_address=token,
        notional_usd=0.50,
        side="sell",
        quantity_token=3.1777,
        dry_run=False,
        quote=None,
        input_price_usd=87.0,
        token_decimals=6,
    )
    sell_call = [(m, a) for m, a in trader.calls if m == "sell_token"][0]
    assert sell_call[1]["token_address"] == token
    assert sell_call[1]["token_address"] != SOL_NATIVE_MINT
