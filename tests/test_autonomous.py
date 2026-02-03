"""Tests for autonomous agent and scheduler."""

import asyncio
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from app.autonomous_agent import (
    AutonomousWatchlistAgent,
    TokenCandidate,
    WatchlistReview,
    AutonomousCycleResult,
)
from app.autonomous_scheduler import AutonomousScheduler
from app.watchlist import WatchlistDB, WatchlistEntry


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
    manager.get_gemini_functions.return_value = []
    manager.get_client.return_value = None
    return manager


class TestTokenCandidate:
    """Tests for TokenCandidate dataclass."""

    def test_create_token_candidate(self):
        """Test creating a token candidate."""
        candidate = TokenCandidate(
            token_address="abc123",
            symbol="TEST",
            chain="solana",
            current_price=0.001,
            price_change_24h=15.5,
            volume_24h=100000,
            liquidity=50000,
            momentum_score=75,
            alert_above=0.0011,
            alert_below=0.00095,
            reasoning="Test reasoning",
        )

        assert candidate.symbol == "TEST"
        assert candidate.chain == "solana"
        assert candidate.momentum_score == 75
        assert candidate.alert_above == 0.0011
        assert candidate.alert_below == 0.00095


class TestWatchlistReview:
    """Tests for WatchlistReview dataclass."""

    def test_create_watchlist_review(self):
        """Test creating a watchlist review."""
        review = WatchlistReview(
            entry_id=1,
            token_address="abc123",
            symbol="TEST",
            action="update",
            new_alert_above=0.0012,
            new_alert_below=0.00098,
            new_momentum_score=70,
            reasoning="Price up, raising stop",
        )

        assert review.action == "update"
        assert review.new_alert_above == 0.0012
        assert review.reasoning == "Price up, raising stop"


class TestAutonomousWatchlistAgent:
    """Tests for AutonomousWatchlistAgent."""

    def test_calculate_triggers(self, mock_mcp_manager):
        """Test price trigger calculation."""
        with patch("app.autonomous_agent.genai"):
            agent = AutonomousWatchlistAgent(
                api_key="test-key",
                mcp_manager=mock_mcp_manager,
            )

            # Test default triggers (10% above, 5% below)
            above, below = agent.calculate_triggers(1.0)
            assert above == 1.10
            assert below == 0.95

            # Test custom triggers
            above, below = agent.calculate_triggers(1.0, take_profit_pct=0.20, stop_loss_pct=0.10)
            assert above == 1.20
            assert below == 0.90

    def test_calculate_trailing_stop(self, mock_mcp_manager):
        """Test trailing stop calculation."""
        with patch("app.autonomous_agent.genai"):
            agent = AutonomousWatchlistAgent(
                api_key="test-key",
                mcp_manager=mock_mcp_manager,
            )

            # Price went up - stop should increase
            new_stop = agent.calculate_trailing_stop(
                current_price=1.20,
                entry_price=1.00,
                current_stop=0.95,
                trail_pct=0.05,
            )
            assert new_stop == 1.14  # 1.20 * 0.95

            # Price went down - stop should NOT decrease
            new_stop = agent.calculate_trailing_stop(
                current_price=1.10,
                entry_price=1.00,
                current_stop=1.14,
                trail_pct=0.05,
            )
            assert new_stop == 1.14  # Stays at previous high

    def test_parse_discovery_response(self, mock_mcp_manager):
        """Test parsing discovery response JSON."""
        with patch("app.autonomous_agent.genai"):
            agent = AutonomousWatchlistAgent(
                api_key="test-key",
                mcp_manager=mock_mcp_manager,
            )

            response = '''
            Here is my analysis:
            {
                "candidates": [
                    {
                        "token_address": "abc123",
                        "symbol": "TEST",
                        "chain": "solana",
                        "current_price": 0.001,
                        "price_change_24h": 15.5,
                        "volume_24h": 100000,
                        "liquidity": 50000,
                        "momentum_score": 75,
                        "alert_above": 0.0011,
                        "alert_below": 0.00095,
                        "reasoning": "Strong volume"
                    }
                ],
                "summary": "Found 1 token"
            }
            '''

            candidates = agent._parse_discovery_response(response)
            assert len(candidates) == 1
            assert candidates[0].symbol == "TEST"
            assert candidates[0].momentum_score == 75

    def test_parse_review_response(self, mock_mcp_manager):
        """Test parsing review response JSON."""
        with patch("app.autonomous_agent.genai"):
            agent = AutonomousWatchlistAgent(
                api_key="test-key",
                mcp_manager=mock_mcp_manager,
            )

            # Create mock entries
            entries = [
                WatchlistEntry(
                    id=1,
                    token_address="abc123",
                    symbol="TEST",
                    chain="solana",
                    added_at=datetime.utcnow(),
                )
            ]

            response = '''
            {
                "reviews": [
                    {
                        "entry_id": 1,
                        "token_address": "abc123",
                        "symbol": "TEST",
                        "action": "update",
                        "new_alert_above": 0.0012,
                        "new_alert_below": 0.00098,
                        "new_momentum_score": 70,
                        "reasoning": "Price up 8%"
                    }
                ],
                "summary": "Updated 1 token"
            }
            '''

            reviews = agent._parse_review_response(response, entries)
            assert len(reviews) == 1
            assert reviews[0].action == "update"
            assert reviews[0].entry_id == 1

    def test_extract_json_object_simple(self, mock_mcp_manager):
        """Test extracting JSON from simple response."""
        with patch("app.autonomous_agent.genai"):
            agent = AutonomousWatchlistAgent(
                api_key="test-key",
                mcp_manager=mock_mcp_manager,
            )

            text = '{"reviews": [{"id": 1}]}'
            result = agent._extract_json_object(text, "reviews")
            assert result == '{"reviews": [{"id": 1}]}'

    def test_extract_json_object_with_surrounding_text(self, mock_mcp_manager):
        """Test extracting JSON when surrounded by text."""
        with patch("app.autonomous_agent.genai"):
            agent = AutonomousWatchlistAgent(
                api_key="test-key",
                mcp_manager=mock_mcp_manager,
            )

            text = '''
            Here is my analysis:
            {"reviews": [{"id": 1}]}
            That concludes the review.
            '''
            result = agent._extract_json_object(text, "reviews")
            assert result is not None
            assert '"reviews"' in result

    def test_extract_json_object_with_trailing_braces(self, mock_mcp_manager):
        """Test extracting JSON when there are braces after the JSON."""
        with patch("app.autonomous_agent.genai"):
            agent = AutonomousWatchlistAgent(
                api_key="test-key",
                mcp_manager=mock_mcp_manager,
            )

            text = '''
            {"reviews": [{"id": 1}], "summary": "done"}
            
            The token at {address} looks good.
            '''
            result = agent._extract_json_object(text, "reviews")
            assert result is not None
            import json
            data = json.loads(result)
            assert "reviews" in data
            assert data["summary"] == "done"

    def test_extract_json_object_code_block(self, mock_mcp_manager):
        """Test extracting JSON from code block."""
        with patch("app.autonomous_agent.genai"):
            agent = AutonomousWatchlistAgent(
                api_key="test-key",
                mcp_manager=mock_mcp_manager,
            )

            text = '''
            Here is the result:
            ```json
            {"reviews": [{"id": 1}]}
            ```
            '''
            result = agent._extract_json_object(text, "reviews")
            assert result is not None
            assert '"reviews"' in result

    def test_extract_json_object_nested_braces_in_string(self, mock_mcp_manager):
        """Test extracting JSON with braces inside string values."""
        with patch("app.autonomous_agent.genai"):
            agent = AutonomousWatchlistAgent(
                api_key="test-key",
                mcp_manager=mock_mcp_manager,
            )

            text = '''
            {"reviews": [{"id": 1, "note": "Token {ABC} looks good"}]}
            '''
            result = agent._extract_json_object(text, "reviews")
            assert result is not None
            import json
            data = json.loads(result)
            assert data["reviews"][0]["note"] == "Token {ABC} looks good"

    def test_extract_json_object_not_found(self, mock_mcp_manager):
        """Test when required key is not found."""
        with patch("app.autonomous_agent.genai"):
            agent = AutonomousWatchlistAgent(
                api_key="test-key",
                mcp_manager=mock_mcp_manager,
            )

            text = '{"candidates": [{"id": 1}]}'
            result = agent._extract_json_object(text, "reviews")
            assert result is None


class TestAutonomousScheduler:
    """Tests for AutonomousScheduler."""

    @pytest.mark.asyncio
    async def test_scheduler_status(self, db, mock_mcp_manager):
        """Test scheduler status reporting."""
        with patch("app.autonomous_agent.genai"):
            agent = AutonomousWatchlistAgent(
                api_key="test-key",
                mcp_manager=mock_mcp_manager,
            )

            scheduler = AutonomousScheduler(
                agent=agent,
                db=db,
                interval_seconds=3600,
                max_tokens=5,
            )

            status = scheduler.get_status()
            assert status["running"] is False
            assert status["interval_seconds"] == 3600
            assert status["max_tokens"] == 5
            assert status["cycle_count"] == 0

    @pytest.mark.asyncio
    async def test_scheduler_start_stop(self, db, mock_mcp_manager):
        """Test scheduler start and stop."""
        with patch("app.autonomous_agent.genai"):
            agent = AutonomousWatchlistAgent(
                api_key="test-key",
                mcp_manager=mock_mcp_manager,
            )

            # Mock the agent methods to return empty results
            agent.discover_tokens = AsyncMock(return_value=[])
            agent.review_watchlist = AsyncMock(return_value=[])

            scheduler = AutonomousScheduler(
                agent=agent,
                db=db,
                interval_seconds=3600,
                max_tokens=5,
            )

            assert not scheduler.is_running

            await scheduler.start()
            assert scheduler.is_running

            await scheduler.stop()
            assert not scheduler.is_running

    @pytest.mark.asyncio
    async def test_generate_summary(self, db, mock_mcp_manager):
        """Test summary generation."""
        with patch("app.autonomous_agent.genai"):
            agent = AutonomousWatchlistAgent(
                api_key="test-key",
                mcp_manager=mock_mcp_manager,
            )

            scheduler = AutonomousScheduler(
                agent=agent,
                db=db,
                interval_seconds=3600,
                max_tokens=5,
            )

            # Test with no changes
            result = AutonomousCycleResult(timestamp=datetime.utcnow())
            summary = scheduler._generate_summary(result)
            assert summary == "No changes"

            # Test with updated tokens
            result.tokens_updated = [
                WatchlistReview(
                    entry_id=1,
                    token_address="abc",
                    symbol="TEST",
                    action="update",
                    new_alert_above=0.0012,
                    new_alert_below=0.00095,
                    reasoning="Price up, raising stop",
                )
            ]
            summary = scheduler._generate_summary(result)
            assert "Updated: TEST" in summary

            # Test with errors
            result.errors = ["Some error"]
            summary = scheduler._generate_summary(result)
            assert "Errors: 1" in summary


class TestAutonomousWatchlistDB:
    """Tests for autonomous watchlist database operations."""

    @pytest.mark.asyncio
    async def test_add_autonomous_entry(self, db):
        """Test adding an autonomous entry."""
        entry = await db.add_autonomous_entry(
            token_address="abc123",
            symbol="TEST",
            chain="solana",
            alert_above=0.0011,
            alert_below=0.00095,
            momentum_score=75,
            review_notes="Test entry",
        )

        assert entry.autonomous_managed is True
        assert entry.momentum_score == 75
        assert entry.review_notes == "Test entry"

    @pytest.mark.asyncio
    async def test_list_autonomous_entries(self, db):
        """Test listing autonomous entries."""
        # Add autonomous entry
        await db.add_autonomous_entry(
            token_address="abc123",
            symbol="TEST1",
            chain="solana",
            momentum_score=75,
        )

        # Add regular entry
        await db.add_entry(
            token_address="def456",
            symbol="TEST2",
            chain="solana",
        )

        # List autonomous entries only
        entries = await db.list_autonomous_entries()
        assert len(entries) == 1
        assert entries[0].symbol == "TEST1"

    @pytest.mark.asyncio
    async def test_count_autonomous_entries(self, db):
        """Test counting autonomous entries."""
        assert await db.count_autonomous_entries() == 0

        await db.add_autonomous_entry(
            token_address="abc123",
            symbol="TEST",
            chain="solana",
        )

        assert await db.count_autonomous_entries() == 1

    @pytest.mark.asyncio
    async def test_update_autonomous_entry(self, db):
        """Test updating an autonomous entry."""
        entry = await db.add_autonomous_entry(
            token_address="abc123",
            symbol="TEST",
            chain="solana",
            momentum_score=75,
        )

        success = await db.update_autonomous_entry(
            entry_id=entry.id,
            alert_above=0.0012,
            alert_below=0.00098,
            momentum_score=80,
            review_notes="Updated",
        )

        assert success is True

        # Verify update
        entries = await db.list_autonomous_entries()
        assert len(entries) == 1
        assert entries[0].alert_above == 0.0012
        assert entries[0].momentum_score == 80
        assert entries[0].review_notes == "Updated"

    @pytest.mark.asyncio
    async def test_remove_autonomous_entry(self, db):
        """Test removing an autonomous entry."""
        await db.add_autonomous_entry(
            token_address="abc123",
            symbol="TEST",
            chain="solana",
        )

        removed = await db.remove_autonomous_entry("abc123", "solana")
        assert removed is True

        entries = await db.list_autonomous_entries()
        assert len(entries) == 0

    @pytest.mark.asyncio
    async def test_clear_autonomous_watchlist(self, db):
        """Test clearing all autonomous entries."""
        # Add multiple autonomous entries
        await db.add_autonomous_entry(
            token_address="abc123",
            symbol="TEST1",
            chain="solana",
        )
        await db.add_autonomous_entry(
            token_address="def456",
            symbol="TEST2",
            chain="solana",
        )

        # Add a regular entry
        await db.add_entry(
            token_address="ghi789",
            symbol="TEST3",
            chain="solana",
        )

        # Clear autonomous entries
        count = await db.clear_autonomous_watchlist()
        assert count == 2

        # Verify only autonomous entries were removed
        all_entries = await db.list_entries()
        assert len(all_entries) == 1
        assert all_entries[0].symbol == "TEST3"
