"""Tests for portfolio strategy engine: discovery cycle, exit checks, risk guards."""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
import pytest_asyncio

from app.portfolio_strategy import (
    PortfolioDiscoveryCycleResult,
    PortfolioExitCycleResult,
    PortfolioStrategyConfig,
    PortfolioStrategyEngine,
)
from app.portfolio_discovery import DiscoveryCandidate
from app.database import Database, PortfolioPosition


# ---------------------------------------------------------------------------
# Mock clients
# ---------------------------------------------------------------------------

SOL_MINT = "So11111111111111111111111111111111111111112"


class MockDexScreenerClient:
    def __init__(
        self,
        price_usd: float = 0.01,
        liquidity_usd: float = 50000.0,
        native_price_usd: float = 180.0,
    ) -> None:
        self.price_usd = price_usd
        self.liquidity_usd = liquidity_usd
        self.native_price_usd = native_price_usd
        self.prices: Dict[str, float] = {}

    async def call_tool(self, method: str, arguments: Dict[str, Any]) -> Any:
        token = arguments.get("tokenAddress", "")
        if token == SOL_MINT:
            price = self.native_price_usd
        else:
            price = self.prices.get(token.lower(), self.price_usd)
        return {
            "pairs": [
                {
                    "priceUsd": str(price),
                    "liquidity": {"usd": self.liquidity_usd},
                }
            ]
        }


class MockTraderClient:
    def __init__(self, price: float = 0.01, success: bool = True) -> None:
        self.price = price
        self.success = success
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
            return {"priceUsd": str(self.price), "liquidityUsd": 100000}
        if method == "swap":
            if self.success:
                return {"success": True, "executedPrice": str(self.price), "txHash": "mocktx"}
            return {"success": False, "error": "execution failed"}
        raise ValueError(f"Unexpected method: {method}")


class MockRugcheckClient:
    def __init__(self, score: float = 100.0) -> None:
        self.score = score

    async def call_tool(self, method: str, arguments: Dict[str, Any]) -> Any:
        return {"score_normalised": self.score, "risks": []}


class MockMCPManager:
    def __init__(
        self,
        dexscreener: MockDexScreenerClient,
        trader: MockTraderClient,
        rugcheck: Optional[MockRugcheckClient] = None,
    ) -> None:
        self._dexscreener = dexscreener
        self._trader = trader
        self._rugcheck = rugcheck or MockRugcheckClient()

    def get_client(self, name: str) -> Any:
        if name == "dexscreener":
            return self._dexscreener
        if name == "trader":
            return self._trader
        if name == "rugcheck":
            return self._rugcheck
        return None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_db_path():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir) / "test_portfolio.db"


@pytest_asyncio.fixture
async def db(temp_db_path):
    database = Database(db_path=temp_db_path)
    await database.connect()
    yield database
    await database.close()


def _config(**overrides: Any) -> PortfolioStrategyConfig:
    base = PortfolioStrategyConfig(
        enabled=True,
        dry_run=True,
        chain="solana",
        max_positions=5,
        position_size_usd=5.0,
        take_profit_pct=15.0,
        stop_loss_pct=8.0,
        trailing_stop_pct=5.0,
        max_hold_hours=24,
        discovery_interval_mins=30,
        price_check_seconds=60,
        daily_loss_limit_usd=50.0,
        min_volume_usd=10000.0,
        min_liquidity_usd=5000.0,
        min_market_cap_usd=250000.0,
        cooldown_seconds=300,
        min_momentum_score=50.0,
        max_slippage_bps=300,
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def _make_engine(
    db: Database,
    dex_price: float = 0.01,
    trader_price: float = 0.01,
    trader_success: bool = True,
    native_price: float = 180.0,
    **config_overrides: Any,
) -> PortfolioStrategyEngine:
    dex = MockDexScreenerClient(price_usd=dex_price, native_price_usd=native_price)
    trader = MockTraderClient(price=trader_price, success=trader_success)
    manager = MockMCPManager(dexscreener=dex, trader=trader)
    return PortfolioStrategyEngine(
        db=db,
        mcp_manager=manager,
        config=_config(**config_overrides),
        api_key="fake-key",
        model_name="test-model",
    )


async def _insert_position(
    db: Database,
    token_address: str = "TestToken111111111111111111111111111111111",
    symbol: str = "TEST",
    chain: str = "solana",
    entry_price: float = 0.01,
    quantity_token: float = 500.0,
    notional_usd: float = 5.0,
    stop_pct: float = 8.0,
    take_pct: float = 15.0,
    opened_at_offset_hours: float = 0.0,
) -> PortfolioPosition:
    """Insert a position into the DB and return it."""
    stop_price = entry_price * (1 - stop_pct / 100)
    take_price = entry_price * (1 + take_pct / 100)
    pos = await db.add_portfolio_position(
        token_address=token_address,
        symbol=symbol,
        chain=chain,
        entry_price=entry_price,
        quantity_token=quantity_token,
        notional_usd=notional_usd,
        stop_price=stop_price,
        take_price=take_price,
        dry_run=True,
    )
    # If we need to backdate the position, update it directly
    if opened_at_offset_hours:
        async with db._lock:
            opened_at = datetime.now(timezone.utc) - timedelta(hours=opened_at_offset_hours)
            await db._connection.execute(
                "UPDATE portfolio_positions SET opened_at = ? WHERE id = ?",
                (opened_at.isoformat(), pos.id),
            )
            await db._connection.commit()
        pos.opened_at = opened_at
    return pos


# ---------------------------------------------------------------------------
# Exit checks
# ---------------------------------------------------------------------------


class TestExitChecks:
    """Tests for PortfolioStrategyEngine.run_exit_checks()."""

    @pytest.mark.asyncio
    async def test_stop_loss_triggers_close(self, db):
        """When price drops below stop_price, position closes with stop_loss reason."""
        pos = await _insert_position(db, entry_price=1.00)

        engine = _make_engine(db, dex_price=0.90)  # Below stop (1.00 * 0.92 = 0.92)

        result = await engine.run_exit_checks()

        assert len(result.positions_closed) == 1
        assert result.positions_closed[0].close_reason == "stop_loss"
        assert len(await db.list_open_portfolio_positions(chain="solana")) == 0

    @pytest.mark.asyncio
    async def test_take_profit_triggers_close(self, db):
        """When price rises above take_price, position closes with take_profit reason."""
        pos = await _insert_position(db, entry_price=1.00)

        engine = _make_engine(db, dex_price=1.20)  # Above take (1.00 * 1.15 = 1.15)

        result = await engine.run_exit_checks()

        assert len(result.positions_closed) == 1
        assert result.positions_closed[0].close_reason == "take_profit"

    @pytest.mark.asyncio
    async def test_max_hold_triggers_close(self, db):
        """Position closes after max hold time."""
        pos = await _insert_position(db, entry_price=1.00, opened_at_offset_hours=25.0)

        engine = _make_engine(db, dex_price=1.05)  # Price between SL and TP

        result = await engine.run_exit_checks()

        assert len(result.positions_closed) == 1
        assert result.positions_closed[0].close_reason == "max_hold_time"

    @pytest.mark.asyncio
    async def test_no_close_when_in_range(self, db):
        """Position stays open when price is between SL and TP."""
        pos = await _insert_position(db, entry_price=1.00)

        engine = _make_engine(db, dex_price=1.05)  # Between SL and TP

        result = await engine.run_exit_checks()

        assert len(result.positions_closed) == 0
        assert result.positions_checked == 1
        assert len(await db.list_open_portfolio_positions(chain="solana")) == 1

    @pytest.mark.asyncio
    async def test_trailing_stop_ratchets_upward(self, db):
        """Trailing stop updates when price makes new high."""
        pos = await _insert_position(db, entry_price=1.00)
        original_stop = pos.stop_price

        # Price rises — should update trailing stop
        engine = _make_engine(db, dex_price=1.10)

        result = await engine.run_exit_checks()

        assert result.trailing_stops_updated == 1
        assert len(result.positions_closed) == 0

        # Verify stop was ratcheted up
        updated = await db.get_open_portfolio_position(pos.token_address, "solana")
        assert updated is not None
        assert updated.stop_price > original_stop
        assert updated.highest_price == 1.10

    @pytest.mark.asyncio
    async def test_trailing_stop_never_lowers(self, db):
        """Stop price never decreases even when price drops."""
        pos = await _insert_position(db, entry_price=1.00)

        engine = _make_engine(db, dex_price=1.10)
        # First check: raise trailing stop
        await engine.run_exit_checks()
        updated = await db.get_open_portfolio_position(pos.token_address, "solana")
        high_stop = updated.stop_price

        # Second check: price drops but still above stop
        engine2 = _make_engine(db, dex_price=1.06)
        result = await engine2.run_exit_checks()

        updated2 = await db.get_open_portfolio_position(pos.token_address, "solana")
        assert updated2.stop_price >= high_stop  # Never lowered

    @pytest.mark.asyncio
    async def test_pnl_calculation_on_close(self, db):
        """Realized PnL is calculated correctly on exit."""
        pos = await _insert_position(
            db, entry_price=1.00, quantity_token=100.0, notional_usd=100.0,
        )

        engine = _make_engine(db, dex_price=1.20, trader_price=1.20)

        result = await engine.run_exit_checks()

        assert len(result.positions_closed) == 1
        closed = result.positions_closed[0]
        expected_pnl = (1.20 - 1.00) * 100.0  # $20.00
        assert closed.realized_pnl_usd == pytest.approx(expected_pnl, rel=0.01)

    @pytest.mark.asyncio
    async def test_no_positions_exits_early(self, db):
        """Exit check returns quickly when no open positions."""
        engine = _make_engine(db)

        result = await engine.run_exit_checks()

        assert result.positions_checked == 0
        assert result.summary == "No open positions"

    @pytest.mark.asyncio
    async def test_disabled_returns_early(self, db):
        """Engine does nothing when disabled."""
        engine = _make_engine(db, enabled=False)

        result = await engine.run_exit_checks()

        assert result.summary == "Portfolio strategy disabled"


# ---------------------------------------------------------------------------
# Discovery cycle
# ---------------------------------------------------------------------------


class TestDiscoveryCycle:
    """Tests for PortfolioStrategyEngine.run_discovery_cycle()."""

    @pytest.mark.asyncio
    async def test_disabled_returns_early(self, db):
        engine = _make_engine(db, enabled=False)

        result = await engine.run_discovery_cycle()

        assert result.summary == "Portfolio strategy disabled"

    @pytest.mark.asyncio
    async def test_full_portfolio_skips(self, db):
        """When max positions reached, discovery skips."""
        for i in range(5):
            await _insert_position(
                db, token_address=f"Token{i}{'1' * 38}", symbol=f"T{i}",
            )

        engine = _make_engine(db, max_positions=5)

        result = await engine.run_discovery_cycle()

        assert "full" in result.summary.lower()

    @pytest.mark.asyncio
    async def test_daily_loss_limit_skips(self, db):
        """Discovery skips when daily loss limit is reached."""
        # Create and close a losing position to accumulate loss
        pos = await _insert_position(
            db, entry_price=10.0, quantity_token=10.0, notional_usd=100.0,
        )
        await db.close_portfolio_position(
            position_id=pos.id,
            exit_price=4.0,
            close_reason="stop_loss",
            realized_pnl_usd=-60.0,
        )

        engine = _make_engine(db, daily_loss_limit_usd=50.0)

        result = await engine.run_discovery_cycle()

        assert "daily loss limit" in result.summary.lower()


# ---------------------------------------------------------------------------
# Risk guards
# ---------------------------------------------------------------------------


class TestRiskGuards:
    """Test risk guards in the strategy engine."""

    @pytest.mark.asyncio
    async def test_exit_reason_stop_loss(self, db):
        engine = _make_engine(db)
        now = datetime.now(timezone.utc)
        pos = PortfolioPosition(
            id=1, token_address="t", symbol="T", chain="solana",
            entry_price=1.0, quantity_token=10, notional_usd=10,
            stop_price=0.92, take_price=1.15, highest_price=1.0,
            opened_at=now,
        )

        assert engine._exit_reason(pos, 0.91, now) == "stop_loss"
        assert engine._exit_reason(pos, 0.92, now) == "stop_loss"

    @pytest.mark.asyncio
    async def test_exit_reason_take_profit(self, db):
        engine = _make_engine(db)
        now = datetime.now(timezone.utc)
        pos = PortfolioPosition(
            id=1, token_address="t", symbol="T", chain="solana",
            entry_price=1.0, quantity_token=10, notional_usd=10,
            stop_price=0.92, take_price=1.15, highest_price=1.0,
            opened_at=now,
        )

        assert engine._exit_reason(pos, 1.15, now) == "take_profit"
        assert engine._exit_reason(pos, 1.50, now) == "take_profit"

    @pytest.mark.asyncio
    async def test_exit_reason_max_hold(self, db):
        engine = _make_engine(db, max_hold_hours=24)
        now = datetime.now(timezone.utc)
        pos = PortfolioPosition(
            id=1, token_address="t", symbol="T", chain="solana",
            entry_price=1.0, quantity_token=10, notional_usd=10,
            stop_price=0.92, take_price=1.15, highest_price=1.0,
            opened_at=now - timedelta(hours=25),
        )

        assert engine._exit_reason(pos, 1.05, now) == "max_hold_time"

    @pytest.mark.asyncio
    async def test_exit_reason_none_in_range(self, db):
        engine = _make_engine(db)
        now = datetime.now(timezone.utc)
        pos = PortfolioPosition(
            id=1, token_address="t", symbol="T", chain="solana",
            entry_price=1.0, quantity_token=10, notional_usd=10,
            stop_price=0.92, take_price=1.15, highest_price=1.0,
            opened_at=now,
        )

        assert engine._exit_reason(pos, 1.05, now) is None


# ---------------------------------------------------------------------------
# Reference price parsing
# ---------------------------------------------------------------------------


class TestParseReferenceResult:
    """Test the DexScreener response parser."""

    def test_parses_pairs_list(self):
        result = {
            "pairs": [{"priceUsd": "1.50", "liquidity": {"usd": 25000}}]
        }
        price, liq = PortfolioStrategyEngine._parse_reference_result(result)
        assert price == 1.50
        assert liq == 25000.0

    def test_parses_raw_list(self):
        result = [{"priceUsd": "2.00", "liquidity": {"usd": 10000}}]
        price, liq = PortfolioStrategyEngine._parse_reference_result(result)
        assert price == 2.00
        assert liq == 10000.0

    def test_raises_on_empty(self):
        with pytest.raises(RuntimeError, match="no pairs"):
            PortfolioStrategyEngine._parse_reference_result({"pairs": []})

    def test_raises_on_missing_price(self):
        with pytest.raises(RuntimeError, match="missing priceUsd"):
            PortfolioStrategyEngine._parse_reference_result(
                {"pairs": [{"liquidity": {"usd": 100}}]}
            )

    def test_handles_missing_liquidity(self):
        result = {"pairs": [{"priceUsd": "1.0"}]}
        price, liq = PortfolioStrategyEngine._parse_reference_result(result)
        assert price == 1.0
        assert liq is None
