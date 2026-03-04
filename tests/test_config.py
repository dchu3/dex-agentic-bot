"""Tests for configuration validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_insider_thresholds_allow_valid_ordering() -> None:
    Settings(
        GEMINI_API_KEY="x",
        PORTFOLIO_INSIDER_MAX_CONCENTRATION_PCT=50.0,
        PORTFOLIO_INSIDER_WARN_CONCENTRATION_PCT=30.0,
        PORTFOLIO_INSIDER_MAX_CREATOR_PCT=30.0,
        PORTFOLIO_INSIDER_WARN_CREATOR_PCT=10.0,
        _env_file=None,
    )


def test_decision_log_default_disabled() -> None:
    settings = Settings(GEMINI_API_KEY="x", _env_file=None)
    assert settings.portfolio_decision_log_enabled is False


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "PORTFOLIO_INSIDER_MAX_CONCENTRATION_PCT": 50.0,
            "PORTFOLIO_INSIDER_WARN_CONCENTRATION_PCT": 50.0,
        },
        {
            "PORTFOLIO_INSIDER_MAX_CREATOR_PCT": 30.0,
            "PORTFOLIO_INSIDER_WARN_CREATOR_PCT": 30.0,
        },
    ],
)
def test_insider_thresholds_require_warn_below_max(overrides: dict[str, float]) -> None:
    with pytest.raises(ValidationError):
        Settings(GEMINI_API_KEY="x", _env_file=None, **overrides)


def test_blue_chip_defaults() -> None:
    """Regression test: ensure optimised blue-chip defaults stay pinned."""
    s = Settings(GEMINI_API_KEY="x", _env_file=None)
    assert s.gemini_model == "gemini-3-flash-preview"
    assert s.portfolio_min_liquidity_usd == 245_000.0
    assert s.portfolio_min_volume_usd == 380_000.0
    assert s.portfolio_min_market_cap_usd == 1_650_000.0
    assert s.portfolio_min_token_age_hours == 11.0
    assert s.portfolio_max_token_age_hours == 0.0
    assert s.portfolio_min_momentum_score == 54.0
    assert s.portfolio_discovery_interval_mins == 20
    assert s.portfolio_price_check_seconds == 40
    assert s.portfolio_stop_loss_pct == 17.0
    assert s.portfolio_trailing_stop_pct == 11.0
    assert s.portfolio_sell_pct == 45.0
    assert s.portfolio_take_profit_pct == 0.0
    assert s.portfolio_position_size_usd == 5.0
    assert s.portfolio_max_positions == 5
    assert s.portfolio_max_slippage_bps == 300
