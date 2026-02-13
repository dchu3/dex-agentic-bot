"""Tests for lag strategy trader execution helpers."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import pytest

from app.lag_execution import TraderExecutionService


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
