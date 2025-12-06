"""Tests for MCP Manager configuration."""

from app.mcp_client import MCPManager


def test_mcp_manager_with_honeypot():
    """Test MCPManager initializes honeypot client when cmd is provided."""
    manager = MCPManager(
        dexscreener_cmd="echo dexscreener",
        dexpaprika_cmd="echo dexpaprika",
        honeypot_cmd="echo honeypot",
    )
    
    assert manager.honeypot is not None
    assert manager.honeypot.name == "honeypot"


def test_mcp_manager_without_honeypot():
    """Test MCPManager skips honeypot client when cmd is empty."""
    manager = MCPManager(
        dexscreener_cmd="echo dexscreener",
        dexpaprika_cmd="echo dexpaprika",
        honeypot_cmd="",
    )
    
    assert manager.honeypot is None


def test_mcp_manager_get_client_with_honeypot():
    """Test get_client returns honeypot when configured."""
    manager = MCPManager(
        dexscreener_cmd="echo dexscreener",
        dexpaprika_cmd="echo dexpaprika",
        honeypot_cmd="echo honeypot",
    )
    
    client = manager.get_client("honeypot")
    assert client is not None
    assert client.name == "honeypot"


def test_mcp_manager_get_client_without_honeypot():
    """Test get_client returns None when honeypot is not configured."""
    manager = MCPManager(
        dexscreener_cmd="echo dexscreener",
        dexpaprika_cmd="echo dexpaprika",
        honeypot_cmd="",
    )
    
    client = manager.get_client("honeypot")
    assert client is None
