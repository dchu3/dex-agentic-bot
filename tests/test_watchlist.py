"""Tests for watchlist database operations."""

import asyncio
import tempfile
from datetime import datetime
from pathlib import Path

import pytest
import pytest_asyncio

from app.watchlist import WatchlistDB, WatchlistEntry, AlertRecord


@pytest.fixture
def temp_db_path():
    """Create a temporary database path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir) / "test_watchlist.db"


@pytest_asyncio.fixture
async def db(temp_db_path):
    """Create and connect to a test database."""
    database = WatchlistDB(db_path=temp_db_path)
    await database.connect()
    yield database
    await database.close()


@pytest.mark.asyncio
async def test_add_entry(db):
    """Test adding a token to the watchlist."""
    entry = await db.add_entry(
        token_address="0x6982508145454Ce325dDbE47a25d4ec3d2311933",
        symbol="PEPE",
        chain="ethereum",
    )

    assert entry.id is not None
    # Address should preserve original case
    assert entry.token_address == "0x6982508145454Ce325dDbE47a25d4ec3d2311933"
    assert entry.symbol == "PEPE"
    assert entry.chain == "ethereum"
    assert entry.alert_above is None
    assert entry.alert_below is None


@pytest.mark.asyncio
async def test_add_entry_with_alerts(db):
    """Test adding a token with alert thresholds."""
    entry = await db.add_entry(
        token_address="0x6982508145454Ce325dDbE47a25d4ec3d2311933",
        symbol="PEPE",
        chain="ethereum",
        alert_above=0.00002,
        alert_below=0.00001,
    )

    assert entry.alert_above == 0.00002
    assert entry.alert_below == 0.00001


@pytest.mark.asyncio
async def test_add_duplicate_entry_updates(db):
    """Test that adding a duplicate entry updates existing one."""
    await db.add_entry(
        token_address="0x6982508145454Ce325dDbE47a25d4ec3d2311933",
        symbol="PEPE",
        chain="ethereum",
        alert_above=0.00001,
    )

    # Add again with different alert
    entry = await db.add_entry(
        token_address="0x6982508145454Ce325dDbE47a25d4ec3d2311933",
        symbol="PEPE",
        chain="ethereum",
        alert_below=0.000005,
    )

    # Should keep the original alert_above and add alert_below
    assert entry.alert_above == 0.00001
    assert entry.alert_below == 0.000005

    # Should only have one entry
    entries = await db.list_entries()
    assert len(entries) == 1


@pytest.mark.asyncio
async def test_remove_entry_by_address(db):
    """Test removing a token by address."""
    await db.add_entry(
        token_address="0x6982508145454Ce325dDbE47a25d4ec3d2311933",
        symbol="PEPE",
        chain="ethereum",
    )

    result = await db.remove_entry("0x6982508145454Ce325dDbE47a25d4ec3d2311933")
    assert result is True

    entries = await db.list_entries()
    assert len(entries) == 0


@pytest.mark.asyncio
async def test_remove_entry_by_address_and_chain(db):
    """Test removing a token by address and chain."""
    await db.add_entry(
        token_address="0x6982508145454Ce325dDbE47a25d4ec3d2311933",
        symbol="PEPE",
        chain="ethereum",
    )
    await db.add_entry(
        token_address="0x6982508145454Ce325dDbE47a25d4ec3d2311933",
        symbol="PEPE",
        chain="base",
    )

    result = await db.remove_entry(
        "0x6982508145454Ce325dDbE47a25d4ec3d2311933", chain="ethereum"
    )
    assert result is True

    entries = await db.list_entries()
    assert len(entries) == 1
    assert entries[0].chain == "base"


@pytest.mark.asyncio
async def test_remove_entry_by_symbol(db):
    """Test removing a token by symbol."""
    await db.add_entry(
        token_address="0x6982508145454Ce325dDbE47a25d4ec3d2311933",
        symbol="PEPE",
        chain="ethereum",
    )

    result = await db.remove_entry_by_symbol("PEPE")
    assert result is True

    entries = await db.list_entries()
    assert len(entries) == 0


@pytest.mark.asyncio
async def test_add_entry_with_emoji_prefix(db):
    """Test that emoji prefixes are stripped from symbols."""
    entry = await db.add_entry(
        token_address="0x1234567890abcdef1234567890abcdef12345678",
        symbol="🔥PRIME",
        chain="solana",
    )

    assert entry.symbol == "PRIME"


@pytest.mark.asyncio
async def test_remove_entry_by_symbol_with_emoji(db):
    """Test removing a token that was added with emoji prefix."""
    await db.add_entry(
        token_address="0x1234567890abcdef1234567890abcdef12345678",
        symbol="🚀ROCKET",
        chain="ethereum",
    )

    # Should find it without the emoji
    result = await db.remove_entry_by_symbol("ROCKET")
    assert result is True

    entries = await db.list_entries()
    assert len(entries) == 0


@pytest.mark.asyncio
async def test_get_entry_by_symbol_with_emoji(db):
    """Test getting a token by symbol when stored with emoji prefix."""
    await db.add_entry(
        token_address="0xabcdef1234567890abcdef1234567890abcdef12",
        symbol="✨STAR",
        chain="base",
    )

    # Should find it without the emoji
    entry = await db.get_entry(symbol="STAR")
    assert entry is not None
    assert entry.symbol == "STAR"


@pytest.mark.asyncio
async def test_remove_nonexistent_entry(db):
    """Test removing a token that doesn't exist."""
    result = await db.remove_entry("0xnonexistent")
    assert result is False


@pytest.mark.asyncio
async def test_get_entry_by_address(db):
    """Test getting a token by address."""
    await db.add_entry(
        token_address="0x6982508145454Ce325dDbE47a25d4ec3d2311933",
        symbol="PEPE",
        chain="ethereum",
    )

    entry = await db.get_entry(
        token_address="0x6982508145454Ce325dDbE47a25d4ec3d2311933"
    )
    assert entry is not None
    assert entry.symbol == "PEPE"


@pytest.mark.asyncio
async def test_get_entry_by_symbol(db):
    """Test getting a token by symbol."""
    await db.add_entry(
        token_address="0x6982508145454Ce325dDbE47a25d4ec3d2311933",
        symbol="PEPE",
        chain="ethereum",
    )

    entry = await db.get_entry(symbol="PEPE")
    assert entry is not None
    # Address should preserve original case
    assert entry.token_address == "0x6982508145454Ce325dDbE47a25d4ec3d2311933"


@pytest.mark.asyncio
async def test_get_entry_by_symbol_and_chain(db):
    """Test getting a token by symbol and chain."""
    await db.add_entry(
        token_address="0xaaa",
        symbol="PEPE",
        chain="ethereum",
    )
    await db.add_entry(
        token_address="0xbbb",
        symbol="PEPE",
        chain="base",
    )

    entry = await db.get_entry(symbol="PEPE", chain="base")
    assert entry is not None
    assert entry.token_address == "0xbbb"


@pytest.mark.asyncio
async def test_get_nonexistent_entry(db):
    """Test getting a token that doesn't exist."""
    entry = await db.get_entry(token_address="0xnonexistent")
    assert entry is None


@pytest.mark.asyncio
async def test_case_insensitive_lookup(db):
    """Test that lookups are case-insensitive but storage preserves case."""
    # Add with mixed case (like Solana addresses)
    original_address = "3QrGcwFSKXiKetD5ixzZa7H4zHPuTg8grrH7s5Mzpump"
    await db.add_entry(
        token_address=original_address,
        symbol="TEST",
        chain="solana",
    )

    # Should find with lowercase lookup
    entry = await db.get_entry(token_address=original_address.lower())
    assert entry is not None
    assert entry.token_address == original_address  # Preserved original case

    # Should find with uppercase lookup
    entry = await db.get_entry(token_address=original_address.upper())
    assert entry is not None
    assert entry.token_address == original_address  # Preserved original case

    # Remove should work with different case
    removed = await db.remove_entry(token_address=original_address.lower())
    assert removed


@pytest.mark.asyncio
async def test_list_entries(db):
    """Test listing all entries."""
    await db.add_entry(token_address="0xaaa", symbol="AAA", chain="ethereum")
    await db.add_entry(token_address="0xbbb", symbol="BBB", chain="base")
    await db.add_entry(token_address="0xccc", symbol="CCC", chain="solana")

    entries = await db.list_entries()
    assert len(entries) == 3
    symbols = {e.symbol for e in entries}
    assert symbols == {"AAA", "BBB", "CCC"}


@pytest.mark.asyncio
async def test_update_token_address(db):
    """Test updating a token address (e.g., to fix case)."""
    # Add with lowercase
    entry = await db.add_entry(
        token_address="3qrgcwfskxiketd5ixzza7h4zhputg8grrh7s5mzpump",
        symbol="TEST",
        chain="solana",
    )
    
    # Update to correct case
    correct_address = "3QrGcwFSKXiKetD5ixzZa7H4zHPuTg8grrH7s5Mzpump"
    result = await db.update_token_address(entry.id, correct_address)
    assert result is True
    
    # Verify the address was updated
    updated = await db.get_entry(symbol="TEST")
    assert updated.token_address == correct_address


@pytest.mark.asyncio
async def test_update_alert(db):
    """Test updating alert thresholds."""
    entry = await db.add_entry(
        token_address="0x6982508145454Ce325dDbE47a25d4ec3d2311933",
        symbol="PEPE",
        chain="ethereum",
    )

    result = await db.update_alert(entry.id, alert_above=0.00002)
    assert result is True

    updated = await db.get_entry(token_address="0x6982508145454Ce325dDbE47a25d4ec3d2311933")
    assert updated.alert_above == 0.00002


@pytest.mark.asyncio
async def test_update_alert_clear(db):
    """Test clearing alert thresholds."""
    entry = await db.add_entry(
        token_address="0x6982508145454Ce325dDbE47a25d4ec3d2311933",
        symbol="PEPE",
        chain="ethereum",
        alert_above=0.00002,
    )

    result = await db.update_alert(entry.id, clear_above=True)
    assert result is True

    updated = await db.get_entry(token_address="0x6982508145454Ce325dDbE47a25d4ec3d2311933")
    assert updated.alert_above is None


@pytest.mark.asyncio
async def test_update_price(db):
    """Test updating last price."""
    entry = await db.add_entry(
        token_address="0x6982508145454Ce325dDbE47a25d4ec3d2311933",
        symbol="PEPE",
        chain="ethereum",
    )

    await db.update_price(entry.id, 0.0000185)

    updated = await db.get_entry(token_address="0x6982508145454Ce325dDbE47a25d4ec3d2311933")
    assert updated.last_price == 0.0000185
    assert updated.last_checked is not None


@pytest.mark.asyncio
async def test_clear_watchlist(db):
    """Test clearing the entire watchlist."""
    await db.add_entry(token_address="0xaaa", symbol="AAA", chain="ethereum")
    await db.add_entry(token_address="0xbbb", symbol="BBB", chain="base")

    count = await db.clear_watchlist()
    assert count == 2

    entries = await db.list_entries()
    assert len(entries) == 0


# --- Alert History Tests ---


@pytest.mark.asyncio
async def test_record_alert(db):
    """Test recording a triggered alert."""
    entry = await db.add_entry(
        token_address="0x6982508145454Ce325dDbE47a25d4ec3d2311933",
        symbol="PEPE",
        chain="ethereum",
        alert_above=0.00002,
    )

    alert = await db.record_alert(
        entry_id=entry.id,
        alert_type="above",
        threshold=0.00002,
        triggered_price=0.000021,
    )

    assert alert.id is not None
    assert alert.entry_id == entry.id
    assert alert.alert_type == "above"
    assert alert.threshold == 0.00002
    assert alert.triggered_price == 0.000021
    assert alert.acknowledged is False


@pytest.mark.asyncio
async def test_get_unacknowledged_alerts(db):
    """Test getting unacknowledged alerts."""
    entry = await db.add_entry(
        token_address="0x6982508145454Ce325dDbE47a25d4ec3d2311933",
        symbol="PEPE",
        chain="ethereum",
    )

    await db.record_alert(
        entry_id=entry.id,
        alert_type="above",
        threshold=0.00002,
        triggered_price=0.000021,
    )

    alerts = await db.get_unacknowledged_alerts()
    assert len(alerts) == 1
    assert alerts[0].symbol == "PEPE"
    assert alerts[0].chain == "ethereum"


@pytest.mark.asyncio
async def test_acknowledge_alerts(db):
    """Test acknowledging alerts."""
    entry = await db.add_entry(
        token_address="0x6982508145454Ce325dDbE47a25d4ec3d2311933",
        symbol="PEPE",
        chain="ethereum",
    )

    alert = await db.record_alert(
        entry_id=entry.id,
        alert_type="above",
        threshold=0.00002,
        triggered_price=0.000021,
    )

    count = await db.acknowledge_alerts([alert.id])
    assert count == 1

    alerts = await db.get_unacknowledged_alerts()
    assert len(alerts) == 0


@pytest.mark.asyncio
async def test_acknowledge_all_alerts(db):
    """Test acknowledging all alerts."""
    entry = await db.add_entry(
        token_address="0x6982508145454Ce325dDbE47a25d4ec3d2311933",
        symbol="PEPE",
        chain="ethereum",
    )

    await db.record_alert(
        entry_id=entry.id,
        alert_type="above",
        threshold=0.00002,
        triggered_price=0.000021,
    )
    await db.record_alert(
        entry_id=entry.id,
        alert_type="below",
        threshold=0.00001,
        triggered_price=0.000009,
    )

    count = await db.acknowledge_alerts()
    assert count == 2


@pytest.mark.asyncio
async def test_get_alert_history(db):
    """Test getting alert history."""
    entry = await db.add_entry(
        token_address="0x6982508145454Ce325dDbE47a25d4ec3d2311933",
        symbol="PEPE",
        chain="ethereum",
    )

    await db.record_alert(
        entry_id=entry.id,
        alert_type="above",
        threshold=0.00002,
        triggered_price=0.000021,
    )
    await db.acknowledge_alerts()

    history = await db.get_alert_history()
    assert len(history) == 1
    assert history[0].acknowledged is True


@pytest.mark.asyncio
async def test_clear_alert_history(db):
    """Test clearing alert history."""
    entry = await db.add_entry(
        token_address="0x6982508145454Ce325dDbE47a25d4ec3d2311933",
        symbol="PEPE",
        chain="ethereum",
    )

    await db.record_alert(
        entry_id=entry.id,
        alert_type="above",
        threshold=0.00002,
        triggered_price=0.000021,
    )

    count = await db.clear_alert_history()
    assert count == 1

    history = await db.get_alert_history()
    assert len(history) == 0


@pytest.mark.asyncio
async def test_cascade_delete(db):
    """Test that deleting a watchlist entry cascades to alerts."""
    entry = await db.add_entry(
        token_address="0x6982508145454Ce325dDbE47a25d4ec3d2311933",
        symbol="PEPE",
        chain="ethereum",
    )

    await db.record_alert(
        entry_id=entry.id,
        alert_type="above",
        threshold=0.00002,
        triggered_price=0.000021,
    )

    await db.remove_entry("0x6982508145454Ce325dDbE47a25d4ec3d2311933")

    history = await db.get_alert_history()
    assert len(history) == 0
