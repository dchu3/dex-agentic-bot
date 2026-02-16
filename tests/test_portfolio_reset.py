"""Tests for /portfolio reset command and delete_closed_portfolio_data()."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from app.database import Database


@pytest.fixture
def temp_db_path():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir) / "test_reset.db"


@pytest_asyncio.fixture
async def db(temp_db_path):
    database = Database(db_path=temp_db_path)
    await database.connect()
    yield database
    await database.close()


async def _add_position(db, symbol="TEST", status="open", chain="solana"):
    """Helper to add a position and optionally close it."""
    pos = await db.add_portfolio_position(
        token_address=f"0x{symbol.lower()}",
        symbol=symbol,
        chain=chain,
        entry_price=1.0,
        quantity_token=100.0,
        notional_usd=100.0,
        stop_price=0.9,
        take_price=1.15,
        dry_run=True,
    )
    if status == "closed":
        await db.close_portfolio_position(
            position_id=pos.id,
            exit_price=1.1,
            close_reason="test",
            realized_pnl_usd=10.0,
        )
        await db.record_portfolio_execution(
            position_id=pos.id,
            token_address=pos.token_address,
            symbol=symbol,
            chain=chain,
            action="sell",
            requested_notional_usd=100.0,
            executed_price=1.1,
            quantity_token=100.0,
            tx_hash="0xabc",
            success=True,
        )
    return pos


class TestDeleteClosedPortfolioData:
    """Tests for Database.delete_closed_portfolio_data()."""

    @pytest.mark.asyncio
    async def test_deletes_closed_positions(self, db):
        await _add_position(db, symbol="AAA", status="closed")
        await _add_position(db, symbol="BBB", status="closed")

        deleted = await db.delete_closed_portfolio_data()

        assert deleted == 2
        closed = await db.list_closed_portfolio_positions(limit=100)
        assert len(closed) == 0

    @pytest.mark.asyncio
    async def test_preserves_open_positions(self, db):
        await _add_position(db, symbol="OPEN", status="open")
        await _add_position(db, symbol="CLOSED", status="closed")

        deleted = await db.delete_closed_portfolio_data()

        assert deleted == 1
        open_positions = await db.list_open_portfolio_positions()
        assert len(open_positions) == 1
        assert open_positions[0].symbol == "OPEN"

    @pytest.mark.asyncio
    async def test_deletes_associated_executions(self, db):
        pos = await _add_position(db, symbol="EXE", status="closed")

        await db.delete_closed_portfolio_data()

        conn = await db._ensure_connected()
        cursor = await conn.execute(
            "SELECT COUNT(*) AS cnt FROM portfolio_executions WHERE position_id = ?",
            (pos.id,),
        )
        row = await cursor.fetchone()
        assert row["cnt"] == 0

    @pytest.mark.asyncio
    async def test_returns_zero_when_none_closed(self, db):
        await _add_position(db, symbol="STILL_OPEN", status="open")

        deleted = await db.delete_closed_portfolio_data()

        assert deleted == 0

    @pytest.mark.asyncio
    async def test_resets_daily_pnl(self, db):
        await _add_position(db, symbol="PNL", status="closed")
        pnl_before = await db.get_daily_portfolio_pnl()
        assert pnl_before != 0.0

        await db.delete_closed_portfolio_data()

        pnl_after = await db.get_daily_portfolio_pnl()
        assert pnl_after == 0.0


class TestPortfolioResetCommand:
    """Tests for the /portfolio reset CLI command routing."""

    @pytest.mark.asyncio
    async def test_reset_confirmed(self, db):
        from app.cli import _cmd_portfolio

        await _add_position(db, symbol="DEL", status="closed")

        output = AsyncMock()
        output.info = lambda msg: None
        output.warning = lambda msg: None

        with patch("app.cli.input", return_value="yes"):
            await _cmd_portfolio(["reset"], output, db, None)

        closed = await db.list_closed_portfolio_positions(limit=100)
        assert len(closed) == 0

    @pytest.mark.asyncio
    async def test_reset_cancelled(self, db):
        from app.cli import _cmd_portfolio

        await _add_position(db, symbol="KEEP", status="closed")

        output = AsyncMock()
        output.info = lambda msg: None
        output.warning = lambda msg: None

        with patch("app.cli.input", return_value="no"):
            await _cmd_portfolio(["reset"], output, db, None)

        closed = await db.list_closed_portfolio_positions(limit=100)
        assert len(closed) == 1

    @pytest.mark.asyncio
    async def test_reset_no_closed_positions(self, db):
        from app.cli import _cmd_portfolio

        messages = []
        output = AsyncMock()
        output.info = lambda msg: messages.append(msg)
        output.warning = lambda msg: messages.append(msg)

        await _cmd_portfolio(["reset"], output, db, None)

        assert any("No closed" in m for m in messages)
