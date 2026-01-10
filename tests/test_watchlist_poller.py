"""Tests for watchlist background poller."""

import asyncio
import tempfile
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from app.watchlist import WatchlistDB
from app.watchlist_poller import WatchlistPoller, TriggeredAlert


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


@pytest.fixture
def mock_mcp_manager():
    """Create a mock MCP manager."""
    manager = MagicMock()
    
    # Create mock clients
    mock_dexscreener = AsyncMock()
    mock_dexpaprika = AsyncMock()
    
    def get_client(name: str):
        if name == "dexscreener":
            return mock_dexscreener
        elif name == "dexpaprika":
            return mock_dexpaprika
        return None
    
    manager.get_client = get_client
    manager._mock_dexscreener = mock_dexscreener
    manager._mock_dexpaprika = mock_dexpaprika
    
    return manager


@pytest.mark.asyncio
async def test_poller_start_stop(db, mock_mcp_manager):
    """Test starting and stopping the poller."""
    poller = WatchlistPoller(db, mock_mcp_manager, poll_interval=1)
    
    assert not poller.is_running
    
    await poller.start()
    assert poller.is_running
    
    await poller.stop()
    assert not poller.is_running


@pytest.mark.asyncio
async def test_poller_check_now_empty_watchlist(db, mock_mcp_manager):
    """Test check_now with empty watchlist."""
    poller = WatchlistPoller(db, mock_mcp_manager)
    
    alerts = await poller.check_now()
    assert alerts == []


@pytest.mark.asyncio
async def test_poller_fetches_prices(db, mock_mcp_manager):
    """Test that poller fetches prices from MCP clients."""
    # Add entry to watchlist
    await db.add_entry(
        token_address="0x6982508145454ce325ddbe47a25d4ec3d2311933",
        symbol="PEPE",
        chain="ethereum",
    )
    
    # Mock DexScreener response
    mock_mcp_manager._mock_dexscreener.call_tool = AsyncMock(return_value={
        "pairs": [{"priceUsd": "0.0000185"}]
    })
    
    poller = WatchlistPoller(db, mock_mcp_manager)
    await poller.check_now()
    
    # Verify price was updated
    entry = await db.get_entry(symbol="PEPE")
    assert entry.last_price == 0.0000185
    assert entry.last_checked is not None


@pytest.mark.asyncio
async def test_poller_triggers_above_alert(db, mock_mcp_manager):
    """Test that poller triggers alert when price goes above threshold."""
    # Add entry with alert threshold
    await db.add_entry(
        token_address="0x6982508145454ce325ddbe47a25d4ec3d2311933",
        symbol="PEPE",
        chain="ethereum",
        alert_above=0.00002,
    )
    
    # Mock price above threshold
    mock_mcp_manager._mock_dexscreener.call_tool = AsyncMock(return_value={
        "pairs": [{"priceUsd": "0.000021"}]
    })
    
    triggered_alerts: List[TriggeredAlert] = []
    
    def callback(alert: TriggeredAlert):
        triggered_alerts.append(alert)
    
    poller = WatchlistPoller(db, mock_mcp_manager, alert_callback=callback)
    alerts = await poller.check_now()
    
    assert len(alerts) == 1
    assert alerts[0].symbol == "PEPE"
    assert alerts[0].alert_type == "above"
    assert alerts[0].threshold == 0.00002
    assert alerts[0].current_price == 0.000021
    
    # Verify callback was called
    assert len(triggered_alerts) == 1
    
    # Verify alert was recorded in database
    history = await db.get_alert_history()
    assert len(history) == 1


@pytest.mark.asyncio
async def test_poller_triggers_below_alert(db, mock_mcp_manager):
    """Test that poller triggers alert when price goes below threshold."""
    # Add entry with alert threshold and set initial price
    entry = await db.add_entry(
        token_address="0x6982508145454ce325ddbe47a25d4ec3d2311933",
        symbol="PEPE",
        chain="ethereum",
        alert_below=0.00001,
    )
    # Set initial price above threshold
    await db.update_price(entry.id, 0.000015)
    
    # Mock price below threshold
    mock_mcp_manager._mock_dexscreener.call_tool = AsyncMock(return_value={
        "pairs": [{"priceUsd": "0.000009"}]
    })
    
    poller = WatchlistPoller(db, mock_mcp_manager)
    alerts = await poller.check_now()
    
    assert len(alerts) == 1
    assert alerts[0].alert_type == "below"
    assert alerts[0].threshold == 0.00001
    assert alerts[0].current_price == 0.000009


@pytest.mark.asyncio
async def test_poller_no_duplicate_alerts(db, mock_mcp_manager):
    """Test that poller doesn't trigger duplicate alerts for same crossing."""
    # Add entry with alert threshold
    entry = await db.add_entry(
        token_address="0x6982508145454ce325ddbe47a25d4ec3d2311933",
        symbol="PEPE",
        chain="ethereum",
        alert_above=0.00002,
    )
    # Set last_price already above threshold
    await db.update_price(entry.id, 0.000021)
    
    # Mock price still above threshold
    mock_mcp_manager._mock_dexscreener.call_tool = AsyncMock(return_value={
        "pairs": [{"priceUsd": "0.000022"}]
    })
    
    poller = WatchlistPoller(db, mock_mcp_manager)
    alerts = await poller.check_now()
    
    # Should not trigger since price was already above
    assert len(alerts) == 0


@pytest.mark.asyncio
async def test_poller_no_alert_when_below_threshold(db, mock_mcp_manager):
    """Test that poller doesn't trigger alert when price is below threshold."""
    await db.add_entry(
        token_address="0x6982508145454ce325ddbe47a25d4ec3d2311933",
        symbol="PEPE",
        chain="ethereum",
        alert_above=0.00002,
    )
    
    # Mock price below threshold
    mock_mcp_manager._mock_dexscreener.call_tool = AsyncMock(return_value={
        "pairs": [{"priceUsd": "0.0000185"}]
    })
    
    poller = WatchlistPoller(db, mock_mcp_manager)
    alerts = await poller.check_now()
    
    assert len(alerts) == 0


@pytest.mark.asyncio
async def test_poller_fallback_to_dexpaprika(db, mock_mcp_manager):
    """Test that poller falls back to DexPaprika when DexScreener fails."""
    await db.add_entry(
        token_address="0x6982508145454ce325ddbe47a25d4ec3d2311933",
        symbol="PEPE",
        chain="ethereum",
    )
    
    # Mock DexScreener to fail
    mock_mcp_manager._mock_dexscreener.call_tool = AsyncMock(side_effect=Exception("API error"))
    
    # Mock DexPaprika response
    mock_mcp_manager._mock_dexpaprika.call_tool = AsyncMock(return_value={
        "price_usd": 0.0000185
    })
    
    poller = WatchlistPoller(db, mock_mcp_manager)
    await poller.check_now()
    
    # Verify price was updated from DexPaprika
    entry = await db.get_entry(symbol="PEPE")
    assert entry.last_price == 0.0000185


@pytest.mark.asyncio
async def test_poller_handles_missing_price(db, mock_mcp_manager):
    """Test that poller handles missing price gracefully."""
    await db.add_entry(
        token_address="0x6982508145454ce325ddbe47a25d4ec3d2311933",
        symbol="PEPE",
        chain="ethereum",
        alert_above=0.00002,
    )
    
    # Mock empty response
    mock_mcp_manager._mock_dexscreener.call_tool = AsyncMock(return_value={"pairs": []})
    mock_mcp_manager._mock_dexpaprika.call_tool = AsyncMock(return_value={})
    
    poller = WatchlistPoller(db, mock_mcp_manager)
    alerts = await poller.check_now()
    
    # Should not crash, just return no alerts
    assert len(alerts) == 0
    
    # Price should not be updated
    entry = await db.get_entry(symbol="PEPE")
    assert entry.last_price is None


@pytest.mark.asyncio
async def test_extract_price_from_dexscreener():
    """Test price extraction from DexScreener response."""
    poller = WatchlistPoller(MagicMock(), MagicMock())
    
    # Valid response
    result = {"pairs": [{"priceUsd": "0.0000185"}]}
    assert poller._extract_price_from_dexscreener(result) == 0.0000185
    
    # Empty pairs
    result = {"pairs": []}
    assert poller._extract_price_from_dexscreener(result) is None
    
    # Missing priceUsd
    result = {"pairs": [{"baseToken": {"symbol": "PEPE"}}]}
    assert poller._extract_price_from_dexscreener(result) is None
    
    # Not a dict
    assert poller._extract_price_from_dexscreener("invalid") is None


@pytest.mark.asyncio
async def test_extract_price_from_dexpaprika():
    """Test price extraction from DexPaprika response."""
    poller = WatchlistPoller(MagicMock(), MagicMock())
    
    # Valid response
    result = {"price_usd": 0.0000185}
    assert poller._extract_price_from_dexpaprika(result) == 0.0000185
    
    # Missing price_usd
    result = {"symbol": "PEPE"}
    assert poller._extract_price_from_dexpaprika(result) is None
    
    # Not a dict
    assert poller._extract_price_from_dexpaprika("invalid") is None
