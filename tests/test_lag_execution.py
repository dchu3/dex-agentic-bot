"""Tests for lag strategy trader execution helpers."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import pytest

from app.lag_execution import TraderExecutionService


def test_extract_price_alternative_keys() -> None:
    """_extract_price finds prices under alternative key names."""
    extract = TraderExecutionService._extract_price
    assert extract({"estimatedPrice": "1.5"}, side="buy") == pytest.approx(1.5)
    assert extract({"quotePrice": 0.003}, side="buy") == pytest.approx(0.003)
    assert extract({"swap_price": "42.1"}, side="sell") == pytest.approx(42.1)
    assert extract({"expected_price": 100}, side="buy") == pytest.approx(100.0)
    assert extract({}, side="buy") is None
    assert extract({"unrelated": "data"}, side="buy") is None


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
    """Mock matching the actual dex-trader-mcp tool schemas (sol_amount, token_amount)."""

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

    async def call_tool(self, method: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((method, arguments))
        if method == "get_quote":
            return {"priceUsd": "0.000025", "liquidityUsd": 100000}
        if method == "buy_token":
            return {"success": True, "txHash": "buy-tx", "executedPrice": "0.000025", "quantity": "1000000"}
        if method == "sell_token":
            return {"success": True, "txHash": "sell-tx", "executedPrice": "0.000026", "quantity": "1000000"}
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
    sol_price = 180.0
    notional_usd = 0.50

    quote = await service.get_quote(
        token_address="BonkMint111111111111111111111111111111111111",
        notional_usd=notional_usd,
        side="buy",
        input_price_usd=sol_price,
    )
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

    # Verify the buy_token call received SOL amount, not USD
    buy_call = [c for c in trader.calls if c[0] == "buy_token"][0]
    sol_amount = buy_call[1]["sol_amount"]
    expected_sol = notional_usd / sol_price  # 0.50 / 180 ≈ 0.00278
    assert sol_amount == pytest.approx(expected_sol, rel=1e-6)
    # Must NOT be the raw USD value
    assert sol_amount != pytest.approx(notional_usd)


@pytest.mark.asyncio
async def test_sell_passes_quantity_not_usd() -> None:
    """sell_token with token_amount param uses quantity_token on sell side."""
    trader = MockRealTraderClient()
    service = TraderExecutionService(
        mcp_manager=MockMCPManager(trader),
        chain="solana",
        max_slippage_bps=100,
    )
    token_price = 0.000025
    quantity = 1000000.0

    quote = await service.get_quote(
        token_address="BonkMint111111111111111111111111111111111111",
        notional_usd=25,
        side="sell",
        input_price_usd=token_price,
    )
    result = await service.execute_trade(
        token_address="BonkMint111111111111111111111111111111111111",
        notional_usd=25,
        side="sell",
        quantity_token=quantity,
        dry_run=False,
        quote=quote,
        input_price_usd=token_price,
    )

    assert result.success is True
    assert result.method == "sell_token"
    sell_call = [c for c in trader.calls if c[0] == "sell_token"][0]
    assert sell_call[1]["token_amount"] == pytest.approx(quantity)
    assert sell_call[1]["token_decimals"] == 9


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
