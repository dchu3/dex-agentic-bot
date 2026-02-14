"""Tests for lag strategy scheduler notification formatting."""

from __future__ import annotations

from datetime import datetime, timezone

from app.lag_scheduler import LagStrategyScheduler
from app.lag_strategy import LagCycleResult
from app.watchlist import LagPosition


def _make_position(**kwargs) -> LagPosition:
    defaults = dict(
        id=1,
        token_address="TokenMintAddress",
        symbol="TEST",
        chain="solana",
        entry_price=0.001,
        quantity_token=1000.0,
        notional_usd=1.0,
        stop_price=0.0009,
        take_price=0.0011,
        opened_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    defaults.update(kwargs)
    return LagPosition(**defaults)


def _make_result(**kwargs) -> LagCycleResult:
    defaults = dict(
        timestamp=datetime(2025, 6, 1, 12, 0, tzinfo=timezone.utc),
        summary="samples=5 | signals=1 | closed=1",
    )
    defaults.update(kwargs)
    return LagCycleResult(**defaults)


class TestFormatMessage:
    """Tests for LagStrategyScheduler._format_message."""

    def _formatter(self) -> LagStrategyScheduler:
        # engine is not used by _format_message, pass None
        return LagStrategyScheduler(engine=None, interval_seconds=60)  # type: ignore[arg-type]

    def test_closed_position_includes_sell_price(self):
        pos = _make_position(
            exit_price=0.0012,
            realized_pnl_usd=0.20,
        )
        result = _make_result(positions_closed=[pos])
        msg = self._formatter()._format_message(result)
        assert "sell $0.001200" in msg
        assert "PnL $0.20" in msg

    def test_closed_position_sell_price_none_shows_na(self):
        pos = _make_position(
            exit_price=None,
            realized_pnl_usd=0.50,
        )
        result = _make_result(positions_closed=[pos])
        msg = self._formatter()._format_message(result)
        assert "sell N/A" in msg

    def test_pnl_negative_zero_displays_as_zero(self):
        pos = _make_position(
            exit_price=0.001,
            realized_pnl_usd=-0.0,
        )
        result = _make_result(positions_closed=[pos])
        msg = self._formatter()._format_message(result)
        # Must not contain "$-0.00"
        assert "$-0.00" not in msg
        assert "PnL $0.00" in msg

    def test_pnl_none_displays_as_zero(self):
        pos = _make_position(
            exit_price=0.001,
            realized_pnl_usd=None,
        )
        result = _make_result(positions_closed=[pos])
        msg = self._formatter()._format_message(result)
        assert "PnL $0.00" in msg

    def test_closed_position_negative_pnl(self):
        pos = _make_position(
            exit_price=0.0008,
            realized_pnl_usd=-0.25,
        )
        result = _make_result(positions_closed=[pos])
        msg = self._formatter()._format_message(result)
        assert "PnL $-0.25" in msg
        assert "sell $0.000800" in msg

    def test_opened_position_format_unchanged(self):
        pos = _make_position()
        result = _make_result(entries_opened=[pos])
        msg = self._formatter()._format_message(result)
        assert "🟢" in msg
        assert "entry $0.001000" in msg
        assert "qty 1000.0000" in msg
