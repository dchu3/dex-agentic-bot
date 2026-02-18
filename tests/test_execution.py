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


# ---------------------------------------------------------------------------
# verify_transaction_success tests
# ---------------------------------------------------------------------------


class TestVerifyTransactionSuccess:
    """Unit tests for verify_transaction_success()."""

    @pytest.mark.asyncio
    async def test_returns_true_when_tx_confirmed(self):
        """Returns True when transaction confirmed with no error."""
        from unittest.mock import AsyncMock, MagicMock, patch
        from app.execution import verify_transaction_success

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"result": {"meta": {"err": None}}}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await verify_transaction_success("tx123", retries=0)

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_tx_has_error(self):
        """Returns False when meta.err is set (tx failed on-chain)."""
        from unittest.mock import AsyncMock, MagicMock, patch
        from app.execution import verify_transaction_success

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"result": {"meta": {"err": {"InstructionError": [0, "SlippageToleranceExceeded"]}}}}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await verify_transaction_success("tx123", retries=0)

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_none_when_tx_not_found(self):
        """Returns None when RPC returns null result (tx not yet indexed)."""
        from unittest.mock import AsyncMock, MagicMock, patch
        from app.execution import verify_transaction_success

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"result": None}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await verify_transaction_success("tx123", retries=0)

        assert result is None

    @pytest.mark.asyncio
    async def test_retries_on_rpc_error_then_succeeds(self):
        """Retries after RPC failure and returns True on subsequent success."""
        from unittest.mock import AsyncMock, MagicMock, call, patch
        from app.execution import verify_transaction_success

        success_response = MagicMock()
        success_response.raise_for_status = MagicMock()
        success_response.json.return_value = {"result": {"meta": {"err": None}}}

        call_count = 0

        async def _post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("RPC timeout")
            return success_response

        with patch("httpx.AsyncClient") as mock_client_cls, \
             patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = _post
            mock_client_cls.return_value = mock_client

            result = await verify_transaction_success("tx123", retries=2, retry_delay_seconds=0.0)

        assert result is True
        assert call_count == 2
        mock_sleep.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_none_after_all_retries_exhausted(self):
        """Returns None (not raises) when all retry attempts fail."""
        from unittest.mock import AsyncMock, patch
        from app.execution import verify_transaction_success

        with patch("httpx.AsyncClient") as mock_client_cls, \
             patch("asyncio.sleep", new_callable=AsyncMock):
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(side_effect=ConnectionError("RPC down"))
            mock_client_cls.return_value = mock_client

            result = await verify_transaction_success("tx123", retries=2, retry_delay_seconds=0.0)

        assert result is None
