"""Portfolio token discovery engine with deterministic pre-filter and AI scoring."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

from google import genai
from google.genai import types

if TYPE_CHECKING:
    from app.mcp_client import MCPManager
    from app.database import Database

logger = logging.getLogger(__name__)

LogCallback = Callable[[str, str, Optional[Dict[str, Any]]], None]


@dataclass
class DiscoveryCandidate:
    """A token candidate that passed deterministic filters."""

    token_address: str
    symbol: str
    chain: str
    price_usd: float
    volume_24h: float
    liquidity_usd: float
    market_cap_usd: float = 0.0
    price_change_24h: float = 0.0
    safety_status: str = "unknown"
    safety_score: Optional[float] = None
    momentum_score: float = 0.0
    reasoning: str = ""
    buy_decision: Optional[bool] = None


DECISION_SYSTEM_PROMPT = """You are an autonomous crypto investment analyst deciding whether to buy a Solana token for a live trading portfolio.

## Your Job
1. Review the candidate data provided.
2. Use the available tools to fetch any additional information you need (deeper pool data, safety re-check, volume trends).
3. Make a definitive buy or no-buy decision.

## Available Tools
- **dexscreener** — search pairs, get token pools, trending data
- **rugcheck** — Solana token safety summary

## Decision Criteria
- **Buy** if: strong volume surge (volume/liquidity ratio > 1.5), positive price momentum, adequate liquidity (>$25k), safe or only mildly risky rugcheck status.
- **No-buy** if: negative price momentum, low volume relative to liquidity, dangerous rugcheck risks, or insufficient data to confirm safety.

## CRITICAL: Final Response Format
When you have finished investigating, you MUST end your response with ONLY this JSON block and nothing else after it:
```json
{
  "buy": true,
  "reasoning": "One sentence explaining the decision"
}
```

Use `"buy": false` to reject. Keep reasoning to one sentence.
"""


class PortfolioDiscovery:
    """Hybrid discovery: deterministic pre-filter → AI scoring."""

    def __init__(
        self,
        mcp_manager: "MCPManager",
        api_key: str,
        model_name: str = "gemini-2.5-flash",
        min_volume_usd: float = 50000.0,
        min_liquidity_usd: float = 25000.0,
        min_market_cap_usd: float = 250000.0,
        min_token_age_hours: float = 4.0,
        min_momentum_score: float = 50.0,
        chain: str = "solana",
        verbose: bool = False,
        log_callback: Optional[LogCallback] = None,
    ) -> None:
        self.mcp_manager = mcp_manager
        self.api_key = api_key
        self.model_name = model_name
        self.min_volume_usd = min_volume_usd
        self.min_liquidity_usd = min_liquidity_usd
        self.min_market_cap_usd = min_market_cap_usd
        self.min_token_age_hours = min_token_age_hours
        self.min_momentum_score = min_momentum_score
        self.chain = chain
        self.verbose = verbose
        self.log_callback = log_callback

    def _log(self, level: str, message: str, data: Optional[Dict[str, Any]] = None) -> None:
        if self.verbose and self.log_callback:
            self.log_callback(level, message, data)

    async def discover(
        self,
        db: "Database",
        max_candidates: int = 5,
    ) -> List[DiscoveryCandidate]:
        """Run full discovery pipeline: scan → filter → safety → AI decision."""
        # Step 1: Scan trending tokens via DexScreener
        raw_pairs = await self._scan_trending()
        if not raw_pairs:
            self._log("info", "No trending pairs found")
            return []
        self._log("info", f"Scanned {len(raw_pairs)} trending pairs")

        # Step 2: Apply deterministic filters
        filtered = self._apply_filters(raw_pairs)
        if not filtered:
            self._log("info", "No candidates passed filters")
            return []
        self._log("info", f"{len(filtered)} candidates passed filters")

        # Step 3: Exclude already-held tokens
        filtered = await self._exclude_held_tokens(filtered, db)
        if not filtered:
            self._log("info", "All candidates already held")
            return []

        # Step 4: Safety check via rugcheck
        safe_candidates = await self._safety_check(filtered)
        if not safe_candidates:
            self._log("info", "No candidates passed safety checks")
            return []
        self._log("info", f"{len(safe_candidates)} candidates passed safety")

        # Step 5: Per-candidate agentic buy decision
        approved: List[DiscoveryCandidate] = []
        for candidate in safe_candidates:
            if len(approved) >= max_candidates:
                break
            # Populate heuristic score for reference regardless of AI outcome
            candidate.momentum_score = self._heuristic_score(candidate)
            buy, reasoning = await self._ai_decide(candidate)
            candidate.buy_decision = buy
            candidate.reasoning = reasoning
            self._log(
                "info",
                f"Decision: {candidate.symbol} → {'BUY' if buy else 'SKIP'} "
                f"(heuristic={candidate.momentum_score:.0f}, "
                f"vol=${candidate.volume_24h:,.0f} liq=${candidate.liquidity_usd:,.0f} "
                f"chg={candidate.price_change_24h:+.1f}%) — {reasoning}",
            )
            if buy:
                approved.append(candidate)

        if not approved:
            self._log("info", "No candidates approved by AI decision step")

        return approved

    async def _scan_trending(self) -> List[Dict[str, Any]]:
        """Fetch trending tokens from DexScreener using boosted + search endpoints."""
        client = self.mcp_manager.get_client("dexscreener")
        if not client:
            self._log("error", "DexScreener MCP client not available")
            return []

        all_pairs: List[Dict[str, Any]] = []
        seen_addresses: set[str] = set()

        def _add_pairs(pairs: List[Dict[str, Any]]) -> int:
            added = 0
            for pair in pairs:
                addr = (pair.get("baseToken") or {}).get("address", "").lower()
                if not addr or addr in seen_addresses:
                    continue
                seen_addresses.add(addr)
                all_pairs.append(pair)
                added += 1
            return added

        # Primary: boosted/trending token endpoints → fetch pair data per token
        boosted_tokens = await self._fetch_boosted_tokens(client)
        if boosted_tokens:
            boosted_pairs = await self._fetch_pairs_for_tokens(client, boosted_tokens)
            count = _add_pairs(boosted_pairs)
            self._log("info", f"Boosted tokens: {len(boosted_tokens)} found, {count} pairs added")

        # Secondary: text search for additional breadth
        queries = ["trending solana", "solana"]
        for query in queries:
            try:
                result = await client.call_tool("search_pairs", {"query": query})
                pairs = self._extract_pairs(result)
                count = _add_pairs(pairs)
                self._log("info", f"Search '{query}': {len(pairs)} results, {count} new pairs added")
            except Exception as exc:
                self._log("warning", f"DexScreener query '{query}' failed: {exc}")

        return all_pairs

    async def _fetch_boosted_tokens(self, client: Any) -> List[Dict[str, Any]]:
        """Fetch boosted token addresses from DexScreener trending endpoints."""
        endpoints = ["get_top_boosted_tokens", "get_latest_boosted_tokens"]

        async def _call(endpoint: str) -> List[Dict[str, Any]]:
            try:
                result = await client.call_tool(endpoint, {})
                return self._extract_boosted_tokens(result)
            except Exception as exc:
                self._log("warning", f"{endpoint} failed: {exc}")
                return []

        results = await asyncio.gather(*[_call(ep) for ep in endpoints])

        tokens: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for items in results:
            for item in items:
                chain = (item.get("chainId") or "").lower()
                addr = (item.get("tokenAddress") or "").lower()
                if chain != self.chain or not addr or addr in seen:
                    continue
                seen.add(addr)
                tokens.append(item)

        return tokens

    @staticmethod
    def _extract_boosted_tokens(result: Any) -> List[Dict[str, Any]]:
        """Extract token entries from boosted token response."""
        if isinstance(result, list):
            return [t for t in result if isinstance(t, dict) and t.get("tokenAddress")]
        if isinstance(result, dict):
            # Some responses wrap in a key
            for key in ("tokens", "data", "results"):
                items = result.get(key)
                if isinstance(items, list):
                    return [t for t in items if isinstance(t, dict) and t.get("tokenAddress")]
        return []

    async def _fetch_pairs_for_tokens(
        self, client: Any, tokens: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Fetch pair data for a list of boosted tokens via get_token_pools."""

        async def _fetch_one(token: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            chain = token.get("chainId", self.chain)
            addr = token.get("tokenAddress", "")
            if not addr:
                return None
            try:
                result = await client.call_tool(
                    "get_token_pools",
                    {"chainId": chain, "tokenAddress": addr},
                )
                pool_pairs = self._extract_pairs(result)
                if pool_pairs:
                    return max(
                        pool_pairs,
                        key=lambda p: float(
                            (p.get("liquidity") or {}).get("usd", 0)
                            if isinstance(p.get("liquidity"), dict) else 0
                        ),
                    )
            except Exception as exc:
                self._log("warning", f"get_token_pools failed for {addr[:12]}…: {exc}")
            return None

        results = await asyncio.gather(*[_fetch_one(t) for t in tokens])
        return [p for p in results if p is not None]

    @staticmethod
    def _extract_pairs(result: Any) -> List[Dict[str, Any]]:
        """Extract pair dicts from DexScreener response."""
        if isinstance(result, list):
            return [p for p in result if isinstance(p, dict)]
        if isinstance(result, dict):
            pairs = result.get("pairs", result.get("results", []))
            if isinstance(pairs, list):
                return [p for p in pairs if isinstance(p, dict)]
        return []

    def _apply_filters(self, pairs: List[Dict[str, Any]]) -> List[DiscoveryCandidate]:
        """Apply deterministic volume/liquidity/chain/age filters."""
        candidates: List[DiscoveryCandidate] = []
        seen_addresses: set[str] = set()
        chain_counts: Dict[str, int] = {}
        rejected_volume = 0
        rejected_liquidity = 0
        rejected_market_cap = 0
        rejected_age = 0
        now_ms = time.time() * 1000

        for pair in pairs:
            chain_id = (pair.get("chainId") or "").lower()
            chain_counts[chain_id] = chain_counts.get(chain_id, 0) + 1
            if chain_id != self.chain:
                continue

            base_token = pair.get("baseToken", {})
            address = base_token.get("address", "")
            symbol = base_token.get("symbol", "")
            if not address or not symbol:
                continue

            addr_lower = address.lower()
            if addr_lower in seen_addresses:
                continue
            seen_addresses.add(addr_lower)

            try:
                price = float(pair.get("priceUsd", 0))
                volume_24h = float(pair.get("volume", {}).get("h24", 0))
                liquidity_data = pair.get("liquidity", {})
                liquidity = float(liquidity_data.get("usd", 0)) if isinstance(liquidity_data, dict) else 0.0
                price_change = float(pair.get("priceChange", {}).get("h24", 0))
                market_cap_usd = float(pair.get("marketCap", pair.get("fdv", 0)))
                pair_created_at_ms = float(pair.get("pairCreatedAt") or 0)
            except (TypeError, ValueError):
                continue

            if volume_24h < self.min_volume_usd:
                rejected_volume += 1
                continue
            if liquidity < self.min_liquidity_usd:
                rejected_liquidity += 1
                continue
            if market_cap_usd < self.min_market_cap_usd:
                rejected_market_cap += 1
                continue
            if price <= 0:
                continue

            if self.min_token_age_hours > 0 and pair_created_at_ms > 0:
                age_hours = (now_ms - pair_created_at_ms) / 1_000 / 3_600
                if age_hours < self.min_token_age_hours:
                    rejected_age += 1
                    continue

            candidates.append(DiscoveryCandidate(
                token_address=address,
                symbol=symbol,
                chain=self.chain,
                price_usd=price,
                volume_24h=volume_24h,
                liquidity_usd=liquidity,
                market_cap_usd=market_cap_usd,
                price_change_24h=price_change,
            ))

        self._log(
            "info",
            f"Filter breakdown: chains={chain_counts}, "
            f"rejected_volume={rejected_volume}, rejected_liquidity={rejected_liquidity}, "
            f"rejected_market_cap={rejected_market_cap}, rejected_age={rejected_age}, "
            f"passed={len(candidates)}",
        )

        return candidates

    async def _exclude_held_tokens(
        self,
        candidates: List[DiscoveryCandidate],
        db: "Database",
    ) -> List[DiscoveryCandidate]:
        """Remove candidates that already have open portfolio positions."""
        result: List[DiscoveryCandidate] = []
        for c in candidates:
            existing = await db.get_open_portfolio_position(c.token_address, c.chain)
            if existing is None:
                result.append(c)
        return result

    async def _safety_check(
        self, candidates: List[DiscoveryCandidate]
    ) -> List[DiscoveryCandidate]:
        """Run rugcheck safety analysis on each candidate."""
        client = self.mcp_manager.get_client("rugcheck")
        if not client:
            self._log("warning", "Rugcheck not available — skipping safety checks")
            for c in candidates:
                c.safety_status = "unverified"
            return candidates

        safe: List[DiscoveryCandidate] = []
        for candidate in candidates:
            try:
                result = await client.call_tool(
                    "get_token_summary",
                    {"token_address": candidate.token_address},
                )
                status, score = self._parse_safety(result)
                candidate.safety_status = status
                candidate.safety_score = score

                if status in ("Safe", "Risky", "unverified"):
                    safe.append(candidate)
                else:
                    self._log(
                        "info",
                        f"Rejected {candidate.symbol}: safety={status}",
                    )
            except Exception as exc:
                self._log("warning", f"Safety check failed for {candidate.symbol}: {exc}")
                candidate.safety_status = "unverified"
                safe.append(candidate)

        return safe

    @staticmethod
    def _parse_safety(result: Any) -> tuple[str, Optional[float]]:
        """Parse rugcheck response into (status, score)."""
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except (json.JSONDecodeError, ValueError):
                return "unverified", None

        if isinstance(result, list) and result and isinstance(result[0], dict):
            result = result[0]

        if not isinstance(result, dict):
            return "unverified", None

        score = result.get("score_normalised", result.get("score", 0))
        risks = result.get("risks", [])

        try:
            score = float(score)
        except (TypeError, ValueError):
            score = 0.0

        if score <= 500 and not risks:
            return "Safe", score
        elif score <= 2000 or len(risks) <= 2:
            return "Risky", score
        else:
            return "Dangerous", score

    async def _ai_decide(self, candidate: DiscoveryCandidate) -> tuple[bool, str]:
        """Run a per-candidate agentic loop to make a binary buy/no-buy decision.

        The model may call MCP tools to gather additional data before deciding.
        Returns (buy: bool, reasoning: str).
        Falls back to heuristic scoring if the agentic call fails or times out.
        """
        _MAX_ITERATIONS = 4
        _TIMEOUT_SECONDS = 45

        initial_message = (
            f"Should I buy {candidate.symbol} ({candidate.token_address}) on Solana?\n\n"
            f"Current data:\n"
            f"- Price: ${candidate.price_usd}\n"
            f"- 24h Volume: ${candidate.volume_24h:,.0f}\n"
            f"- Liquidity: ${candidate.liquidity_usd:,.0f}\n"
            f"- Market Cap: ${candidate.market_cap_usd:,.0f}\n"
            f"- 24h Price Change: {candidate.price_change_24h:+.2f}%\n"
            f"- Safety: {candidate.safety_status}"
            + (f" (score {candidate.safety_score:.0f})" if candidate.safety_score is not None else "")
        )

        try:
            return await asyncio.wait_for(
                self._run_decision_loop(candidate, initial_message, _MAX_ITERATIONS),
                timeout=_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            self._log("warning", f"AI decision timed out for {candidate.symbol} — using heuristic fallback")
        except Exception as exc:
            self._log("error", f"AI decision failed for {candidate.symbol}: {exc} — using heuristic fallback")

        # Heuristic fallback
        score = self._heuristic_score(candidate)
        buy = score >= self.min_momentum_score
        return buy, f"Heuristic fallback (score={score:.0f}): {'buy' if buy else 'skip'}"

    async def _run_decision_loop(
        self,
        candidate: DiscoveryCandidate,
        initial_message: str,
        max_iterations: int,
    ) -> tuple[bool, str]:
        """Inner agentic loop for _ai_decide."""
        from app.tool_converter import parse_function_call_name

        gemini_client = genai.Client(api_key=self.api_key)
        tools = self.mcp_manager.get_gemini_functions_for(["dexscreener", "rugcheck"])
        tool_config = [types.Tool(functionDeclarations=tools)] if tools else None

        chat = gemini_client.chats.create(
            model=self.model_name,
            config=types.GenerateContentConfig(
                system_instruction=DECISION_SYSTEM_PROMPT,
                tools=tool_config,
            ),
        )

        response = chat.send_message(initial_message)

        for _ in range(max_iterations):
            # Collect any function calls in this response
            function_calls = []
            if response.candidates:
                for part in response.candidates[0].content.parts:
                    if hasattr(part, "function_call") and part.function_call:
                        function_calls.append(part.function_call)

            if not function_calls:
                # No more tool calls — extract the final decision
                text = ""
                if response.candidates:
                    for part in response.candidates[0].content.parts:
                        if hasattr(part, "text") and part.text:
                            text += part.text
                return self._parse_decision(text)

            # Execute tool calls and feed results back
            tool_results = []
            for fc in function_calls:
                client_name, method = parse_function_call_name(fc.name)
                args = dict(fc.args) if fc.args else {}
                try:
                    mcp_client = self.mcp_manager.get_client(client_name)
                    if mcp_client:
                        result = await mcp_client.call_tool(method, args)
                        result_str = json.dumps(result) if not isinstance(result, str) else result
                    else:
                        result_str = f"Client '{client_name}' not available"
                except Exception as exc:
                    result_str = f"Tool error: {exc}"

                tool_results.append(
                    types.Part.from_function_response(
                        name=fc.name,
                        response={"result": result_str},
                    )
                )
                self._log("debug", f"Tool call: {fc.name}({args}) → {result_str[:120]}…")

            response = chat.send_message(tool_results)

        # Exhausted iterations — parse whatever we have
        text = ""
        if response.candidates:
            for part in response.candidates[0].content.parts:
                if hasattr(part, "text") and part.text:
                    text += part.text
        return self._parse_decision(text)

    @staticmethod
    def _parse_decision(text: str) -> tuple[bool, str]:
        """Parse the model's final JSON decision block.

        Expected format (last JSON block in text):
          { "buy": true/false, "reasoning": "..." }
        """
        # Find the last JSON block in the text
        matches = list(re.finditer(r'\{[^{}]*"buy"[^{}]*\}', text, re.DOTALL))
        for match in reversed(matches):
            try:
                data = json.loads(match.group())
                buy = bool(data.get("buy", False))
                reasoning = str(data.get("reasoning", "")).strip()
                return buy, reasoning
            except (json.JSONDecodeError, ValueError):
                continue

        # Fallback: look for bare true/false near "buy" keyword
        lower = text.lower()
        if '"buy": true' in lower or '"buy":true' in lower:
            return True, "Decision: buy (parsed from text)"
        if '"buy": false' in lower or '"buy":false' in lower:
            return False, "Decision: skip (parsed from text)"

        # Cannot parse — conservative default
        return False, "AI response unparseable — conservative skip"

    @staticmethod
    def _heuristic_score(candidate: DiscoveryCandidate) -> float:
        """Simple fallback score when AI is unavailable."""
        score = 0.0
        # Volume/liquidity ratio (0-30)
        if candidate.liquidity_usd > 0:
            vol_ratio = candidate.volume_24h / candidate.liquidity_usd
            score += min(30.0, vol_ratio * 10)
        # Price momentum (0-30)
        if candidate.price_change_24h > 0:
            score += min(30.0, candidate.price_change_24h)
        # Liquidity depth (0-20)
        if candidate.liquidity_usd >= 50000:
            score += 20.0
        elif candidate.liquidity_usd >= 10000:
            score += 10.0
        # Safety (0-20)
        if candidate.safety_status == "Safe":
            score += 20.0
        elif candidate.safety_status in ("Risky", "unverified"):
            score += 10.0
        return min(100.0, score)
