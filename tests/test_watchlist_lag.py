"""Tests for lag strategy persistence methods in WatchlistDB."""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio

from app.watchlist import WatchlistDB


@pytest.fixture
def temp_db_path():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir) / "test_watchlist_lag.db"


@pytest_asyncio.fixture
async def db(temp_db_path):
    database = WatchlistDB(db_path=temp_db_path)
    await database.connect()
    yield database
    await database.close()


@pytest.mark.asyncio
async def test_record_lag_snapshot_and_event(db):
    snapshot = await db.record_lag_snapshot(
        token_address="So11111111111111111111111111111111111111111",
        symbol="TEST",
        chain="solana",
        reference_price=1.23,
        executable_price=1.20,
        edge_bps=250.0,
        liquidity_usd=50000.0,
        signal_triggered=True,
    )
    event = await db.record_lag_event(
        event_type="signal_triggered",
        message="Edge threshold hit",
        token_address="So11111111111111111111111111111111111111111",
        symbol="TEST",
        chain="solana",
        data={"edge_bps": 250.0},
    )

    assert snapshot.id > 0
    assert snapshot.signal_triggered is True
    assert event.id > 0
    assert event.event_type == "signal_triggered"

    events = await db.list_recent_lag_events(limit=5)
    assert len(events) == 1
    assert events[0].message == "Edge threshold hit"


@pytest.mark.asyncio
async def test_add_and_close_lag_position_with_daily_pnl(db):
    position = await db.add_lag_position(
        token_address="So11111111111111111111111111111111111111111",
        symbol="TEST",
        chain="solana",
        entry_price=1.0,
        quantity_token=10.0,
        notional_usd=10.0,
        stop_price=0.9,
        take_price=1.1,
        dry_run=True,
    )
    open_positions = await db.list_open_lag_positions(chain="solana")
    assert len(open_positions) == 1
    assert open_positions[0].id == position.id

    closed = await db.close_lag_position(
        position_id=position.id,
        exit_price=1.2,
        close_reason="take_profit",
        realized_pnl_usd=2.0,
        closed_at=datetime.now(timezone.utc),
    )
    assert closed is True
    assert await db.count_open_lag_positions(chain="solana") == 0

    daily_pnl = await db.get_daily_lag_realized_pnl()
    assert daily_pnl == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_lag_position_opened_at_is_timezone_aware(db):
    """Positions read from DB must have timezone-aware opened_at."""
    position = await db.add_lag_position(
        token_address="So11111111111111111111111111111111111111111",
        symbol="SOL",
        chain="solana",
        entry_price=150.0,
        quantity_token=1.0,
        notional_usd=25.0,
        stop_price=148.8,
        take_price=152.25,
        dry_run=True,
    )
    assert position.opened_at.tzinfo is not None

    positions = await db.list_open_lag_positions(chain="solana")
    assert positions[0].opened_at.tzinfo is not None

    now = datetime.now(timezone.utc)
    age = (now - positions[0].opened_at).total_seconds()
    assert age >= 0
