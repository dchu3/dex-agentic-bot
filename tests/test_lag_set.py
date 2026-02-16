"""Tests for /lag set CLI command."""

from __future__ import annotations

import pytest

from app.lag_strategy import LagStrategyConfig


class MockCLIOutput:
    """Captures CLI output calls for assertions."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def info(self, msg: str) -> None:
        self.messages.append(("info", msg))

    def warning(self, msg: str) -> None:
        self.messages.append(("warning", msg))

    def status(self, msg: str) -> None:
        self.messages.append(("status", msg))

    def last_message(self) -> str:
        return self.messages[-1][1] if self.messages else ""

    def all_text(self) -> str:
        return "\n".join(m[1] for m in self.messages)


def _make_config(**overrides) -> LagStrategyConfig:
    defaults = dict(
        enabled=True,
        dry_run=True,
        interval_seconds=30,
        chain="solana",
        sample_notional_usd=25.0,
        min_edge_bps=10.0,
        min_liquidity_usd=1000.0,
        max_slippage_bps=100,
        max_position_usd=25.0,
        max_open_positions=2,
        cooldown_seconds=180,
        take_profit_bps=50.0,
        stop_loss_bps=30.0,
        max_hold_seconds=600,
        daily_loss_limit_usd=50.0,
    )
    defaults.update(overrides)
    return LagStrategyConfig(**defaults)


class MockScheduler:
    """Minimal scheduler mock with a real LagStrategyConfig."""

    def __init__(self, config: LagStrategyConfig | None = None) -> None:
        self.engine = type("Engine", (), {"config": config or _make_config()})()


# ── successful set ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_max_position_usd() -> None:
    from app.cli import _cmd_lag

    scheduler = MockScheduler()
    output = MockCLIOutput()
    await _cmd_lag(["set", "max_position_usd", "50"], output, None, scheduler)
    assert scheduler.engine.config.max_position_usd == 50.0
    assert "25.00" in output.last_message()
    assert "50.00" in output.last_message()
    assert "✅" in output.last_message()


@pytest.mark.asyncio
async def test_set_max_open_positions() -> None:
    from app.cli import _cmd_lag

    scheduler = MockScheduler()
    output = MockCLIOutput()
    await _cmd_lag(["set", "max_open_positions", "5"], output, None, scheduler)
    assert scheduler.engine.config.max_open_positions == 5
    assert "2 →" in output.last_message()
    assert "5" in output.last_message()


@pytest.mark.asyncio
async def test_set_min_edge_bps() -> None:
    from app.cli import _cmd_lag

    scheduler = MockScheduler()
    output = MockCLIOutput()
    await _cmd_lag(["set", "min_edge_bps", "15.5"], output, None, scheduler)
    assert scheduler.engine.config.min_edge_bps == 15.5


@pytest.mark.asyncio
async def test_set_cooldown_seconds() -> None:
    from app.cli import _cmd_lag

    scheduler = MockScheduler()
    output = MockCLIOutput()
    await _cmd_lag(["set", "cooldown_seconds", "60"], output, None, scheduler)
    assert scheduler.engine.config.cooldown_seconds == 60


# ── no-args shows current values ─────────────────────────────────────


@pytest.mark.asyncio
async def test_set_no_args_shows_params() -> None:
    from app.cli import _cmd_lag

    scheduler = MockScheduler()
    output = MockCLIOutput()
    await _cmd_lag(["set"], output, None, scheduler)
    text = output.all_text()
    assert "max_position_usd" in text
    assert "max_open_positions" in text
    assert "Tunable" in text


# ── validation: invalid param name ────────────────────────────────────


@pytest.mark.asyncio
async def test_set_unknown_param() -> None:
    from app.cli import _cmd_lag

    scheduler = MockScheduler()
    output = MockCLIOutput()
    await _cmd_lag(["set", "nonexistent", "10"], output, None, scheduler)
    assert output.messages[-1][0] == "warning"
    assert "Unknown parameter" in output.last_message()


# ── validation: invalid value type ────────────────────────────────────


@pytest.mark.asyncio
async def test_set_invalid_type() -> None:
    from app.cli import _cmd_lag

    scheduler = MockScheduler()
    output = MockCLIOutput()
    await _cmd_lag(["set", "max_position_usd", "abc"], output, None, scheduler)
    assert output.messages[-1][0] == "warning"
    assert "Invalid value" in output.last_message()


@pytest.mark.asyncio
async def test_set_int_param_rejects_float() -> None:
    from app.cli import _cmd_lag

    scheduler = MockScheduler()
    output = MockCLIOutput()
    await _cmd_lag(["set", "max_open_positions", "2.5"], output, None, scheduler)
    assert output.messages[-1][0] == "warning"
    assert "Invalid value" in output.last_message()


# ── validation: out of bounds ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_below_minimum() -> None:
    from app.cli import _cmd_lag

    scheduler = MockScheduler()
    output = MockCLIOutput()
    await _cmd_lag(["set", "max_position_usd", "0"], output, None, scheduler)
    assert output.messages[-1][0] == "warning"
    assert "below minimum" in output.last_message()


@pytest.mark.asyncio
async def test_set_above_maximum() -> None:
    from app.cli import _cmd_lag

    scheduler = MockScheduler()
    output = MockCLIOutput()
    await _cmd_lag(["set", "max_open_positions", "25"], output, None, scheduler)
    assert output.messages[-1][0] == "warning"
    assert "above maximum" in output.last_message()


# ── guard: scheduler not enabled ──────────────────────────────────────


@pytest.mark.asyncio
async def test_set_no_scheduler() -> None:
    from app.cli import _cmd_lag

    output = MockCLIOutput()
    await _cmd_lag(["set", "max_position_usd", "50"], output, None, None)
    assert output.messages[-1][0] == "warning"
    assert "not enabled" in output.last_message()


# ── missing value arg ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_missing_value() -> None:
    from app.cli import _cmd_lag

    scheduler = MockScheduler()
    output = MockCLIOutput()
    await _cmd_lag(["set", "max_position_usd"], output, None, scheduler)
    assert output.messages[-1][0] == "warning"
    assert "Usage" in output.last_message()
