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

    # Native SOL mint used by _refresh_native_price
    _SOL_MINT = "So11111111111111111111111111111111111111112"

    def __init__(self, price_usd: float, liquidity_usd: float, native_price_usd: float = 180.0) -> None:
        self.price_usd = price_usd
        self.liquidity_usd = liquidity_usd
        self.native_price_usd = native_price_usd

    async def call_tool(self, method: str, arguments: Dict[str, Any]) -> Any:
        assert method == "get_token_pools"
        token = arguments.get("tokenAddress", "")
        price = self.native_price_usd if token == self._SOL_MINT else self.price_usd
        return {
            "pairs": [
                {
                    "priceUsd": str(price),
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


@pytest.mark.asyncio
async def test_lag_cycle_skips_token_after_error(db):
    """Token that fails with an error is skipped on subsequent cycles."""
    await db.add_entry(
        token_address="FailMint1111111111111111111111111111111111111",
        symbol="FAIL",
        chain="solana",
    )
    await db.add_entry(
        token_address="So11111111111111111111111111111111111111111",
        symbol="GOOD",
        chain="solana",
    )

    class FailingTraderClient(MockTraderClient):
        async def call_tool(self, method: str, arguments: Dict[str, Any]) -> Any:
            if method == "getQuote":
                out_mint = arguments.get("outputMint", "")
                if "failmint" in out_mint.lower():
                    return "Error: Jupiter quote failed (400): TOKEN_NOT_TRADABLE"
            return await super().call_tool(method, arguments)

    manager = MockMCPManager(
        dexscreener=MockDexScreenerClient(price_usd=1.20, liquidity_usd=50000),
        trader=FailingTraderClient(buy_price=1.00, sell_price=1.15),
    )
    engine = LagStrategyEngine(db=db, mcp_manager=manager, config=_config())

    # First cycle: FAIL errors, GOOD succeeds
    r1 = await engine.run_cycle()
    assert len(r1.errors) == 1
    assert "FAIL" in r1.errors[0]
    assert r1.samples_taken >= 1  # GOOD was sampled

    # Second cycle: FAIL is skipped (no new error), GOOD still processed
    r2 = await engine.run_cycle()
    assert len(r2.errors) == 0
    assert r2.samples_taken >= 1


@pytest.mark.asyncio
async def test_lag_cycle_excludes_native_and_quote_mints(db):
    """Tokens matching native mint (SOL) or quote mint (USDC) are excluded."""
    from app.lag_execution import SOL_NATIVE_MINT

    usdc_mint = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    await db.add_entry(
        token_address=SOL_NATIVE_MINT,
        symbol="SOL",
        chain="solana",
    )
    await db.add_entry(
        token_address=usdc_mint,
        symbol="USDC",
        chain="solana",
    )
    await db.add_entry(
        token_address="JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
        symbol="JUP",
        chain="solana",
    )

    manager = MockMCPManager(
        dexscreener=MockDexScreenerClient(price_usd=1.20, liquidity_usd=50000),
        trader=MockTraderClient(buy_price=1.00, sell_price=1.15),
    )
    engine = LagStrategyEngine(db=db, mcp_manager=manager, config=_config())

    result = await engine.run_cycle()

    # Only JUP should be sampled; SOL and USDC excluded
    assert result.samples_taken == 1


@pytest.mark.asyncio
async def test_lag_cycle_skips_when_native_price_unavailable(db):
    """Cycle is skipped entirely when native token price cannot be fetched."""
    await db.add_entry(
        token_address="So11111111111111111111111111111111111111111",
        symbol="TEST",
        chain="solana",
    )

    class NoPriceDexScreener:
        async def call_tool(self, method: str, arguments: Dict[str, Any]) -> Any:
            raise RuntimeError("DexScreener unavailable")

    manager = MockMCPManager(
        dexscreener=NoPriceDexScreener(),  # type: ignore[arg-type]
        trader=MockTraderClient(buy_price=1.00, sell_price=1.15),
    )
    engine = LagStrategyEngine(db=db, mcp_manager=manager, config=_config())

    result = await engine.run_cycle()

    assert "native token price unavailable" in result.summary.lower()
    assert result.samples_taken == 0
    assert len(result.errors) == 1

    events = await db.list_recent_lag_events(limit=5)
    event_types = {e.event_type for e in events}
    assert "native_price_unavailable" in event_types


@pytest.mark.asyncio
async def test_compute_edge_bps_rejects_zero_reference_price():
    """_compute_edge_bps raises ValueError for zero reference price."""
    with pytest.raises(ValueError, match="Reference price"):
        LagStrategyEngine._compute_edge_bps(0.0, 1.0)


@pytest.mark.asyncio
async def test_compute_edge_bps_rejects_negative_reference_price():
    """_compute_edge_bps raises ValueError for negative reference price."""
    with pytest.raises(ValueError, match="Reference price"):
        LagStrategyEngine._compute_edge_bps(-1.0, 1.0)


@pytest.mark.asyncio
async def test_lag_cycle_blocks_on_total_exposure_limit(db):
    """Entry is blocked when total exposure would exceed max_total_exposure_usd."""
    await db.add_entry(
        token_address="Token111111111111111111111111111111111111111",
        symbol="TOK1",
        chain="solana",
    )
    await db.add_entry(
        token_address="Token222222222222222222222222222222222222222",
        symbol="TOK2",
        chain="solana",
    )

    manager = MockMCPManager(
        dexscreener=MockDexScreenerClient(price_usd=1.20, liquidity_usd=50000),
        trader=MockTraderClient(buy_price=1.00, sell_price=1.15),
    )
    engine = LagStrategyEngine(
        db=db,
        mcp_manager=manager,
        config=_config(
            max_total_exposure_usd=30.0,
            max_position_usd=25.0,
            cooldown_seconds=0,
            # Wide TP/SL to prevent exit triggers interfering
            take_profit_bps=5000.0,
            stop_loss_bps=5000.0,
        ),
    )

    # First cycle opens TOK1 ($25 notional)
    r1 = await engine.run_cycle()
    assert len(r1.entries_opened) >= 1

    # Second cycle: TOK2 should be blocked ($25 + $25 = $50 > $30 limit)
    r2 = await engine.run_cycle()
    assert len(r2.entries_opened) == 0

    events = await db.list_recent_lag_events(limit=20)
    event_types = {e.event_type for e in events}
    assert "risk_block_total_exposure" in event_types


@pytest.mark.asyncio
async def test_sell_pnl_uses_actual_sold_quantity(db):
    """PnL calculation uses actual sell_qty, not stored position quantity."""
    await db.add_entry(
        token_address="So11111111111111111111111111111111111111111",
        symbol="TEST",
        chain="solana",
    )

    class AdjustedBalanceTraderClient(MockTraderClient):
        """Trader that also exposes get_balance returning less than position qty."""
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.tools.append({
                "name": "get_balance",
                "inputSchema": {
                    "type": "object",
                    "required": ["token_address"],
                    "properties": {"token_address": {"type": "string"}},
                },
            })

        async def call_tool(self, method: str, arguments: Dict[str, Any]) -> Any:
            if method == "get_balance":
                # Return half the expected quantity
                return {"tokenBalance": {"uiAmount": 5.0}}
            return await super().call_tool(method, arguments)

    manager = MockMCPManager(
        dexscreener=MockDexScreenerClient(price_usd=1.20, liquidity_usd=50000),
        trader=AdjustedBalanceTraderClient(buy_price=1.00, sell_price=1.20),
    )
    # Use dry_run=False so wallet balance is checked, but note that live
    # execution requires tx_hash. The mock returns txHash so it works.
    engine = LagStrategyEngine(
        db=db,
        mcp_manager=manager,
        config=_config(take_profit_bps=50.0, dry_run=False),
    )

    first = await engine.run_cycle()
    assert len(first.entries_opened) == 1
    position = first.entries_opened[0]

    second = await engine.run_cycle()
    assert len(second.positions_closed) == 1
    closed_pos = second.positions_closed[0]

    # PnL should be based on sell_qty (5.0), not position.quantity_token (25.0)
    # PnL = (exit_price - entry_price) * sell_qty
    expected_pnl = (1.20 - 1.00) * 5.0
    assert closed_pos.realized_pnl_usd == pytest.approx(expected_pnl, abs=0.01)


@pytest.mark.asyncio
async def test_sell_failure_tracks_retry_count(db):
    """Consecutive sell failures increment the retry counter and escalate."""
    # Directly insert an open position to avoid needing a real buy cycle
    position = await db.add_lag_position(
        token_address="So11111111111111111111111111111111111111111",
        symbol="TEST",
        chain="solana",
        entry_price=1.00,
        quantity_token=25.0,
        notional_usd=25.0,
        stop_price=0.99,
        take_price=1.005,
        dry_run=False,
    )

    class FailingSellTrader(MockTraderClient):
        async def call_tool(self, method: str, arguments: Dict[str, Any]) -> Any:
            if method == "swap" and arguments.get("side") == "sell":
                return {"success": False, "error": "insufficient liquidity"}
            return await super().call_tool(method, arguments)

    manager = MockMCPManager(
        dexscreener=MockDexScreenerClient(price_usd=1.20, liquidity_usd=50000),
        trader=FailingSellTrader(buy_price=1.00, sell_price=1.20),
    )
    engine = LagStrategyEngine(
        db=db,
        mcp_manager=manager,
        config=_config(take_profit_bps=50.0, dry_run=False),
    )

    # 3 failed sell attempts → should escalate to "exit_stuck"
    for i in range(3):
        r = await engine.run_cycle()
        assert len(r.errors) >= 1, f"Cycle {i+1} should have sell errors"

    events = await db.list_recent_lag_events(limit=20)
    event_types = [e.event_type for e in events]
    assert "exit_stuck" in event_types
