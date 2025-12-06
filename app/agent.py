"""Agentic planner using Gemini native function calling."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import google.generativeai as genai

from app.mcp_client import MCPManager
from app.types import PlannerResult
from app.tool_converter import parse_function_call_name


@dataclass
class ToolCall:
    """Represents a single tool call made by the model."""

    client: str
    method: str
    params: Dict[str, Any]
    result: Optional[Any] = None
    error: Optional[str] = None


@dataclass
class AgenticContext:
    """Tracks state across iterations of the agentic loop."""

    iteration: int = 0
    total_tool_calls: int = 0
    tool_calls: List[ToolCall] = field(default_factory=list)
    tokens_found: List[Dict[str, str]] = field(default_factory=list)


AGENTIC_SYSTEM_PROMPT = """You are a crypto/DeFi assistant that helps users find token and pool information across multiple blockchains.

## Your Capabilities
You can call tools to:
- Search tokens and get prices (dexscreener)
- Get pool/liquidity data across DEXs (dexpaprika)
- Check if tokens are honeypots (honeypot) - ONLY for ethereum, bsc, base chains

## Tool Selection Guide

| User Intent | Primary Tool | Parameters |
|-------------|--------------|------------|
| "search for [token]" | dexscreener_searchPairs | query=token name/symbol |
| "trending tokens" | dexscreener_getLatestBoostedTokens | - |
| "token info for 0x..." | dexscreener_getTokenPairs | tokenAddress |
| "available networks" | dexpaprika_getNetworks | - |
| "dexes on [network]" | dexpaprika_getNetworkDexes | network |
| "top pools on [chain]" | dexpaprika_getNetworkPools | network, orderBy="volume_usd", limit |
| "new pools" | dexpaprika_getNetworkPools | network, orderBy="created_at", limit |
| "pools on dex" | dexpaprika_getDexPools | network, dex, orderBy="volume_usd", limit |
| "pool details" | dexpaprika_getPoolDetails | network, poolAddress |
| "pool ohlcv" | dexpaprika_getPoolOHLCV | network, poolAddress, interval (e.g., 1h/4h/1d), limit |
| "pool transactions" | dexpaprika_getPoolTransactions | network, poolAddress, limit/offset |
| "token details" | dexpaprika_getTokenDetails | network, tokenAddress |
| "token pools" | dexpaprika_getTokenPools | network, tokenAddress, limit/offset |
| "token multi prices" | dexpaprika_getTokenMultiPrices | network, tokens=[addresses] |
| "dexpaprika search" | dexpaprika_search | query |
| "check honeypot" | honeypot_check_honeypot | address, chain (ethereum/bsc/base only) |

## Multi-Step Query Handling
For complex queries like "analyze [token]", break into steps:
1. Search for the token to get address
2. Get token details (price, volume)
3. Get token pools (liquidity info)

For comparison queries (e.g., "compare Uniswap vs SushiSwap"):
1. Get pools/volume for first DEX
2. Get pools/volume for second DEX
3. Present comparison table

For OHLCV data:
1. First find the pool address using search or getTokenPools
2. Then call dexpaprika_getPoolOHLCV with network, poolAddress, interval, limit

For transaction analysis (buy vs sell pressure):
1. Get pool transactions
2. Summarize: count buys vs sells, total buy volume vs sell volume

## Honeypot Detection
- **IMPORTANT**: When displaying token/pool results on ethereum, bsc, or base chains, AUTOMATICALLY call honeypot_check_honeypot for each unique token address before showing results
- Call honeypot checks in parallel for efficiency when showing multiple tokens
- The chain parameter values are: "ethereum", "bsc", "base" (lowercase)
- For tokens on other chains (solana, arbitrum, polygon, etc.): mark as "Unverified" without calling the tool
- If the honeypot check returns an error: mark the token as "Unverified" in your response

## Blockchain Agnostic
- Work with ANY blockchain the user mentions (ethereum, base, solana, arbitrum, fantom, etc.)
- If user doesn't specify a chain, search across all or ask for clarification
- Use the network parameter appropriately for each chain
- When using dexpaprika tools, ensure required params (network, poolAddress or tokenAddress, interval for OHLCV) are present; if missing, ask the user or pick sensible defaults (e.g., interval=1h, limit=10) and state them

## Response Format - USE TABLES

For multiple tokens/pools, use horizontal tables:

| Token | Price | 24h Change | Volume | Safety |
|-------|-------|------------|--------|--------|
| PEPE/WETH | $0.00001234 | +15.2% | $1.2M | ✅ Safe |

For single token details, use a compact vertical format:

| Field | Value |
|-------|-------|
| Token | PEPE |
| Address | 0x6982508... |
| Chain | ethereum |
| Price | $0.00001234 |
| 24h Change | +15.2% |
| Volume | $1.2M |
| Safety | ✅ Safe |
| DexScreener | [View](https://dexscreener.com/...) |

For OHLCV data, use a table format:

| Time | Open | High | Low | Close | Volume |
|------|------|------|-----|-------|--------|
| 2024-01-01 00:00 | $100.50 | $102.30 | $99.80 | $101.20 | $1.5M |

Safety column values:
- ✅ Safe - honeypot check passed (low risk, not a honeypot)
- ⚠️ Risky - honeypot check shows concerns (high taxes, medium/high risk)
- ❌ Honeypot - confirmed honeypot, avoid
- Unverified - chain not supported or check failed

Do NOT include long descriptions - keep rows concise and show full contract addresses.

## Guidelines
1. Call tools to get real data - don't make up prices or stats
2. Format numbers nicely (use K, M, B suffixes)
3. Include relevant links when available
4. If a tool fails, explain what happened and suggest alternatives
5. Be concise but informative
6. Never let a honeypot check failure block your main response - just mark as Unverified
"""

# Type alias for log callback
LogCallback = Callable[[str, str, Optional[Dict[str, Any]]], None]


class AgenticPlanner:
    """Gemini-based agentic planner with native function calling."""

    def __init__(
        self,
        api_key: str,
        mcp_manager: MCPManager,
        model_name: str = "gemini-2.5-flash",
        max_iterations: int = 8,
        max_tool_calls: int = 30,
        timeout_seconds: int = 90,
        verbose: bool = False,
        log_callback: Optional[LogCallback] = None,
    ) -> None:
        self.mcp_manager = mcp_manager
        self.model_name = model_name
        self.max_iterations = max_iterations
        self.max_tool_calls = max_tool_calls
        self.timeout_seconds = timeout_seconds
        self.verbose = verbose
        self.log_callback = log_callback

        genai.configure(api_key=api_key)

        # Get tools from MCP servers
        self.gemini_tools = mcp_manager.get_gemini_functions()

        # Create model with tools
        self.model = genai.GenerativeModel(
            model_name=model_name,
            tools=self.gemini_tools,
            system_instruction=AGENTIC_SYSTEM_PROMPT,
        )

    def _log(self, level: str, message: str, data: Optional[Dict[str, Any]] = None) -> None:
        """Log a message if verbose mode is enabled."""
        if self.verbose and self.log_callback:
            self.log_callback(level, message, data)

    async def run(
        self, message: str, context: Optional[Dict[str, Any]] = None
    ) -> PlannerResult:
        """Execute a query using the agentic loop."""
        context = context or {}
        agentic_ctx = AgenticContext()

        self._log("info", f"Starting query: {message}")
        self._log("debug", f"Model: {self.model_name}, Tools: {len(self.gemini_tools)}")

        # Build conversation history
        history = context.get("conversation_history", [])
        chat = self.model.start_chat(history=self._convert_history(history))

        try:
            return await asyncio.wait_for(
                self._agentic_loop(chat, message, agentic_ctx),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError:
            return self._build_timeout_result(agentic_ctx)
        except Exception as e:
            self._log("error", f"Query failed: {str(e)}")
            return PlannerResult(message=f"Error: {str(e)}")

    async def _agentic_loop(
        self,
        chat: genai.ChatSession,
        message: str,
        ctx: AgenticContext,
    ) -> PlannerResult:
        """Main agentic reasoning loop."""
        response = chat.send_message(message)

        while ctx.iteration < self.max_iterations:
            ctx.iteration += 1
            self._log("info", f"Iteration {ctx.iteration}/{self.max_iterations}")

            # Check if model wants to call tools
            function_calls = self._extract_function_calls(response)

            if not function_calls:
                # No more tool calls - return final response
                self._log("info", f"Complete. Total tool calls: {ctx.total_tool_calls}")
                return PlannerResult(
                    message=self._extract_text(response),
                    tokens=ctx.tokens_found,
                )

            self._log("info", f"Tool calls requested: {len(function_calls)}")
            for fc in function_calls:
                self._log("tool", f"→ {fc['name']}", {"args": fc["args"]})

            # Check tool call limits
            if ctx.total_tool_calls + len(function_calls) > self.max_tool_calls:
                return self._build_limit_result(ctx, "tool call limit")

            # Execute tool calls in parallel
            tool_results = await self._execute_tool_calls(function_calls, ctx)

            # Send results back to model
            response = chat.send_message(tool_results)

        return self._build_limit_result(ctx, "iteration limit")

    def _extract_function_calls(self, response: Any) -> List[Dict[str, Any]]:
        """Extract function calls from Gemini response."""
        calls = []
        for candidate in response.candidates:
            for part in candidate.content.parts:
                if hasattr(part, "function_call") and part.function_call:
                    fc = part.function_call
                    calls.append({
                        "name": fc.name,
                        "args": dict(fc.args) if fc.args else {},
                    })
        return calls

    def _extract_text(self, response: Any) -> str:
        """Extract text from Gemini response."""
        texts = []
        for candidate in response.candidates:
            for part in candidate.content.parts:
                if hasattr(part, "text") and part.text:
                    texts.append(part.text)
        return "\n".join(texts) if texts else "No response generated."

    async def _execute_tool_calls(
        self, function_calls: List[Dict[str, Any]], ctx: AgenticContext
    ) -> List[genai.protos.Part]:
        """Execute tool calls and return results for Gemini."""
        tasks = []
        for fc in function_calls:
            tasks.append(self._execute_single_tool(fc, ctx))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Build response parts
        parts = []
        for fc, result in zip(function_calls, results):
            if isinstance(result, Exception):
                response_data = {"error": str(result)}
            else:
                response_data = result

            parts.append(
                genai.protos.Part(
                    function_response=genai.protos.FunctionResponse(
                        name=fc["name"],
                        response={"result": response_data},
                    )
                )
            )

        return parts

    async def _execute_single_tool(
        self, fc: Dict[str, Any], ctx: AgenticContext
    ) -> Any:
        """Execute a single tool call."""
        name = fc["name"]
        args = fc["args"]

        client_name, method = parse_function_call_name(name)
        client = self.mcp_manager.get_client(client_name)

        if not client:
            raise ValueError(f"Unknown MCP client: {client_name}")

        ctx.total_tool_calls += 1

        tool_call = ToolCall(
            client=client_name,
            method=method,
            params=args,
        )
        ctx.tool_calls.append(tool_call)

        try:
            result = await client.call_tool(method, args)

            tool_call.result = result

            # Log success
            result_preview = self._preview_result(result)
            self._log("tool", f"✓ {name}", {"result_preview": result_preview})

            # Extract tokens for context
            self._extract_tokens(result, ctx)

            return result
        except Exception as e:
            tool_call.error = str(e)
            self._log("error", f"✗ {name}: {str(e)}")
            raise

    def _preview_result(self, result: Any) -> str:
        """Create a short preview of a result for logging."""
        if isinstance(result, dict):
            if "pairs" in result:
                return f"{len(result['pairs'])} pairs"
            if "pools" in result:
                return f"{len(result['pools'])} pools"
            keys = list(result.keys())[:3]
            return f"dict with keys: {keys}"
        if isinstance(result, list):
            return f"list with {len(result)} items"
        return str(result)[:50]

    def _extract_tokens(self, result: Any, ctx: AgenticContext) -> None:
        """Extract token info from results for context tracking."""
        if not isinstance(result, dict):
            return

        # From dexscreener pairs
        pairs = result.get("pairs", [])
        for pair in pairs[:5]:
            base = pair.get("baseToken", {})
            if base.get("address") and base.get("symbol"):
                ctx.tokens_found.append({
                    "address": base["address"],
                    "symbol": base["symbol"],
                    "chainId": pair.get("chainId", "unknown"),
                })

        # From dexpaprika pools
        pools = result.get("pools", [])
        for pool in pools[:5]:
            tokens = pool.get("tokens", [])
            for token in tokens:
                if token.get("id") and token.get("symbol"):
                    ctx.tokens_found.append({
                        "address": token["id"],
                        "symbol": token["symbol"],
                        "chainId": pool.get("chain") or pool.get("network", "unknown"),
                    })

    def _convert_history(
        self, history: List[Dict[str, str]]
    ) -> List[genai.protos.Content]:
        """Convert conversation history to Gemini format."""
        contents = []
        for msg in history:
            role = "user" if msg.get("role") == "user" else "model"
            contents.append(
                genai.protos.Content(
                    role=role,
                    parts=[genai.protos.Part(text=msg.get("content", ""))],
                )
            )
        return contents

    def _build_timeout_result(self, ctx: AgenticContext) -> PlannerResult:
        """Build result when timeout occurs."""
        lines = ["⏱️ Request timed out."]
        if ctx.tool_calls:
            lines.append(f"\nCompleted {len(ctx.tool_calls)} tool calls before timeout.")
        return PlannerResult(message="\n".join(lines), tokens=ctx.tokens_found)

    def _build_limit_result(self, ctx: AgenticContext, reason: str) -> PlannerResult:
        """Build result when limits are reached."""
        lines = [f"⚠️ Reached {reason}."]
        if ctx.tool_calls:
            lines.append(f"\nCompleted {len(ctx.tool_calls)} tool calls.")
        return PlannerResult(message="\n".join(lines), tokens=ctx.tokens_found)
