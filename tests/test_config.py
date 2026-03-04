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
        Settings(GEMINI_API_KEY="x", **overrides)
