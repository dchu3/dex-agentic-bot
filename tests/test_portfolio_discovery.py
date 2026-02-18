"""Tests for portfolio discovery engine: filters, AI scoring, safety checks."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import pytest

from app.portfolio_discovery import DiscoveryCandidate, PortfolioDiscovery


# ---------------------------------------------------------------------------
# Mock clients
# ---------------------------------------------------------------------------

class MockDexScreenerClient:
    """Returns canned data for DexScreener MCP endpoints."""

    def __init__(
        self,
        pairs: Optional[List[Dict[str, Any]]] = None,
        boosted_tokens: Optional[List[Dict[str, Any]]] = None,
        pool_pairs: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    ) -> None:
        self.pairs = pairs or []
        self.boosted_tokens = boosted_tokens or []
        # pool_pairs: token_address (lower) → list of pair dicts
        self.pool_pairs = pool_pairs or {}

    async def call_tool(self, method: str, arguments: Dict[str, Any]) -> Any:
        if method == "search_pairs":
            return {"pairs": self.pairs}
        if method in ("get_top_boosted_tokens", "get_latest_boosted_tokens"):
            return self.boosted_tokens
        if method == "get_token_pools":
            addr = arguments.get("tokenAddress", "").lower()
            return self.pool_pairs.get(addr, [])
        return {}


class MockRugcheckClient:
    def __init__(self, score: float = 100.0, risks: Optional[list] = None) -> None:
        self.score = score
        self.risks = risks or []

    async def call_tool(self, method: str, arguments: Dict[str, Any]) -> Any:
        return {"score_normalised": self.score, "risks": self.risks}


class MockMCPManager:
    def __init__(
        self,
        dexscreener: Optional[MockDexScreenerClient] = None,
        rugcheck: Optional[MockRugcheckClient] = None,
    ) -> None:
        self._dexscreener = dexscreener
        self._rugcheck = rugcheck

    def get_client(self, name: str) -> Any:
        if name == "dexscreener":
            return self._dexscreener
        if name == "rugcheck":
            return self._rugcheck
        return None


class MockDatabase:
    """Mock DB that reports no open positions by default."""

    def __init__(self, held_addresses: Optional[set] = None) -> None:
        self._held = held_addresses or set()

    async def get_open_portfolio_position(self, token_address: str, chain: str) -> Any:
        if token_address.lower() in {a.lower() for a in self._held}:
            return object()  # truthy value = position exists
        return None


def _make_pair(
    address: str = "TestAddr111111111111111111111111111111111",
    symbol: str = "TEST",
    chain: str = "solana",
    price: float = 0.01,
    volume_24h: float = 50000.0,
    liquidity_usd: float = 30000.0,
    market_cap: float = 500000.0,
    price_change: float = 5.0,
) -> Dict[str, Any]:
    return {
        "chainId": chain,
        "baseToken": {"address": address, "symbol": symbol},
        "priceUsd": str(price),
        "volume": {"h24": volume_24h},
        "liquidity": {"usd": liquidity_usd},
        "marketCap": market_cap,
        "priceChange": {"h24": price_change},
    }


# ---------------------------------------------------------------------------
# Deterministic filter tests
# ---------------------------------------------------------------------------


class TestApplyFilters:
    """Test the deterministic pre-filter step."""

    def test_filters_by_chain(self):
        discovery = PortfolioDiscovery(
            mcp_manager=MockMCPManager(), api_key="x", chain="solana",
        )
        pairs = [
            _make_pair(chain="solana", address="A1111111111111111111111111111111111111111"),
            _make_pair(chain="ethereum", address="B2222222222222222222222222222222222222222"),
        ]
        result = discovery._apply_filters(pairs)
        assert len(result) == 1
        assert result[0].chain == "solana"

    def test_filters_by_volume(self):
        discovery = PortfolioDiscovery(
            mcp_manager=MockMCPManager(), api_key="x", min_volume_usd=50000.0,
        )
        pairs = [
            _make_pair(volume_24h=60000.0, address="A1111111111111111111111111111111111111111"),
            _make_pair(volume_24h=30000.0, address="B2222222222222222222222222222222222222222"),
        ]
        result = discovery._apply_filters(pairs)
        assert len(result) == 1

    def test_filters_by_liquidity(self):
        discovery = PortfolioDiscovery(
            mcp_manager=MockMCPManager(), api_key="x", min_liquidity_usd=25000.0,
        )
        pairs = [
            _make_pair(liquidity_usd=30000.0, address="A1111111111111111111111111111111111111111"),
            _make_pair(liquidity_usd=15000.0, address="B2222222222222222222222222222222222222222"),
        ]
        result = discovery._apply_filters(pairs)
        assert len(result) == 1

    def test_filters_by_market_cap(self):
        discovery = PortfolioDiscovery(
            mcp_manager=MockMCPManager(), api_key="x", min_market_cap_usd=250000.0,
        )
        pairs = [
            _make_pair(market_cap=300000.0, address="A1111111111111111111111111111111111111111"),
            _make_pair(market_cap=100000.0, address="B2222222222222222222222222222222222222222"),
        ]
        result = discovery._apply_filters(pairs)
        assert len(result) == 1

    def test_filters_by_market_cap_fdv_fallback(self):
        """Should use fdv when marketCap is missing."""
        discovery = PortfolioDiscovery(
            mcp_manager=MockMCPManager(), api_key="x", min_market_cap_usd=250000.0,
        )
        pair = _make_pair(address="A1111111111111111111111111111111111111111")
        pair.pop("marketCap", None)
        pair["fdv"] = 300000.0
        result = discovery._apply_filters([pair])
        assert len(result) == 1

    def test_filters_zero_price(self):
        discovery = PortfolioDiscovery(
            mcp_manager=MockMCPManager(), api_key="x",
        )
        pairs = [
            _make_pair(price=0.0),
        ]
        result = discovery._apply_filters(pairs)
        assert len(result) == 0

    def test_deduplicates_addresses(self):
        discovery = PortfolioDiscovery(
            mcp_manager=MockMCPManager(), api_key="x",
        )
        addr = "DupAddr1111111111111111111111111111111111"
        pairs = [
            _make_pair(address=addr, symbol="DUP1"),
            _make_pair(address=addr, symbol="DUP2"),
        ]
        result = discovery._apply_filters(pairs)
        assert len(result) == 1

    def test_skips_missing_address(self):
        discovery = PortfolioDiscovery(
            mcp_manager=MockMCPManager(), api_key="x",
        )
        pairs = [{"chainId": "solana", "baseToken": {"address": "", "symbol": "X"}}]
        result = discovery._apply_filters(pairs)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# Held token exclusion
# ---------------------------------------------------------------------------


class TestExcludeHeldTokens:
    @pytest.mark.asyncio
    async def test_excludes_held(self):
        held_addr = "HeldToken111111111111111111111111111111111"
        discovery = PortfolioDiscovery(
            mcp_manager=MockMCPManager(), api_key="x",
        )
        candidates = [
            DiscoveryCandidate(
                token_address=held_addr, symbol="HELD", chain="solana",
                price_usd=1.0, volume_24h=50000, liquidity_usd=20000,
            ),
            DiscoveryCandidate(
                token_address="FreeToken111111111111111111111111111111111",
                symbol="FREE", chain="solana",
                price_usd=1.0, volume_24h=50000, liquidity_usd=20000,
            ),
        ]
        db = MockDatabase(held_addresses={held_addr})

        result = await discovery._exclude_held_tokens(candidates, db)

        assert len(result) == 1
        assert result[0].symbol == "FREE"


# ---------------------------------------------------------------------------
# Safety check parsing
# ---------------------------------------------------------------------------


class TestParseSafety:
    def test_safe_token(self):
        status, score = PortfolioDiscovery._parse_safety(
            {"score_normalised": 200, "risks": []}
        )
        assert status == "Safe"
        assert score == 200.0

    def test_risky_token(self):
        status, score = PortfolioDiscovery._parse_safety(
            {"score_normalised": 1500, "risks": ["one", "two"]}
        )
        assert status == "Risky"
        assert score == 1500.0

    def test_dangerous_token(self):
        status, score = PortfolioDiscovery._parse_safety(
            {"score_normalised": 5000, "risks": ["a", "b", "c"]}
        )
        assert status == "Dangerous"
        assert score == 5000.0

    def test_string_json_input(self):
        status, score = PortfolioDiscovery._parse_safety(
            json.dumps({"score_normalised": 100, "risks": []})
        )
        assert status == "Safe"

    def test_list_input(self):
        status, score = PortfolioDiscovery._parse_safety(
            [{"score_normalised": 300, "risks": []}]
        )
        assert status == "Safe"

    def test_invalid_string(self):
        status, score = PortfolioDiscovery._parse_safety("not json")
        assert status == "unverified"
        assert score is None


# ---------------------------------------------------------------------------
# AI score parsing
# ---------------------------------------------------------------------------


class TestParseScores:
    def test_parses_valid_json(self):
        text = json.dumps({
            "scores": [
                {"token_address": "addr1", "momentum_score": 85, "reasoning": "strong"},
                {"token_address": "addr2", "momentum_score": 40, "reasoning": "weak"},
            ]
        })
        result = PortfolioDiscovery._parse_scores(text)
        assert len(result) == 2
        assert result["addr1"]["momentum_score"] == 85
        assert result["addr2"]["reasoning"] == "weak"

    def test_parses_code_block(self):
        text = '```json\n{"scores": [{"token_address": "a", "momentum_score": 60, "reasoning": "ok"}]}\n```'
        result = PortfolioDiscovery._parse_scores(text)
        assert len(result) == 1
        assert result["a"]["momentum_score"] == 60

    def test_empty_on_invalid(self):
        result = PortfolioDiscovery._parse_scores("no json here")
        assert result == {}

    def test_handles_surrounding_text(self):
        text = 'Here are the scores: {"scores": [{"token_address": "x", "momentum_score": 70, "reasoning": "good"}]} End.'
        result = PortfolioDiscovery._parse_scores(text)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Heuristic scoring fallback
# ---------------------------------------------------------------------------


class TestHeuristicScore:
    def test_strong_candidate(self):
        c = DiscoveryCandidate(
            token_address="x", symbol="X", chain="solana",
            price_usd=1.0, volume_24h=100000, liquidity_usd=50000,
            price_change_24h=20.0, safety_status="Safe",
        )
        score = PortfolioDiscovery._heuristic_score(c)
        assert score >= 50.0  # Should be a decent score

    def test_weak_candidate(self):
        c = DiscoveryCandidate(
            token_address="x", symbol="X", chain="solana",
            price_usd=1.0, volume_24h=5000, liquidity_usd=3000,
            price_change_24h=-5.0, safety_status="Dangerous",
        )
        score = PortfolioDiscovery._heuristic_score(c)
        assert score < 50.0

    def test_capped_at_100(self):
        c = DiscoveryCandidate(
            token_address="x", symbol="X", chain="solana",
            price_usd=1.0, volume_24h=1000000, liquidity_usd=100000,
            price_change_24h=100.0, safety_status="Safe",
        )
        score = PortfolioDiscovery._heuristic_score(c)
        assert score == 100.0


# ---------------------------------------------------------------------------
# Extract pairs
# ---------------------------------------------------------------------------


class TestExtractPairs:
    def test_from_dict_with_pairs(self):
        result = PortfolioDiscovery._extract_pairs(
            {"pairs": [{"a": 1}, {"b": 2}]}
        )
        assert len(result) == 2

    def test_from_dict_with_results(self):
        result = PortfolioDiscovery._extract_pairs(
            {"results": [{"a": 1}]}
        )
        assert len(result) == 1

    def test_from_list(self):
        result = PortfolioDiscovery._extract_pairs([{"a": 1}])
        assert len(result) == 1

    def test_from_string(self):
        result = PortfolioDiscovery._extract_pairs("invalid")
        assert len(result) == 0


# ---------------------------------------------------------------------------
# Boosted token extraction
# ---------------------------------------------------------------------------


class TestExtractBoostedTokens:
    def test_extracts_from_list(self):
        data = [
            {"tokenAddress": "addr1", "chainId": "solana"},
            {"tokenAddress": "addr2", "chainId": "ethereum"},
        ]
        result = PortfolioDiscovery._extract_boosted_tokens(data)
        assert len(result) == 2

    def test_extracts_from_wrapped_dict(self):
        data = {"tokens": [{"tokenAddress": "addr1", "chainId": "solana"}]}
        result = PortfolioDiscovery._extract_boosted_tokens(data)
        assert len(result) == 1

    def test_skips_entries_without_address(self):
        data = [{"chainId": "solana"}, {"tokenAddress": "addr1", "chainId": "solana"}]
        result = PortfolioDiscovery._extract_boosted_tokens(data)
        assert len(result) == 1

    def test_returns_empty_for_string(self):
        assert PortfolioDiscovery._extract_boosted_tokens("invalid") == []

    def test_returns_empty_for_empty_list(self):
        assert PortfolioDiscovery._extract_boosted_tokens([]) == []


# ---------------------------------------------------------------------------
# Boosted token discovery integration
# ---------------------------------------------------------------------------


class TestFetchBoostedTokens:
    @pytest.mark.asyncio
    async def test_filters_by_chain(self):
        discovery = PortfolioDiscovery(
            mcp_manager=MockMCPManager(), api_key="x", chain="solana",
        )
        client = MockDexScreenerClient(boosted_tokens=[
            {"tokenAddress": "SolToken111", "chainId": "solana"},
            {"tokenAddress": "EthToken111", "chainId": "ethereum"},
        ])
        tokens = await discovery._fetch_boosted_tokens(client)
        assert len(tokens) == 1
        assert tokens[0]["tokenAddress"] == "SolToken111"

    @pytest.mark.asyncio
    async def test_deduplicates_across_endpoints(self):
        """Same token from both boosted endpoints should appear once."""
        discovery = PortfolioDiscovery(
            mcp_manager=MockMCPManager(), api_key="x", chain="solana",
        )
        client = MockDexScreenerClient(boosted_tokens=[
            {"tokenAddress": "DupAddr1111", "chainId": "solana"},
            {"tokenAddress": "DupAddr1111", "chainId": "solana"},
            {"tokenAddress": "Unique11111", "chainId": "solana"},
        ])
        tokens = await discovery._fetch_boosted_tokens(client)
        assert len(tokens) == 2


class TestFetchPairsForTokens:
    @pytest.mark.asyncio
    async def test_selects_highest_liquidity_pair(self):
        discovery = PortfolioDiscovery(
            mcp_manager=MockMCPManager(), api_key="x", chain="solana",
        )
        pool_pairs = {
            "addr1": [
                _make_pair(address="addr1", liquidity_usd=5000),
                _make_pair(address="addr1", liquidity_usd=50000),
                _make_pair(address="addr1", liquidity_usd=10000),
            ]
        }
        client = MockDexScreenerClient(pool_pairs=pool_pairs)
        tokens = [{"tokenAddress": "addr1", "chainId": "solana"}]
        pairs = await discovery._fetch_pairs_for_tokens(client, tokens)
        assert len(pairs) == 1
        assert float(pairs[0]["liquidity"]["usd"]) == 50000

    @pytest.mark.asyncio
    async def test_handles_empty_pools(self):
        discovery = PortfolioDiscovery(
            mcp_manager=MockMCPManager(), api_key="x", chain="solana",
        )
        client = MockDexScreenerClient(pool_pairs={})
        tokens = [{"tokenAddress": "nopool", "chainId": "solana"}]
        pairs = await discovery._fetch_pairs_for_tokens(client, tokens)
        assert len(pairs) == 0


class TestScanTrendingIntegration:
    @pytest.mark.asyncio
    async def test_merges_boosted_and_search_results(self):
        """Boosted tokens + search results are merged and deduplicated."""
        boosted_addr = "BoostedToken1111111111111111111111111111111"
        search_addr = "SearchToken11111111111111111111111111111111"

        boosted_pair = _make_pair(address=boosted_addr, symbol="BOOST", volume_24h=100000)
        search_pair = _make_pair(address=search_addr, symbol="SRCH", volume_24h=50000)

        client = MockDexScreenerClient(
            pairs=[search_pair],
            boosted_tokens=[{"tokenAddress": boosted_addr, "chainId": "solana"}],
            pool_pairs={boosted_addr.lower(): [boosted_pair]},
        )
        manager = MockMCPManager(dexscreener=client)
        discovery = PortfolioDiscovery(
            mcp_manager=manager, api_key="x", chain="solana",
        )
        pairs = await discovery._scan_trending()
        addresses = {(p.get("baseToken") or {}).get("address", "") for p in pairs}
        assert boosted_addr in addresses
        assert search_addr in addresses

    @pytest.mark.asyncio
    async def test_deduplicates_across_sources(self):
        """Token appearing in both boosted and search results appears once."""
        addr = "SharedToken11111111111111111111111111111111"
        pair = _make_pair(address=addr, symbol="SHARED")

        client = MockDexScreenerClient(
            pairs=[pair],
            boosted_tokens=[{"tokenAddress": addr, "chainId": "solana"}],
            pool_pairs={addr.lower(): [pair]},
        )
        manager = MockMCPManager(dexscreener=client)
        discovery = PortfolioDiscovery(
            mcp_manager=manager, api_key="x", chain="solana",
        )
        pairs = await discovery._scan_trending()
        assert len(pairs) == 1

    @pytest.mark.asyncio
    async def test_works_without_boosted_tokens(self):
        """Falls back to search_pairs when no boosted tokens exist."""
        addr = "SearchOnly111111111111111111111111111111111"
        pair = _make_pair(address=addr, symbol="ONLY")

        client = MockDexScreenerClient(pairs=[pair], boosted_tokens=[])
        manager = MockMCPManager(dexscreener=client)
        discovery = PortfolioDiscovery(
            mcp_manager=manager, api_key="x", chain="solana",
        )
        pairs = await discovery._scan_trending()
        assert len(pairs) == 1
