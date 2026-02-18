"""Tests for TraderExecutionService.probe_slippage()."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

import pytest

from app.execution import AtomicTradeExecution, TradeQuote, TraderExecutionService


# ---------------------------------------------------------------------------
# Minimal mock MCP manager
# ---------------------------------------------------------------------------


class _MockTraderClient:
    def __init__(self, price: float = 0.01) -> None:
        self.price = price
        self.tools: List[Dict[str, Any]] = [
            {
                "name": "getQuote",
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

    async def call_tool(self, method: str, arguments: Dict[str, Any]) -> Any:
        if method == "getQuote":
            return {"priceUsd": str(self.price), "liquidityUsd": 100_000}
        raise ValueError(f"Unexpected method: {method}")


class _MockMCPManager:
    def __init__(self, trader: _MockTraderClient) -> None:
        self._trader = trader

    def get_client(self, name: str) -> Any:
        if name == "trader":
            return self._trader
        return None


def _make_service(price: float = 0.01) -> TraderExecutionService:
    trader = _MockTraderClient(price=price)
    manager = _MockMCPManager(trader=trader)
    return TraderExecutionService(
        mcp_manager=manager,
        chain="solana",
        max_slippage_bps=300,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestProbeSlippage:
    """Unit tests for TraderExecutionService.probe_slippage()."""

    @pytest.mark.asyncio
    async def test_acceptable_slippage_returns_no_abort(self):
        """Probe succeeds with slippage within threshold — should_abort is False."""
        svc = _make_service(price=0.01)
        quoted_price = 0.01
        # actual entry matches quoted exactly → 0% deviation
        actual_entry = quoted_price

        with patch.object(svc, "get_quote", new_callable=AsyncMock) as mock_quote, \
             patch.object(
                svc, "execute_atomic_trade", new_callable=AsyncMock
            ) as mock_atomic:
            from app.execution import TradeQuote
            mock_quote.return_value = TradeQuote(price=quoted_price, method="mock", raw={})
            mock_atomic.return_value = AtomicTradeExecution(
                success=True, entry_price=actual_entry
            )
            should_abort, slippage_pct, reason = await svc.probe_slippage(
                token_address="TokenAAA",
                probe_usd=0.50,
                input_price_usd=180.0,
                max_slippage_pct=5.0,
            )

        assert should_abort is False
        assert slippage_pct == pytest.approx(0.0, abs=1e-9)
        assert reason is None

    @pytest.mark.asyncio
    async def test_excessive_slippage_returns_abort(self):
        """Probe actual price deviates >threshold — should_abort is True."""
        svc = _make_service(price=0.01)
        quoted_price = 0.01
        # 10% worse entry price
        actual_entry = quoted_price * 1.10

        with patch.object(svc, "get_quote", new_callable=AsyncMock) as mock_quote, \
             patch.object(
                svc, "execute_atomic_trade", new_callable=AsyncMock
            ) as mock_atomic:
            from app.execution import TradeQuote
            mock_quote.return_value = TradeQuote(price=quoted_price, method="mock", raw={})
            mock_atomic.return_value = AtomicTradeExecution(
                success=True, entry_price=actual_entry
            )
            should_abort, slippage_pct, reason = await svc.probe_slippage(
                token_address="TokenAAA",
                probe_usd=0.50,
                input_price_usd=180.0,
                max_slippage_pct=5.0,
            )

        assert should_abort is True
        assert slippage_pct == pytest.approx(10.0, rel=1e-3)
        assert reason is not None
        assert "10.0%" in reason

    @pytest.mark.asyncio
    async def test_atomic_trade_failure_degrades_gracefully(self):
        """If buy_and_sell fails, probe returns should_abort=False (don't block trade)."""
        svc = _make_service(price=0.01)

        with patch.object(svc, "get_quote", new_callable=AsyncMock) as mock_quote, \
             patch.object(
                svc, "execute_atomic_trade", new_callable=AsyncMock
            ) as mock_atomic:
            from app.execution import TradeQuote
            mock_quote.return_value = TradeQuote(price=0.01, method="mock", raw={})
            mock_atomic.return_value = AtomicTradeExecution(
                success=False, error="buy_and_sell not available"
            )
            should_abort, slippage_pct, reason = await svc.probe_slippage(
                token_address="TokenAAA",
                probe_usd=0.50,
                input_price_usd=180.0,
                max_slippage_pct=5.0,
            )

        assert should_abort is False
        assert slippage_pct is None

    @pytest.mark.asyncio
    async def test_quote_failure_degrades_gracefully(self):
        """If get_quote raises, probe returns should_abort=False."""
        svc = _make_service(price=0.01)

        with patch.object(svc, "get_quote", new_callable=AsyncMock) as mock_quote:
            mock_quote.side_effect = RuntimeError("quote failed")
            should_abort, slippage_pct, reason = await svc.probe_slippage(
                token_address="TokenAAA",
                probe_usd=0.50,
                input_price_usd=180.0,
                max_slippage_pct=5.0,
            )

        assert should_abort is False
        assert slippage_pct is None

    @pytest.mark.asyncio
    async def test_slippage_below_threshold_is_allowed(self):
        """Slippage just below the threshold is not aborted."""
        svc = _make_service(price=0.01)
        quoted_price = 0.01
        actual_entry = quoted_price * 1.049  # ~4.9%, below 5% threshold

        with patch.object(svc, "get_quote", new_callable=AsyncMock) as mock_quote, \
             patch.object(
                svc, "execute_atomic_trade", new_callable=AsyncMock
            ) as mock_atomic:
            from app.execution import TradeQuote
            mock_quote.return_value = TradeQuote(price=quoted_price, method="mock", raw={})
            mock_atomic.return_value = AtomicTradeExecution(
                success=True, entry_price=actual_entry
            )
            should_abort, slippage_pct, reason = await svc.probe_slippage(
                token_address="TokenAAA",
                probe_usd=0.50,
                input_price_usd=180.0,
                max_slippage_pct=5.0,
            )

        assert should_abort is False
        assert slippage_pct is not None
        assert slippage_pct < 5.0
