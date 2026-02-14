"""Tests for /lag close CLI command."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
import pytest_asyncio

from app.watchlist import WatchlistDB


class MockCLIOutput:
    """Captures CLI output calls for assertions."""

    def __init__(self) -> None:
        self.messages: List[tuple[str, str]] = []

    def info(self, msg: str) -> None:
        self.messages.append(("info", msg))

    def warning(self, msg: str) -> None:
        self.messages.append(("warning", msg))

    def status(self, msg: str) -> None:
        self.messages.append(("status", msg))

    def last_message(self) -> str:
        return self.messages[-1][1] if self.messages else ""


class MockScheduler:
    """Minimal scheduler mock exposing engine.config.chain."""

    class _Engine:
        class _Config:
            chain = "solana"
        config = _Config()

    engine = _Engine()


@pytest.fixture
def temp_db_path():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir) / "test_lag_close.db"


@pytest_asyncio.fixture
async def db(temp_db_path):
    database = WatchlistDB(db_path=temp_db_path)
    await database.connect()
    yield database
    await database.close()


async def _add_open_position(db: WatchlistDB, symbol: str = "TEST", token_address: str = "Token1111") -> Any:
    """Helper to add an open lag position and return it."""
    return await db.add_lag_position(
        token_address=token_address,
        symbol=symbol,
        chain="solana",
        entry_price=1.0,
        quantity_token=100.0,
        notional_usd=25.0,
        stop_price=0.90,
        take_price=1.10,
        dry_run=True,
    )


@pytest.mark.asyncio
async def test_lag_close_single_position(db) -> None:
    """Close a single position by ID."""
    from app.cli import _cmd_lag

    pos = await _add_open_position(db, symbol="BONK", token_address="BonkMint111")
    output = MockCLIOutput()

    await _cmd_lag(["close", str(pos.id)], output, db, MockScheduler())

    assert any("Closed position" in m for _, m in output.messages)
    assert any("BONK" in m for _, m in output.messages)

    remaining = await db.list_open_lag_positions(chain="solana")
    assert len(remaining) == 0


@pytest.mark.asyncio
async def test_lag_close_all_positions(db) -> None:
    """Close all open positions."""
    from app.cli import _cmd_lag

    await _add_open_position(db, symbol="BONK", token_address="BonkMint111")
    await _add_open_position(db, symbol="WIF", token_address="WifMint2222")
    output = MockCLIOutput()

    await _cmd_lag(["close", "all"], output, db, MockScheduler())

    assert any("2/2" in m for _, m in output.messages)

    remaining = await db.list_open_lag_positions(chain="solana")
    assert len(remaining) == 0


@pytest.mark.asyncio
async def test_lag_close_no_open_positions(db) -> None:
    """Graceful message when no positions are open."""
    from app.cli import _cmd_lag

    output = MockCLIOutput()
    await _cmd_lag(["close", "all"], output, db, MockScheduler())
    assert any("No open" in m for _, m in output.messages)


@pytest.mark.asyncio
async def test_lag_close_invalid_id(db) -> None:
    """Invalid (non-numeric) ID shows warning."""
    from app.cli import _cmd_lag

    output = MockCLIOutput()
    await _cmd_lag(["close", "abc"], output, db, MockScheduler())
    assert any("Invalid position ID" in m for _, m in output.messages)


@pytest.mark.asyncio
async def test_lag_close_nonexistent_id(db) -> None:
    """Nonexistent position ID shows warning."""
    from app.cli import _cmd_lag

    output = MockCLIOutput()
    await _cmd_lag(["close", "999"], output, db, MockScheduler())
    assert any("No open position" in m for _, m in output.messages)


@pytest.mark.asyncio
async def test_lag_close_missing_argument(db) -> None:
    """Missing argument shows usage."""
    from app.cli import _cmd_lag

    output = MockCLIOutput()
    await _cmd_lag(["close"], output, db, MockScheduler())
    assert any("Usage" in m for _, m in output.messages)


@pytest.mark.asyncio
async def test_lag_close_records_event(db) -> None:
    """Closing a position records a manual_close event."""
    from app.cli import _cmd_lag

    pos = await _add_open_position(db, symbol="BONK", token_address="BonkMint111")
    output = MockCLIOutput()

    await _cmd_lag(["close", str(pos.id)], output, db, MockScheduler())

    events = await db.list_recent_lag_events(limit=5)
    manual_events = [e for e in events if e.event_type == "manual_close"]
    assert len(manual_events) == 1
    assert "BONK" in manual_events[0].message
