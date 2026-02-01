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
    
    # Valid response with price only
    result = {"pairs": [{"priceUsd": "0.0000185"}]}
    data = poller._extract_price_from_dexscreener(result)
    assert data.price == 0.0000185
    assert data.liquidity is None
    
    # Valid response with liquidity
    result = {"pairs": [{"priceUsd": "0.0000185", "liquidity": {"usd": 500000}}]}
    data = poller._extract_price_from_dexscreener(result)
    assert data.price == 0.0000185
    assert data.liquidity == 500000
    
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


class TestAutoAdjustAlerts:
    """Tests for auto-adjust alerts feature."""

    @pytest.mark.asyncio
    async def test_auto_adjust_after_above_trigger(self, db, mock_mcp_manager):
        """Test that alerts are auto-adjusted after above threshold trigger."""
        # Add entry with both alert thresholds
        await db.add_entry(
            token_address="0x6982508145454ce325ddbe47a25d4ec3d2311933",
            symbol="PEPE",
            chain="ethereum",
            alert_above=0.00002,
            alert_below=0.00001,
        )
        
        # Mock price above threshold
        mock_mcp_manager._mock_dexscreener.call_tool = AsyncMock(return_value={
            "pairs": [{"priceUsd": "0.000021"}]
        })
        
        poller = WatchlistPoller(
            db, mock_mcp_manager,
            auto_adjust_enabled=True,
            take_profit_percent=10.0,
            stop_loss_percent=5.0,
        )
        alerts = await poller.check_now()
        
        assert len(alerts) == 1
        assert alerts[0].alert_type == "above"
        
        # Check new thresholds are set in alert
        assert alerts[0].new_alert_above == pytest.approx(0.000021 * 1.10, rel=1e-6)
        assert alerts[0].new_alert_below == pytest.approx(0.000021 * 0.95, rel=1e-6)
        
        # Verify database was updated
        entry = await db.get_entry(symbol="PEPE")
        assert entry.alert_above == pytest.approx(0.000021 * 1.10, rel=1e-6)
        assert entry.alert_below == pytest.approx(0.000021 * 0.95, rel=1e-6)

    @pytest.mark.asyncio
    async def test_auto_adjust_after_below_trigger(self, db, mock_mcp_manager):
        """Test that alerts are auto-adjusted after below threshold trigger."""
        # Add entry with both alert thresholds and set initial price
        entry = await db.add_entry(
            token_address="0x6982508145454ce325ddbe47a25d4ec3d2311933",
            symbol="PEPE",
            chain="ethereum",
            alert_above=0.00002,
            alert_below=0.00001,
        )
        await db.update_price(entry.id, 0.000015)
        
        # Mock price below threshold
        mock_mcp_manager._mock_dexscreener.call_tool = AsyncMock(return_value={
            "pairs": [{"priceUsd": "0.000009"}]
        })
        
        poller = WatchlistPoller(
            db, mock_mcp_manager,
            auto_adjust_enabled=True,
            take_profit_percent=10.0,
            stop_loss_percent=5.0,
        )
        alerts = await poller.check_now()
        
        assert len(alerts) == 1
        assert alerts[0].alert_type == "below"
        
        # Check new thresholds are set
        assert alerts[0].new_alert_above == pytest.approx(0.000009 * 1.10, rel=1e-6)
        assert alerts[0].new_alert_below == pytest.approx(0.000009 * 0.95, rel=1e-6)
        
        # Verify database was updated
        entry = await db.get_entry(symbol="PEPE")
        assert entry.alert_above == pytest.approx(0.000009 * 1.10, rel=1e-6)
        assert entry.alert_below == pytest.approx(0.000009 * 0.95, rel=1e-6)

    @pytest.mark.asyncio
    async def test_auto_adjust_disabled(self, db, mock_mcp_manager):
        """Test that alerts are NOT adjusted when feature is disabled."""
        original_above = 0.00002
        original_below = 0.00001
        
        await db.add_entry(
            token_address="0x6982508145454ce325ddbe47a25d4ec3d2311933",
            symbol="PEPE",
            chain="ethereum",
            alert_above=original_above,
            alert_below=original_below,
        )
        
        # Mock price above threshold
        mock_mcp_manager._mock_dexscreener.call_tool = AsyncMock(return_value={
            "pairs": [{"priceUsd": "0.000021"}]
        })
        
        poller = WatchlistPoller(
            db, mock_mcp_manager,
            auto_adjust_enabled=False,  # Disabled
        )
        alerts = await poller.check_now()
        
        assert len(alerts) == 1
        
        # New thresholds should be None
        assert alerts[0].new_alert_above is None
        assert alerts[0].new_alert_below is None
        
        # Database should NOT be updated
        entry = await db.get_entry(symbol="PEPE")
        assert entry.alert_above == original_above
        assert entry.alert_below == original_below

    @pytest.mark.asyncio
    async def test_auto_adjust_custom_percentages(self, db, mock_mcp_manager):
        """Test auto-adjust with custom take-profit and stop-loss percentages."""
        await db.add_entry(
            token_address="0x6982508145454ce325ddbe47a25d4ec3d2311933",
            symbol="PEPE",
            chain="ethereum",
            alert_above=0.00002,
            alert_below=0.00001,
        )
        
        triggered_price = 0.000025
        mock_mcp_manager._mock_dexscreener.call_tool = AsyncMock(return_value={
            "pairs": [{"priceUsd": str(triggered_price)}]
        })
        
        # Custom percentages: 20% take profit, 10% stop loss
        poller = WatchlistPoller(
            db, mock_mcp_manager,
            auto_adjust_enabled=True,
            take_profit_percent=20.0,
            stop_loss_percent=10.0,
        )
        alerts = await poller.check_now()
        
        assert len(alerts) == 1
        
        # Verify custom percentages were used
        expected_above = triggered_price * 1.20
        expected_below = triggered_price * 0.90
        assert alerts[0].new_alert_above == pytest.approx(expected_above, rel=1e-6)
        assert alerts[0].new_alert_below == pytest.approx(expected_below, rel=1e-6)

    @pytest.mark.asyncio
    async def test_auto_adjust_method_directly(self, db, mock_mcp_manager):
        """Test the _auto_adjust_alerts method directly."""
        entry = await db.add_entry(
            token_address="0x1234",
            symbol="TEST",
            chain="ethereum",
            alert_above=100.0,
            alert_below=80.0,
        )
        
        poller = WatchlistPoller(
            db, mock_mcp_manager,
            auto_adjust_enabled=True,
            take_profit_percent=10.0,
            stop_loss_percent=5.0,
        )
        
        current_price = 95.0
        new_above, new_below = await poller._auto_adjust_alerts(
            entry.id, current_price, "below", entry.alert_below
        )
        
        assert new_above == pytest.approx(95.0 * 1.10, rel=1e-6)  # 104.5
        assert new_below == pytest.approx(95.0 * 0.95, rel=1e-6)  # 90.25
        
        # Verify database was updated
        updated = await db.get_entry(symbol="TEST")
        assert updated.alert_above == pytest.approx(104.5, rel=1e-6)
        assert updated.alert_below == pytest.approx(90.25, rel=1e-6)

    @pytest.mark.asyncio
    async def test_auto_adjust_returns_none_when_disabled(self, db, mock_mcp_manager):
        """Test that _auto_adjust_alerts returns None when disabled."""
        entry = await db.add_entry(
            token_address="0x1234",
            symbol="TEST",
            chain="ethereum",
            alert_above=100.0,
            alert_below=80.0,
        )
        
        poller = WatchlistPoller(
            db, mock_mcp_manager,
            auto_adjust_enabled=False,
        )
        
        new_above, new_below = await poller._auto_adjust_alerts(
            entry.id, 95.0, "above", entry.alert_below
        )
        
        assert new_above is None
        assert new_below is None

    @pytest.mark.asyncio
    async def test_trailing_stop_does_not_lower_on_upward_trigger(self, db, mock_mcp_manager):
        """Test that stop-loss is NOT lowered when price moves up (trailing stop)."""
        # Entry with high stop-loss already set
        entry = await db.add_entry(
            token_address="0x1234",
            symbol="TEST",
            chain="ethereum",
            alert_above=100.0,
            alert_below=90.0,  # High stop-loss
        )
        
        poller = WatchlistPoller(
            db, mock_mcp_manager,
            auto_adjust_enabled=True,
            take_profit_percent=10.0,
            stop_loss_percent=5.0,
        )
        
        # Price triggers above threshold at 105
        # Candidate new_below = 105 * 0.95 = 99.75, but current is 90
        # Trailing stop should keep 90 since 99.75 > 90
        current_price = 105.0
        new_above, new_below = await poller._auto_adjust_alerts(
            entry.id, current_price, "above", entry.alert_below
        )
        
        # New above should be recalculated
        assert new_above == pytest.approx(105.0 * 1.10, rel=1e-6)  # 115.5
        # New below should be the higher of current (90) vs candidate (99.75)
        assert new_below == pytest.approx(99.75, rel=1e-6)
        
    @pytest.mark.asyncio
    async def test_trailing_stop_raises_on_upward_trigger(self, db, mock_mcp_manager):
        """Test that stop-loss is raised when new candidate is higher (trailing stop)."""
        # Entry with low stop-loss
        entry = await db.add_entry(
            token_address="0x1234",
            symbol="TEST",
            chain="ethereum",
            alert_above=100.0,
            alert_below=50.0,  # Low stop-loss
        )
        
        poller = WatchlistPoller(
            db, mock_mcp_manager,
            auto_adjust_enabled=True,
            take_profit_percent=10.0,
            stop_loss_percent=5.0,
        )
        
        # Price triggers above at 105
        # Candidate new_below = 105 * 0.95 = 99.75
        # Since 99.75 > 50, trailing stop should raise to 99.75
        current_price = 105.0
        new_above, new_below = await poller._auto_adjust_alerts(
            entry.id, current_price, "above", entry.alert_below
        )
        
        assert new_above == pytest.approx(115.5, rel=1e-6)
        assert new_below == pytest.approx(99.75, rel=1e-6)  # Raised from 50 to 99.75

    @pytest.mark.asyncio
    async def test_downward_trigger_recalculates_both(self, db, mock_mcp_manager):
        """Test that downward trigger recalculates both thresholds normally."""
        entry = await db.add_entry(
            token_address="0x1234",
            symbol="TEST",
            chain="ethereum",
            alert_above=100.0,
            alert_below=80.0,
        )
        
        poller = WatchlistPoller(
            db, mock_mcp_manager,
            auto_adjust_enabled=True,
            take_profit_percent=10.0,
            stop_loss_percent=5.0,
        )
        
        # Price drops and triggers below threshold at 75
        current_price = 75.0
        new_above, new_below = await poller._auto_adjust_alerts(
            entry.id, current_price, "below", entry.alert_below
        )
        
        # Both should be recalculated from new lower price
        assert new_above == pytest.approx(75.0 * 1.10, rel=1e-6)  # 82.5
        assert new_below == pytest.approx(75.0 * 0.95, rel=1e-6)  # 71.25
