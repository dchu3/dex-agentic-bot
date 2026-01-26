"""Watchlist tool provider for Gemini agent integration."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from google.genai import types

from app.watchlist import WatchlistDB, WatchlistEntry


# Tool definitions in MCP-like schema format
WATCHLIST_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "add",
        "description": "Add a token to the user's watchlist for tracking. Requires token_address, symbol, and chain.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "token_address": {
                    "type": "string",
                    "description": "The token's contract address",
                },
                "symbol": {
                    "type": "string",
                    "description": "The token symbol (e.g., PEPE, DOGE)",
                },
                "chain": {
                    "type": "string",
                    "description": "The blockchain network (e.g., ethereum, solana, base)",
                },
            },
            "required": ["token_address", "symbol", "chain"],
        },
    },
    {
        "name": "remove",
        "description": "Remove a token from the user's watchlist. Can specify by token_address or symbol.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "token_address": {
                    "type": "string",
                    "description": "The token's contract address (optional if symbol provided)",
                },
                "symbol": {
                    "type": "string",
                    "description": "The token symbol (optional if token_address provided)",
                },
                "chain": {
                    "type": "string",
                    "description": "The blockchain network (optional, helps disambiguate)",
                },
            },
        },
    },
    {
        "name": "list",
        "description": "List all tokens currently in the user's watchlist with their details.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get",
        "description": "Get details of a specific token in the watchlist by address or symbol.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "token_address": {
                    "type": "string",
                    "description": "The token's contract address (optional if symbol provided)",
                },
                "symbol": {
                    "type": "string",
                    "description": "The token symbol (optional if token_address provided)",
                },
            },
        },
    },
]


def _entry_to_dict(entry: WatchlistEntry) -> Dict[str, Any]:
    """Convert a WatchlistEntry to a dictionary for JSON serialization."""
    return {
        "id": entry.id,
        "token_address": entry.token_address,
        "symbol": entry.symbol,
        "chain": entry.chain,
        "added_at": entry.added_at.isoformat() if entry.added_at else None,
        "alert_above": entry.alert_above,
        "alert_below": entry.alert_below,
        "last_price": entry.last_price,
        "last_checked": entry.last_checked.isoformat() if entry.last_checked else None,
    }


class WatchlistToolProvider:
    """Provides watchlist management tools for the Gemini agent."""

    def __init__(self, db: WatchlistDB) -> None:
        self.db = db
        self.name = "watchlist"
        self._tools = WATCHLIST_TOOLS

    @property
    def tools(self) -> List[Dict[str, Any]]:
        """Return tool definitions."""
        return self._tools

    def to_gemini_functions(self) -> List[types.FunctionDeclaration]:
        """Convert tools to Gemini function declarations."""
        from app.tool_converter import mcp_tool_to_gemini_function

        declarations = []
        for tool in self._tools:
            declaration = mcp_tool_to_gemini_function(self.name, tool)
            if declaration:
                declarations.append(declaration)
        return declarations

    async def call_tool(self, method: str, arguments: Dict[str, Any]) -> Any:
        """Execute a watchlist tool and return the result."""
        if method == "add":
            return await self._tool_add(arguments)
        elif method == "remove":
            return await self._tool_remove(arguments)
        elif method == "list":
            return await self._tool_list(arguments)
        elif method == "get":
            return await self._tool_get(arguments)
        else:
            raise ValueError(f"Unknown watchlist tool method: {method}")

    async def _tool_add(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Add a token to the watchlist."""
        token_address = args.get("token_address")
        symbol = args.get("symbol")
        chain = args.get("chain")

        if not token_address or not symbol or not chain:
            return {
                "success": False,
                "error": "Missing required parameters: token_address, symbol, and chain are required",
            }

        try:
            entry = await self.db.add_entry(
                token_address=token_address,
                symbol=symbol.upper(),
                chain=chain.lower(),
            )
            return {
                "success": True,
                "message": f"Added {entry.symbol} on {entry.chain} to watchlist",
                "entry": _entry_to_dict(entry),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _tool_remove(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Remove a token from the watchlist."""
        token_address = args.get("token_address")
        symbol = args.get("symbol")
        chain = args.get("chain")

        if not token_address and not symbol:
            return {
                "success": False,
                "error": "Either token_address or symbol must be provided",
            }

        try:
            if token_address:
                removed = await self.db.remove_entry(token_address, chain)
                identifier = token_address[:10] + "..." if len(token_address) > 10 else token_address
            else:
                removed = await self.db.remove_entry_by_symbol(symbol.upper(), chain)
                identifier = symbol.upper()

            if removed:
                return {
                    "success": True,
                    "message": f"Removed {identifier} from watchlist",
                }
            else:
                return {
                    "success": False,
                    "error": f"Token {identifier} not found in watchlist",
                }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _tool_list(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """List all tokens in the watchlist."""
        try:
            entries = await self.db.list_entries()
            return {
                "success": True,
                "count": len(entries),
                "entries": [_entry_to_dict(e) for e in entries],
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _tool_get(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Get a specific token from the watchlist."""
        token_address = args.get("token_address")
        symbol = args.get("symbol")

        if not token_address and not symbol:
            return {
                "success": False,
                "error": "Either token_address or symbol must be provided",
            }

        try:
            entry = await self.db.get_entry(
                token_address=token_address,
                symbol=symbol.upper() if symbol else None,
            )

            if entry:
                return {
                    "success": True,
                    "entry": _entry_to_dict(entry),
                }
            else:
                identifier = token_address or symbol
                return {
                    "success": False,
                    "error": f"Token {identifier} not found in watchlist",
                }
        except Exception as e:
            return {"success": False, "error": str(e)}
