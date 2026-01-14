"""Autonomous watchlist management agent using Gemini."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

from google import genai
from google.genai import types

if TYPE_CHECKING:
    from app.mcp_client import MCPManager
    from app.watchlist import WatchlistDB, WatchlistEntry

# Type alias for log callback
LogCallback = Callable[[str, str, Optional[Dict[str, Any]]], None]


@dataclass
class TokenCandidate:
    """Represents a token candidate for the watchlist."""

    token_address: str
    symbol: str
    chain: str
    current_price: float
    price_change_24h: float
    volume_24h: float
    liquidity: float
    momentum_score: float
    alert_above: float
    alert_below: float
    reasoning: str


@dataclass
class WatchlistReview:
    """Represents a review decision for an existing watchlist entry."""

    entry_id: int
    token_address: str
    symbol: str
    action: str  # "keep", "update", "remove"
    new_alert_above: Optional[float] = None
    new_alert_below: Optional[float] = None
    new_momentum_score: Optional[float] = None
    reasoning: str = ""


@dataclass
class AutonomousCycleResult:
    """Result of an autonomous cycle run."""

    timestamp: datetime
    tokens_added: List[TokenCandidate] = field(default_factory=list)
    tokens_removed: List[str] = field(default_factory=list)
    tokens_updated: List[WatchlistReview] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    summary: str = ""


# System prompt for token discovery
DISCOVERY_SYSTEM_PROMPT = """You are an autonomous crypto trading assistant specialized in finding Solana tokens with upward momentum potential.

## Your Task
Find and analyze Solana tokens that show strong potential for price appreciation. You must use the available tools to gather real data.

## Analysis Criteria
Evaluate tokens based on these momentum indicators:
1. **Volume Surge**: 24h volume significantly higher than average
2. **Price Momentum**: Positive price change over 1h, 6h, and 24h periods
3. **Liquidity**: Sufficient liquidity (>$5,000 USD) for trading
4. **Safety**: Must pass rugcheck safety analysis

## Momentum Score Calculation
Score tokens from 0-100 based on:
- Volume increase: 0-30 points
- Price momentum: 0-30 points  
- Liquidity depth: 0-20 points
- Safety rating: 0-20 points

## Response Format
After gathering data, respond with a JSON object containing your findings:
```json
{
  "candidates": [
    {
      "token_address": "address",
      "symbol": "SYMBOL",
      "chain": "solana",
      "current_price": 0.001,
      "price_change_24h": 15.5,
      "volume_24h": 500000,
      "liquidity": 50000,
      "momentum_score": 75,
      "alert_above": 0.0011,
      "alert_below": 0.00095,
      "reasoning": "Strong volume surge, positive momentum across all timeframes"
    }
  ],
  "summary": "Found X tokens with strong momentum indicators"
}
```

## Price Trigger Calculation
- alert_above: Set 10% above current price as take-profit target
- alert_below: Set 5% below current price as stop-loss

## Important Rules
1. ALWAYS call tools to get real data - never make up prices or stats
2. Only include tokens that pass safety checks
3. Maximum 5 candidates per discovery cycle
4. Focus ONLY on Solana chain tokens
5. Prioritize tokens with recent volume spikes
"""

# System prompt for watchlist review
REVIEW_SYSTEM_PROMPT = """You are an autonomous crypto trading assistant reviewing your current watchlist positions.

## Your Task
Review each token in the watchlist and decide whether to KEEP, UPDATE triggers, or REMOVE the position.

## Current Watchlist
{watchlist_data}

## Review Criteria
For each token, analyze:
1. Has the momentum thesis changed?
2. Is the price moving in our favor or against?
3. Should we adjust stop-loss (trailing stop)?
4. Should we take profits or cut losses?

## Decision Framework
- **KEEP**: Momentum still strong, price moving favorably
- **UPDATE**: Adjust triggers based on price movement
  - If price up >5%: Raise stop-loss to lock in gains (trailing stop)
  - If momentum slowing: Tighten stop-loss
- **REMOVE**: Momentum thesis broken, better opportunities elsewhere

## Response Format
Respond with a JSON object:
```json
{
  "reviews": [
    {
      "entry_id": 1,
      "token_address": "address",
      "symbol": "SYMBOL",
      "action": "keep|update|remove",
      "new_alert_above": 0.0012,
      "new_alert_below": 0.00098,
      "new_momentum_score": 70,
      "reasoning": "Price up 8%, raising stop-loss to lock gains"
    }
  ],
  "replacements_needed": 2,
  "summary": "Reviewed 5 tokens: 3 keep, 1 update, 1 remove"
}
```

## Important Rules
1. Call tools to get CURRENT prices for each token
2. Compare current price to entry price (last_price) and triggers
3. Be disciplined about stop-losses
4. Document reasoning for each decision
"""


class AutonomousWatchlistAgent:
    """Agent for autonomous watchlist management."""

    def __init__(
        self,
        api_key: str,
        mcp_manager: "MCPManager",
        model_name: str = "gemini-2.5-flash",
        max_tokens: int = 5,
        min_volume_usd: float = 10000,
        min_liquidity_usd: float = 5000,
        verbose: bool = False,
        log_callback: Optional[LogCallback] = None,
    ) -> None:
        self.mcp_manager = mcp_manager
        self.model_name = model_name
        self.max_tokens = max_tokens
        self.min_volume_usd = min_volume_usd
        self.min_liquidity_usd = min_liquidity_usd
        self.verbose = verbose
        self.log_callback = log_callback

        # Initialize the client
        self.client = genai.Client(api_key=api_key)

        # Get tools from MCP servers
        self.gemini_tools = mcp_manager.get_gemini_functions()

    def _log(self, level: str, message: str, data: Optional[Dict[str, Any]] = None) -> None:
        """Log a message if verbose mode is enabled."""
        if self.verbose and self.log_callback:
            self.log_callback(level, message, data)

    async def discover_tokens(self) -> List[TokenCandidate]:
        """Discover new Solana tokens with upward momentum potential."""
        self._log("info", "Starting token discovery for Solana")

        # Build discovery prompt with criteria
        discovery_prompt = f"""Search for trending Solana tokens with strong upward momentum.

Requirements:
- Minimum 24h volume: ${self.min_volume_usd:,.0f} USD
- Minimum liquidity: ${self.min_liquidity_usd:,.0f} USD
- Focus on tokens with recent volume spikes and positive price momentum
- Check safety via rugcheck for each candidate
- Return up to {self.max_tokens} best candidates

Steps:
1. Search for trending tokens on Solana using dexscreener
2. Get detailed info for promising tokens
3. Run rugcheck safety analysis on each
4. Calculate momentum scores and price triggers
5. Return findings as JSON
"""

        try:
            response = await self._run_agent(DISCOVERY_SYSTEM_PROMPT, discovery_prompt)
            candidates = self._parse_discovery_response(response)
            self._log("info", f"Discovered {len(candidates)} token candidates")
            return candidates
        except Exception as e:
            self._log("error", f"Discovery failed: {str(e)}")
            return []

    async def review_watchlist(
        self, entries: List["WatchlistEntry"]
    ) -> List[WatchlistReview]:
        """Review existing watchlist entries and decide on actions."""
        if not entries:
            return []

        self._log("info", f"Reviewing {len(entries)} watchlist entries")

        # Format watchlist data for prompt
        watchlist_data = self._format_watchlist_for_review(entries)
        system_prompt = REVIEW_SYSTEM_PROMPT.format(watchlist_data=watchlist_data)

        review_prompt = """Review each token in the current watchlist:

1. Get CURRENT prices for each token using dexscreener
2. Compare current price to the last_price and alert triggers
3. Decide: KEEP (momentum still valid), UPDATE (adjust triggers), or REMOVE (thesis broken)
4. For tokens marked REMOVE, I will search for replacements in a separate step

Return your analysis as JSON with the reviews array.
"""

        try:
            response = await self._run_agent(system_prompt, review_prompt)
            reviews = self._parse_review_response(response, entries)
            self._log("info", f"Completed review: {len(reviews)} decisions")
            return reviews
        except Exception as e:
            self._log("error", f"Review failed: {str(e)}")
            return []

    async def _run_agent(self, system_prompt: str, user_prompt: str) -> str:
        """Run the agent with tool calling and return final response."""
        tool_config = None
        if self.gemini_tools:
            tool_config = [types.Tool(functionDeclarations=self.gemini_tools)]

        config = types.GenerateContentConfig(
            systemInstruction=system_prompt,
            tools=tool_config,
        )

        chat = self.client.chats.create(
            model=self.model_name,
            config=config,
        )

        response = chat.send_message(user_prompt)
        iterations = 0
        max_iterations = 10

        while iterations < max_iterations:
            iterations += 1
            function_calls = self._extract_function_calls(response)

            if not function_calls:
                # No more tool calls - return final response
                return self._extract_text(response)

            self._log("info", f"Executing {len(function_calls)} tool calls")

            # Execute tool calls
            tool_results = await self._execute_tool_calls(function_calls)

            # Send results back to model
            response = chat.send_message(tool_results)

        return self._extract_text(response)

    def _extract_function_calls(self, response: Any) -> List[Dict[str, Any]]:
        """Extract function calls from Gemini response."""
        calls = []
        if not response.candidates:
            return calls
        for candidate in response.candidates:
            if not candidate.content or not candidate.content.parts:
                continue
            for part in candidate.content.parts:
                if hasattr(part, "function_call") and part.function_call:
                    fc = part.function_call
                    name = fc.name.strip() if fc.name else ""
                    if name:
                        calls.append({
                            "name": name,
                            "args": dict(fc.args) if fc.args else {},
                        })
        return calls

    def _extract_text(self, response: Any) -> str:
        """Extract text from Gemini response."""
        if not response.candidates:
            return ""
        for candidate in response.candidates:
            if not candidate.content or not candidate.content.parts:
                continue
            for part in candidate.content.parts:
                if hasattr(part, "text") and part.text:
                    return part.text
        return ""

    async def _execute_tool_calls(
        self, function_calls: List[Dict[str, Any]]
    ) -> List[types.Part]:
        """Execute tool calls and return results for Gemini."""
        from app.tool_converter import parse_function_call_name

        tasks = []
        for fc in function_calls:
            tasks.append(self._execute_single_tool(fc))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        parts = []
        for fc, result in zip(function_calls, results):
            if isinstance(result, Exception):
                response_data = {"error": str(result)}
            else:
                response_data = result

            parts.append(
                types.Part.from_function_response(
                    name=fc["name"],
                    response={"result": response_data},
                )
            )

        return parts

    async def _execute_single_tool(self, fc: Dict[str, Any]) -> Any:
        """Execute a single tool call."""
        from app.tool_converter import parse_function_call_name

        name = fc["name"]
        args = fc["args"]

        client_name, method = parse_function_call_name(name)
        client = self.mcp_manager.get_client(client_name)

        if not client:
            raise ValueError(f"Unknown MCP client: {client_name}")

        self._log("tool", f"→ {name}", {"args": args})

        try:
            result = await client.call_tool(method, args)
            self._log("tool", f"✓ {name}")
            return result
        except Exception as e:
            self._log("error", f"✗ {name}: {str(e)}")
            raise

    def _format_watchlist_for_review(self, entries: List["WatchlistEntry"]) -> str:
        """Format watchlist entries for inclusion in review prompt."""
        lines = []
        for entry in entries:
            lines.append(
                f"- ID: {entry.id}, Symbol: {entry.symbol}, Address: {entry.token_address}, "
                f"Chain: {entry.chain}, Last Price: ${entry.last_price or 0:.8f}, "
                f"Alert Above: ${entry.alert_above or 0:.8f}, Alert Below: ${entry.alert_below or 0:.8f}, "
                f"Momentum Score: {entry.momentum_score or 0:.1f}, "
                f"Notes: {entry.review_notes or 'N/A'}"
            )
        return "\n".join(lines)

    def _parse_discovery_response(self, response: str) -> List[TokenCandidate]:
        """Parse the agent's discovery response into TokenCandidate objects."""
        candidates = []

        # Try to extract JSON from response
        json_match = re.search(r'\{[\s\S]*"candidates"[\s\S]*\}', response)
        if not json_match:
            self._log("warning", "No JSON found in discovery response")
            return candidates

        try:
            data = json.loads(json_match.group())
            for item in data.get("candidates", []):
                try:
                    candidate = TokenCandidate(
                        token_address=item.get("token_address", ""),
                        symbol=item.get("symbol", ""),
                        chain=item.get("chain", "solana"),
                        current_price=float(item.get("current_price", 0)),
                        price_change_24h=float(item.get("price_change_24h", 0)),
                        volume_24h=float(item.get("volume_24h", 0)),
                        liquidity=float(item.get("liquidity", 0)),
                        momentum_score=float(item.get("momentum_score", 0)),
                        alert_above=float(item.get("alert_above", 0)),
                        alert_below=float(item.get("alert_below", 0)),
                        reasoning=item.get("reasoning", ""),
                    )
                    if candidate.token_address and candidate.symbol:
                        candidates.append(candidate)
                except (ValueError, TypeError) as e:
                    self._log("warning", f"Failed to parse candidate: {e}")
        except json.JSONDecodeError as e:
            self._log("error", f"Failed to parse discovery JSON: {e}")

        return candidates

    def _parse_review_response(
        self, response: str, entries: List["WatchlistEntry"]
    ) -> List[WatchlistReview]:
        """Parse the agent's review response into WatchlistReview objects."""
        reviews = []
        entry_map = {e.id: e for e in entries}

        # Try to extract JSON from response
        json_match = re.search(r'\{[\s\S]*"reviews"[\s\S]*\}', response)
        if not json_match:
            self._log("warning", "No JSON found in review response")
            return reviews

        try:
            data = json.loads(json_match.group())
            for item in data.get("reviews", []):
                try:
                    entry_id = int(item.get("entry_id", 0))
                    entry = entry_map.get(entry_id)
                    if not entry:
                        continue

                    review = WatchlistReview(
                        entry_id=entry_id,
                        token_address=entry.token_address,
                        symbol=entry.symbol,
                        action=item.get("action", "keep").lower(),
                        new_alert_above=float(item["new_alert_above"]) if item.get("new_alert_above") else None,
                        new_alert_below=float(item["new_alert_below"]) if item.get("new_alert_below") else None,
                        new_momentum_score=float(item["new_momentum_score"]) if item.get("new_momentum_score") else None,
                        reasoning=item.get("reasoning", ""),
                    )
                    if review.action in ("keep", "update", "remove"):
                        reviews.append(review)
                except (ValueError, TypeError) as e:
                    self._log("warning", f"Failed to parse review: {e}")
        except json.JSONDecodeError as e:
            self._log("error", f"Failed to parse review JSON: {e}")

        return reviews

    def calculate_triggers(
        self, current_price: float, take_profit_pct: float = 0.10, stop_loss_pct: float = 0.05
    ) -> tuple[float, float]:
        """Calculate default price triggers based on current price.
        
        Args:
            current_price: Current token price
            take_profit_pct: Percentage above current price for take-profit (default 10%)
            stop_loss_pct: Percentage below current price for stop-loss (default 5%)
            
        Returns:
            Tuple of (alert_above, alert_below)
        """
        alert_above = current_price * (1 + take_profit_pct)
        alert_below = current_price * (1 - stop_loss_pct)
        return alert_above, alert_below

    def calculate_trailing_stop(
        self, current_price: float, entry_price: float, current_stop: float, trail_pct: float = 0.05
    ) -> float:
        """Calculate trailing stop-loss that only moves up.
        
        Args:
            current_price: Current token price
            entry_price: Original entry price
            current_stop: Current stop-loss price
            trail_pct: Trailing percentage below high (default 5%)
            
        Returns:
            New stop-loss price (only increases, never decreases)
        """
        # Calculate new stop based on current price
        new_stop = current_price * (1 - trail_pct)
        
        # Only raise stop, never lower it
        return max(current_stop, new_stop)
