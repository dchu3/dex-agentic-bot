"""Portfolio token discovery engine with deterministic pre-filter and AI scoring."""

from __future__ import annotations

import asyncio
import json
import logging
import re
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
    price_change_24h: float = 0.0
    safety_status: str = "unknown"
    safety_score: Optional[float] = None
    momentum_score: float = 0.0
    reasoning: str = ""


AI_SCORING_PROMPT = """You are a crypto momentum analyst. Score each token candidate based on the provided market data.

## Scoring Criteria (0-100)
- **Volume Surge** (0-30 pts): 24h volume relative to liquidity. Higher ratio = stronger interest.
- **Price Momentum** (0-30 pts): Positive 24h price change indicates upward trend.
- **Liquidity Depth** (0-20 pts): Higher liquidity = easier to enter/exit positions.
- **Safety** (0-20 pts): Tokens that passed safety checks get full marks.

## Response Format
Return ONLY a JSON object:
```json
{
  "scores": [
    {
      "token_address": "address",
      "momentum_score": 75,
      "reasoning": "Strong volume surge at 5x liquidity, 12% price gain, safe"
    }
  ]
}
```

## Rules
1. Score ONLY the candidates provided — do not add new tokens
2. Be conservative: tokens with negative price momentum or low volume should score below 50
3. Keep reasoning concise (one sentence)
"""


class PortfolioDiscovery:
    """Hybrid discovery: deterministic pre-filter → AI scoring."""

    def __init__(
        self,
        mcp_manager: "MCPManager",
        api_key: str,
        model_name: str = "gemini-2.5-flash",
        min_volume_usd: float = 10000.0,
        min_liquidity_usd: float = 5000.0,
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
        """Run full discovery pipeline: scan → filter → safety → AI score."""
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

        # Step 5: AI scoring
        scored = await self._ai_score(safe_candidates)
        scored.sort(key=lambda c: c.momentum_score, reverse=True)

        # Log individual scores for diagnostics
        for c in scored:
            self._log(
                "info",
                f"Score: {c.symbol} = {c.momentum_score:.0f} "
                f"(vol=${c.volume_24h:,.0f} liq=${c.liquidity_usd:,.0f} "
                f"chg={c.price_change_24h:+.1f}%) — {c.reasoning}",
            )

        # Step 6: Filter by minimum score and return top N
        result = [c for c in scored if c.momentum_score >= self.min_momentum_score]
        if scored and not result:
            self._log(
                "info",
                f"All {len(scored)} candidates scored below min_momentum_score={self.min_momentum_score}",
            )
        return result[:max_candidates]

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
        """Apply deterministic volume/liquidity/chain filters."""
        candidates: List[DiscoveryCandidate] = []
        seen_addresses: set[str] = set()
        chain_counts: Dict[str, int] = {}
        rejected_volume = 0
        rejected_liquidity = 0

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
            except (TypeError, ValueError):
                continue

            if volume_24h < self.min_volume_usd:
                rejected_volume += 1
                continue
            if liquidity < self.min_liquidity_usd:
                rejected_liquidity += 1
                continue
            if price <= 0:
                continue

            candidates.append(DiscoveryCandidate(
                token_address=address,
                symbol=symbol,
                chain=self.chain,
                price_usd=price,
                volume_24h=volume_24h,
                liquidity_usd=liquidity,
                price_change_24h=price_change,
            ))

        self._log(
            "info",
            f"Filter breakdown: chains={chain_counts}, "
            f"rejected_volume={rejected_volume}, rejected_liquidity={rejected_liquidity}, "
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

    async def _ai_score(
        self, candidates: List[DiscoveryCandidate]
    ) -> List[DiscoveryCandidate]:
        """Use Gemini to score candidates with momentum reasoning."""
        if not candidates:
            return []

        candidate_data = []
        for c in candidates:
            candidate_data.append({
                "token_address": c.token_address,
                "symbol": c.symbol,
                "price_usd": c.price_usd,
                "volume_24h": c.volume_24h,
                "liquidity_usd": c.liquidity_usd,
                "price_change_24h": c.price_change_24h,
                "safety_status": c.safety_status,
            })

        user_prompt = f"Score these Solana token candidates:\n```json\n{json.dumps(candidate_data, indent=2)}\n```"

        try:
            client = genai.Client(api_key=self.api_key)
            response = client.models.generate_content(
                model=self.model_name,
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=AI_SCORING_PROMPT,
                ),
            )

            text = ""
            if response.candidates:
                for part in response.candidates[0].content.parts:
                    if hasattr(part, "text") and part.text:
                        text = part.text
                        break

            scores_map = self._parse_scores(text)
            for c in candidates:
                entry = scores_map.get(c.token_address.lower())
                if entry:
                    c.momentum_score = entry.get("momentum_score", 0.0)
                    c.reasoning = entry.get("reasoning", "")

        except Exception as exc:
            self._log("error", f"AI scoring failed: {exc}")
            # Fallback: simple heuristic score
            for c in candidates:
                c.momentum_score = self._heuristic_score(c)
                c.reasoning = "AI scoring unavailable — heuristic fallback"

        return candidates

    @staticmethod
    def _parse_scores(text: str) -> Dict[str, Dict[str, Any]]:
        """Parse AI scoring response into address → {score, reasoning} map."""
        result: Dict[str, Dict[str, Any]] = {}

        # Try code block extraction first
        code_block = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', text)
        json_str = code_block.group(1) if code_block else text

        # Find JSON with "scores" key
        key_pos = json_str.find('"scores"')
        if key_pos == -1:
            return result

        start = json_str.rfind('{', 0, key_pos)
        if start == -1:
            return result

        depth = 0
        for i, char in enumerate(json_str[start:], start):
            if char == '{':
                depth += 1
            elif char == '}':
                depth -= 1
                if depth == 0:
                    try:
                        data = json.loads(json_str[start:i + 1])
                        for item in data.get("scores", []):
                            addr = item.get("token_address", "").lower()
                            if addr:
                                result[addr] = {
                                    "momentum_score": float(item.get("momentum_score", 0)),
                                    "reasoning": item.get("reasoning", ""),
                                }
                    except (json.JSONDecodeError, ValueError):
                        pass
                    break

        return result

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
