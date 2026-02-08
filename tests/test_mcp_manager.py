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


def test_format_tools_for_system_prompt_with_tools():
    """Test format_tools_for_system_prompt generates correct output with tools."""
    manager = MCPManager(
        dexscreener_cmd="echo dexscreener",
        dexpaprika_cmd="echo dexpaprika",
        honeypot_cmd="",
    )
    
    # Simulate tools being loaded
    manager.dexscreener._tools = [
        {
            "name": "search_pairs",
            "description": "Search for token pairs by query",
            "inputSchema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
        {
            "name": "get_token_info",
            "description": "Get token information",
            "inputSchema": {
                "type": "object",
                "properties": {"address": {"type": "string"}},
                "required": [],
            },
        },
    ]
    manager.dexpaprika._tools = []
    
    result = manager.format_tools_for_system_prompt()
    
    assert "### dexscreener tools:" in result
    assert "dexscreener_search_pairs" in result
    assert "[REQUIRED: query:string]" in result
    assert "dexscreener_get_token_info" in result
    # get_token_info has no required params, so no [REQUIRED: ...] tag
    assert "- dexscreener_get_token_info: Get token information" in result


def test_format_tools_for_system_prompt_empty_tools():
    """Test format_tools_for_system_prompt returns empty string when no tools."""
    manager = MCPManager(
        dexscreener_cmd="echo dexscreener",
        dexpaprika_cmd="echo dexpaprika",
        honeypot_cmd="",
    )
    
    # No tools loaded
    manager.dexscreener._tools = []
    manager.dexpaprika._tools = []
    
    result = manager.format_tools_for_system_prompt()
    
    assert result == ""


def test_format_tools_for_system_prompt_description_truncation():
    """Test that long descriptions are truncated at word boundaries."""
    manager = MCPManager(
        dexscreener_cmd="echo dexscreener",
        dexpaprika_cmd="echo dexpaprika",
        honeypot_cmd="",
    )
    
    long_description = "This is a very long description that should be truncated at a word boundary to avoid cutting words in half"
    manager.dexscreener._tools = [
        {
            "name": "testTool",
            "description": long_description,
            "inputSchema": {"type": "object", "properties": {}, "required": []},
        },
    ]
    manager.dexpaprika._tools = []
    
    result = manager.format_tools_for_system_prompt()
    
    # Should be truncated and end with ...
    assert "..." in result
    # Should not contain the full description
    assert long_description not in result
    # Should not cut mid-word
    assert "bounda..." not in result


def test_truncate_description_short():
    """Test _truncate_description returns short descriptions unchanged."""
    result = MCPManager._truncate_description("Short description", max_length=100)
    assert result == "Short description"


def test_truncate_description_at_word_boundary():
    """Test _truncate_description truncates at word boundary."""
    desc = "This is a test description that is longer than the maximum allowed length"
    result = MCPManager._truncate_description(desc, max_length=30)
    
    assert result.endswith("...")
    assert len(result) <= 33  # 30 + "..."
    # Should break at word boundary
    assert result in ["This is a test description...", "This is a test..."]


def test_mcp_manager_with_blockscout():
    """Test MCPManager initializes blockscout client when cmd is provided."""
    manager = MCPManager(
        dexscreener_cmd="echo dexscreener",
        dexpaprika_cmd="echo dexpaprika",
        blockscout_cmd="echo blockscout",
    )

    assert manager.blockscout is not None
    assert manager.blockscout.name == "blockscout"


def test_mcp_manager_without_blockscout():
    """Test MCPManager skips blockscout client when cmd is empty."""
    manager = MCPManager(
        dexscreener_cmd="echo dexscreener",
        dexpaprika_cmd="echo dexpaprika",
        blockscout_cmd="",
    )

    assert manager.blockscout is None


def test_mcp_manager_get_client_blockscout():
    """Test get_client returns blockscout when configured."""
    manager = MCPManager(
        dexscreener_cmd="echo dexscreener",
        dexpaprika_cmd="echo dexpaprika",
        blockscout_cmd="echo blockscout",
    )

    client = manager.get_client("blockscout")
    assert client is not None
    assert client.name == "blockscout"


def test_mcp_manager_get_client_without_blockscout():
    """Test get_client returns None when blockscout is not configured."""
    manager = MCPManager(
        dexscreener_cmd="echo dexscreener",
        dexpaprika_cmd="echo dexpaprika",
        blockscout_cmd="",
    )

    client = manager.get_client("blockscout")
    assert client is None
