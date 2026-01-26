"""Tests for watchlist tool provider."""

import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from app.watchlist import WatchlistDB
from app.watchlist_tools import WatchlistToolProvider, WATCHLIST_TOOLS


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


@pytest_asyncio.fixture
async def provider(db):
    """Create a watchlist tool provider."""
    return WatchlistToolProvider(db)


class TestWatchlistToolProvider:
    """Tests for WatchlistToolProvider."""

    def test_tool_definitions(self, provider):
        """Test that tool definitions are correct."""
        assert len(provider.tools) == 4
        tool_names = [t["name"] for t in provider.tools]
        assert "add" in tool_names
        assert "remove" in tool_names
        assert "list" in tool_names
        assert "get" in tool_names

    def test_to_gemini_functions(self, provider):
        """Test conversion to Gemini function declarations."""
        functions = provider.to_gemini_functions()
        assert len(functions) == 4
        
        # Check function names are namespaced
        names = [f.name for f in functions]
        assert "watchlist_add" in names
        assert "watchlist_remove" in names
        assert "watchlist_list" in names
        assert "watchlist_get" in names

    @pytest.mark.asyncio
    async def test_tool_add(self, provider):
        """Test adding a token via tool."""
        result = await provider.call_tool("add", {
            "token_address": "0x6982508145454Ce325dDbE47a25d4ec3d2311933",
            "symbol": "PEPE",
            "chain": "ethereum",
        })
        
        assert result["success"] is True
        assert "PEPE" in result["message"]
        assert result["entry"]["symbol"] == "PEPE"
        assert result["entry"]["chain"] == "ethereum"

    @pytest.mark.asyncio
    async def test_tool_add_missing_params(self, provider):
        """Test adding a token with missing required params."""
        result = await provider.call_tool("add", {
            "token_address": "0x123",
            "symbol": "TEST",
            # Missing chain
        })
        
        assert result["success"] is False
        assert "Missing required parameters" in result["error"]

    @pytest.mark.asyncio
    async def test_tool_list_empty(self, provider):
        """Test listing empty watchlist."""
        result = await provider.call_tool("list", {})
        
        assert result["success"] is True
        assert result["count"] == 0
        assert result["entries"] == []

    @pytest.mark.asyncio
    async def test_tool_list_with_entries(self, provider):
        """Test listing watchlist with entries."""
        # Add some entries
        await provider.call_tool("add", {
            "token_address": "0x111",
            "symbol": "TOKEN1",
            "chain": "ethereum",
        })
        await provider.call_tool("add", {
            "token_address": "0x222",
            "symbol": "TOKEN2",
            "chain": "base",
        })
        
        result = await provider.call_tool("list", {})
        
        assert result["success"] is True
        assert result["count"] == 2
        assert len(result["entries"]) == 2

    @pytest.mark.asyncio
    async def test_tool_get_by_symbol(self, provider):
        """Test getting a token by symbol."""
        await provider.call_tool("add", {
            "token_address": "0x6982508145454Ce325dDbE47a25d4ec3d2311933",
            "symbol": "PEPE",
            "chain": "ethereum",
        })
        
        result = await provider.call_tool("get", {"symbol": "pepe"})  # lowercase
        
        assert result["success"] is True
        assert result["entry"]["symbol"] == "PEPE"

    @pytest.mark.asyncio
    async def test_tool_get_not_found(self, provider):
        """Test getting a token that doesn't exist."""
        result = await provider.call_tool("get", {"symbol": "NOTFOUND"})
        
        assert result["success"] is False
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_tool_remove_by_symbol(self, provider):
        """Test removing a token by symbol."""
        await provider.call_tool("add", {
            "token_address": "0x6982508145454Ce325dDbE47a25d4ec3d2311933",
            "symbol": "PEPE",
            "chain": "ethereum",
        })
        
        result = await provider.call_tool("remove", {"symbol": "PEPE"})
        
        assert result["success"] is True
        assert "Removed" in result["message"]
        
        # Verify it's gone
        list_result = await provider.call_tool("list", {})
        assert list_result["count"] == 0

    @pytest.mark.asyncio
    async def test_tool_remove_by_address(self, provider):
        """Test removing a token by address."""
        await provider.call_tool("add", {
            "token_address": "0x6982508145454Ce325dDbE47a25d4ec3d2311933",
            "symbol": "PEPE",
            "chain": "ethereum",
        })
        
        result = await provider.call_tool("remove", {
            "token_address": "0x6982508145454Ce325dDbE47a25d4ec3d2311933"
        })
        
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_tool_remove_not_found(self, provider):
        """Test removing a token that doesn't exist."""
        result = await provider.call_tool("remove", {"symbol": "NOTFOUND"})
        
        assert result["success"] is False
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_tool_remove_missing_params(self, provider):
        """Test removing without required params."""
        result = await provider.call_tool("remove", {})
        
        assert result["success"] is False
        assert "must be provided" in result["error"]

    @pytest.mark.asyncio
    async def test_unknown_method(self, provider):
        """Test calling an unknown method."""
        with pytest.raises(ValueError, match="Unknown watchlist tool method"):
            await provider.call_tool("unknown", {})
