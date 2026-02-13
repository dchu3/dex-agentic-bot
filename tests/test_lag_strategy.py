"""Tests for lag-edge strategy engine and persistence wiring."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict, List

import pytest
import pytest_asyncio

from app.lag_strategy import LagStrategyConfig, LagStrategyEngine
from app.watchlist import WatchlistDB


class MockDexScreenerClient:
    """Mock DexScreener client for deterministic reference prices."""

    def __init__(self, price_usd: float, liquidity_usd: float) -> None:
        self.price_usd = price_usd
        self.liquidity_usd = liquidity_usd

    async def call_tool(self, method: str, arguments: Dict[str, Any]) -> Any:
        assert method == "get_token_pools"
        return {
            "pairs": [
                {
                    "priceUsd": str(self.price_usd),
                    "liquidity": {"usd": self.liquidity_usd},
                }
            ]
        }


class MockTraderClient:
    """Mock trader client with quote + swap tools."""

    def __init__(self, buy_price: float, sell_price: float) -> None:
        self.buy_price = buy_price
        self.sell_price = sell_price
        self.tools: List[Dict[str, Any]] = [
            {
                "name": "getQuote",
                "inputSchema": {
                    "type": "object",
                    "required": ["chain", "inputMint", "outputMint", "amountUsd", "slippageBps", "side"],
                    "properties": {
                        "chain": {"type": "string"},
                        "inputMint": {"type": "string"},
                        "outputMint": {"type": "string"},
                        "amountUsd": {"type": "number"},
                        "slippageBps": {"type": "integer"},
                        "side": {"type": "string"},
                    },
                },
            },
            {
                "name": "swap",
                "inputSchema": {
                    "type": "object",
                    "required": ["chain", "inputMint", "outputMint", "amountUsd", "slippageBps", "side"],
                    "properties": {
                        "chain": {"type": "string"},
                        "inputMint": {"type": "string"},
                        "outputMint": {"type": "string"},
                        "amountUsd": {"type": "number"},
                        "slippageBps": {"type": "integer"},
                        "side": {"type": "string"},
                    },
                },
            },
        ]

    async def call_tool(self, method: str, arguments: Dict[str, Any]) -> Any:
        if method == "getQuote":
            price = self.sell_price if arguments.get("side") == "sell" else self.buy_price
            return {"priceUsd": str(price), "liquidityUsd": 250000}
        if method == "swap":
            price = self.sell_price if arguments.get("side") == "sell" else self.buy_price
            return {"success": True, "executedPrice": str(price), "txHash": "mocktx"}
        raise ValueError(f"Unexpected method: {method}")


class MockMCPManager:
    """Mock MCP manager for lag strategy tests."""

    def __init__(self, dexscreener: MockDexScreenerClient, trader: MockTraderClient) -> None:
        self._dexscreener = dexscreener
        self._trader = trader

    def get_client(self, name: str) -> Any:
        if name == "dexscreener":
            return self._dexscreener
        if name == "trader":
            return self._trader
        return None


@pytest.fixture
def temp_db_path():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir) / "test_lag_strategy.db"


@pytest_asyncio.fixture
async def db(temp_db_path):
    database = WatchlistDB(db_path=temp_db_path)
    await database.connect()
    yield database
    await database.close()


def _config(**overrides: Any) -> LagStrategyConfig:
    base = LagStrategyConfig(
        enabled=True,
        dry_run=True,
        interval_seconds=15,
        chain="solana",
        sample_notional_usd=25.0,
        min_edge_bps=50.0,
        min_liquidity_usd=1000.0,
        max_slippage_bps=100,
        max_position_usd=25.0,
        max_open_positions=3,
        cooldown_seconds=3600,
        take_profit_bps=100.0,
        stop_loss_bps=100.0,
        max_hold_seconds=3600,
        daily_loss_limit_usd=500.0,
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


@pytest.mark.asyncio
async def test_lag_cycle_opens_position_on_signal(db):
    await db.add_entry(
        token_address="So11111111111111111111111111111111111111111",
        symbol="TEST",
        chain="solana",
    )

    manager = MockMCPManager(
        dexscreener=MockDexScreenerClient(price_usd=1.20, liquidity_usd=50000),
        trader=MockTraderClient(buy_price=1.00, sell_price=1.15),
    )
    engine = LagStrategyEngine(
        db=db,
        mcp_manager=manager,
        config=_config(),
    )

    result = await engine.run_cycle()

    assert result.samples_taken == 1
    assert result.signals_triggered == 1
    assert len(result.entries_opened) == 1
    assert len(await db.list_open_lag_positions(chain="solana")) == 1


@pytest.mark.asyncio
async def test_lag_cycle_closes_position_on_take_profit(db):
    await db.add_entry(
        token_address="So11111111111111111111111111111111111111111",
        symbol="TEST",
        chain="solana",
    )
    manager = MockMCPManager(
        dexscreener=MockDexScreenerClient(price_usd=1.20, liquidity_usd=50000),
        trader=MockTraderClient(buy_price=1.00, sell_price=1.20),
    )
    engine = LagStrategyEngine(
        db=db,
        mcp_manager=manager,
        config=_config(take_profit_bps=50.0),
    )

    first = await engine.run_cycle()
    assert len(first.entries_opened) == 1

    second = await engine.run_cycle()
    assert len(second.positions_closed) == 1
    assert len(await db.list_open_lag_positions(chain="solana")) == 0

    events = await db.list_recent_lag_events(limit=10)
    event_types = {event.event_type for event in events}
    assert "entry_opened" in event_types
    assert "position_closed" in event_types


@pytest.mark.asyncio
async def test_lag_cycle_disabled(db):
    await db.add_entry(
        token_address="So11111111111111111111111111111111111111111",
        symbol="TEST",
        chain="solana",
    )
    manager = MockMCPManager(
        dexscreener=MockDexScreenerClient(price_usd=1.20, liquidity_usd=50000),
        trader=MockTraderClient(buy_price=1.00, sell_price=1.20),
    )
    engine = LagStrategyEngine(
        db=db,
        mcp_manager=manager,
        config=_config(enabled=False),
    )

    result = await engine.run_cycle()
    assert result.summary == "Lag strategy disabled"
    assert result.samples_taken == 0
