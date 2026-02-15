"""Deterministic lag-edge strategy engine (Solana-first)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

logger = logging.getLogger(__name__)

_ERROR_SKIP_SECONDS = 300  # 5 minutes
_NATIVE_PRICE_STALE_SECONDS = 120  # consider price stale after 2 minutes

from app.lag_execution import TradeQuote, TraderExecutionService
from app.price_cache import PriceCache
from app.watchlist import LagPosition, WatchlistEntry

if TYPE_CHECKING:
    from app.watchlist import WatchlistDB

# Type alias for verbose logging callbacks used elsewhere in the codebase.
LogCallback = Callable[[str, str, Optional[Dict[str, Any]]], None]


@dataclass
class LagStrategyConfig:
    """Runtime config for lag-edge strategy."""

    enabled: bool
    dry_run: bool
    interval_seconds: int
    chain: str
    sample_notional_usd: float
    min_edge_bps: float
    min_liquidity_usd: float
    max_slippage_bps: int
    max_position_usd: float
    max_open_positions: int
    cooldown_seconds: int
    take_profit_bps: float
    stop_loss_bps: float
    max_hold_seconds: int
    daily_loss_limit_usd: float
    max_total_exposure_usd: float = 0.0  # 0 = unlimited
    quote_method: str = ""
    execute_method: str = ""
    quote_mint: str = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    rpc_url: str = "https://api.mainnet-beta.solana.com"


@dataclass
class LagCycleResult:
    """Result payload for one lag strategy cycle."""

    timestamp: datetime
    samples_taken: int = 0
    signals_triggered: int = 0
    entries_opened: List[LagPosition] = field(default_factory=list)
    positions_closed: List[LagPosition] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    summary: str = ""


class LagStrategyEngine:
    """Runs lag-edge signal generation + execution with strict guardrails."""

    def __init__(
        self,
        db: "WatchlistDB",
        mcp_manager: Any,
        config: LagStrategyConfig,
        verbose: bool = False,
        log_callback: Optional[LogCallback] = None,
    ) -> None:
        self.db = db
        self.mcp_manager = mcp_manager
        self.config = config
        self.verbose = verbose
        self.log_callback = log_callback
        self.execution = TraderExecutionService(
            mcp_manager=mcp_manager,
            chain=config.chain,
            max_slippage_bps=config.max_slippage_bps,
            quote_method_override=config.quote_method,
            execute_method_override=config.execute_method,
            quote_mint=config.quote_mint,
            rpc_url=config.rpc_url,
        )
        self._skip_until: Dict[str, datetime] = {}
        self._native_price_usd: Optional[float] = None
        self._native_price_updated_at: Optional[datetime] = None
        self._sell_fail_counts: Dict[int, int] = {}  # position_id → consecutive failures
        self._ref_price_cache: Optional["PriceCache"] = None

    def _log(self, level: str, message: str, data: Optional[Dict[str, Any]] = None) -> None:
        if self.verbose and self.log_callback:
            self.log_callback(level, message, data)

    async def run_cycle(self) -> LagCycleResult:
        """Execute one full lag strategy cycle."""
        now = datetime.now(timezone.utc)
        result = LagCycleResult(timestamp=now)

        if not self.config.enabled:
            result.summary = "Lag strategy disabled"
            return result

        # Fetch native token price for USD→token conversion
        await self._refresh_native_price()

        if self._native_price_usd is None:
            result.summary = "Skipped: native token price unavailable"
            result.errors.append("Native token price is None after refresh")
            await self.db.record_lag_event(
                event_type="native_price_unavailable",
                message="Cycle skipped: unable to fetch native token price",
                severity="error",
            )
            return result

        stale = self._is_native_price_stale(now)
        if stale:
            logger.warning(
                "Native price is stale (updated %s); proceeding with caution",
                self._native_price_updated_at,
            )
            await self.db.record_lag_event(
                event_type="native_price_stale",
                message=f"Native price stale (last updated {self._native_price_updated_at})",
                severity="warning",
            )

        # Exit checks first to reduce risk before opening new positions.
        await self._process_exits(result, now)

        tokens = await self._tokens_for_monitoring()
        if not tokens:
            result.summary = "No Solana tokens available for lag strategy"
            return result

        for entry in tokens:
            key = entry.token_address.lower()
            skip_expires = self._skip_until.get(key)
            if skip_expires and now < skip_expires:
                logger.debug("Skipping %s until %s", entry.symbol, skip_expires.isoformat())
                continue
            self._skip_until.pop(key, None)
            try:
                await self._process_entry_candidate(entry, result, now)
            except Exception as exc:
                err = f"{entry.symbol}: {exc}"
                result.errors.append(err)
                self._skip_until[key] = now + timedelta(seconds=_ERROR_SKIP_SECONDS)
                logger.info("Skipping %s for %ds after error: %s", entry.symbol, _ERROR_SKIP_SECONDS, exc)
                await self.db.record_lag_event(
                    event_type="cycle_error",
                    message=err,
                    token_address=entry.token_address,
                    symbol=entry.symbol,
                    chain=entry.chain,
                    severity="error",
                )

        result.summary = self._build_summary(result)
        return result

    def _build_summary(self, result: LagCycleResult) -> str:
        parts: List[str] = [f"samples={result.samples_taken}", f"signals={result.signals_triggered}"]
        if result.entries_opened:
            parts.append(f"opened={len(result.entries_opened)}")
        if result.positions_closed:
            parts.append(f"closed={len(result.positions_closed)}")
        if result.errors:
            parts.append(f"errors={len(result.errors)}")
        return " | ".join(parts)

    async def _tokens_for_monitoring(self) -> List[WatchlistEntry]:
        entries = await self.db.list_autonomous_entries()
        if not entries:
            entries = await self.db.list_entries()

        from app.lag_execution import SOL_NATIVE_MINT

        excluded = {SOL_NATIVE_MINT.lower(), self.config.quote_mint.lower()}
        chain = self.config.chain.lower()
        deduped: Dict[str, WatchlistEntry] = {}
        for entry in entries:
            if entry.chain != chain:
                continue
            addr_lower = entry.token_address.lower()
            if addr_lower in excluded:
                continue
            deduped.setdefault(addr_lower, entry)
        return list(deduped.values())

    async def _process_entry_candidate(
        self,
        entry: WatchlistEntry,
        cycle_result: LagCycleResult,
        now: datetime,
    ) -> None:
        # Always sample even if position is already open.
        reference_price, reference_liquidity = await self._fetch_reference_price(
            token_address=entry.token_address,
            chain=entry.chain,
        )
        executable_quote = await self.execution.get_quote(
            token_address=entry.token_address,
            notional_usd=self.config.sample_notional_usd,
            side="buy",
            input_price_usd=self._native_price_usd,
        )
        executable_price = executable_quote.price
        edge_bps = self._compute_edge_bps(reference_price, executable_price)
        liquidity = reference_liquidity or executable_quote.liquidity_usd
        signal = edge_bps >= self.config.min_edge_bps

        await self.db.record_lag_snapshot(
            token_address=entry.token_address,
            symbol=entry.symbol,
            chain=entry.chain,
            reference_price=reference_price,
            executable_price=executable_price,
            edge_bps=edge_bps,
            liquidity_usd=liquidity,
            signal_triggered=signal,
        )
        cycle_result.samples_taken += 1

        if not signal:
            return

        cycle_result.signals_triggered += 1
        await self.db.record_lag_event(
            event_type="signal_triggered",
            message=f"Lag signal for {entry.symbol}: edge {edge_bps:.2f} bps",
            token_address=entry.token_address,
            symbol=entry.symbol,
            chain=entry.chain,
            data={
                "edge_bps": edge_bps,
                "reference_price": reference_price,
                "executable_price": executable_price,
            },
        )

        if liquidity is not None and liquidity < self.config.min_liquidity_usd:
            await self.db.record_lag_event(
                event_type="signal_skipped_liquidity",
                message=f"Skipped {entry.symbol}: liquidity {liquidity:.2f} < {self.config.min_liquidity_usd:.2f}",
                token_address=entry.token_address,
                symbol=entry.symbol,
                chain=entry.chain,
                severity="warning",
                data={"liquidity_usd": liquidity},
            )
            return

        existing = await self.db.get_open_lag_position(entry.token_address, entry.chain)
        if existing:
            return

        open_count = await self.db.count_open_lag_positions(self.config.chain)
        if open_count >= self.config.max_open_positions:
            await self.db.record_lag_event(
                event_type="risk_block_open_positions",
                message=f"Blocked entry for {entry.symbol}: max open positions reached",
                token_address=entry.token_address,
                symbol=entry.symbol,
                chain=entry.chain,
                severity="warning",
            )
            return

        if self.config.max_total_exposure_usd > 0:
            open_positions = await self.db.list_open_lag_positions(chain=self.config.chain)
            current_exposure = sum(p.notional_usd for p in open_positions)
            new_notional = min(self.config.max_position_usd, self.config.sample_notional_usd)
            if current_exposure + new_notional > self.config.max_total_exposure_usd:
                await self.db.record_lag_event(
                    event_type="risk_block_total_exposure",
                    message=f"Blocked entry for {entry.symbol}: total exposure ${current_exposure + new_notional:.2f} > limit ${self.config.max_total_exposure_usd:.2f}",
                    token_address=entry.token_address,
                    symbol=entry.symbol,
                    chain=entry.chain,
                    severity="warning",
                    data={"current_exposure_usd": current_exposure, "limit_usd": self.config.max_total_exposure_usd},
                )
                return

        daily_pnl = await self.db.get_daily_lag_realized_pnl(now)
        if daily_pnl <= -abs(self.config.daily_loss_limit_usd):
            await self.db.record_lag_event(
                event_type="risk_block_daily_loss",
                message=f"Blocked entry for {entry.symbol}: daily loss limit reached",
                token_address=entry.token_address,
                symbol=entry.symbol,
                chain=entry.chain,
                severity="warning",
                data={"daily_pnl_usd": daily_pnl},
            )
            return

        last_entry = await self.db.get_last_lag_entry_time(entry.token_address, entry.chain)
        if last_entry and (now - last_entry).total_seconds() < self.config.cooldown_seconds:
            await self.db.record_lag_event(
                event_type="cooldown_skip",
                message=f"Skipped {entry.symbol}: cooldown active",
                token_address=entry.token_address,
                symbol=entry.symbol,
                chain=entry.chain,
                data={"last_entry": last_entry.isoformat()},
            )
            return

        await self._open_position(entry, executable_quote, cycle_result)

    async def _open_position(
        self,
        entry: WatchlistEntry,
        quote: TradeQuote,
        cycle_result: LagCycleResult,
    ) -> None:
        notional = min(self.config.max_position_usd, self.config.sample_notional_usd)
        execution = await self.execution.execute_trade(
            token_address=entry.token_address,
            notional_usd=notional,
            side="buy",
            quantity_token=None,
            dry_run=self.config.dry_run,
            quote=quote,
            input_price_usd=self._native_price_usd,
        )

        quantity = execution.quantity_token
        if quantity is None:
            if quote.price <= 0:
                raise RuntimeError("Cannot derive quantity from quote price")
            quantity = notional / quote.price

        executed_price = execution.executed_price or quote.price
        if not execution.success:
            await self.db.record_lag_execution(
                position_id=None,
                token_address=entry.token_address,
                symbol=entry.symbol,
                chain=entry.chain,
                action="buy",
                requested_notional_usd=notional,
                executed_price=executed_price,
                quantity_token=quantity,
                tx_hash=execution.tx_hash,
                success=False,
                error=execution.error,
                metadata={"raw": execution.raw},
            )
            await self.db.record_lag_event(
                event_type="entry_failed",
                message=f"Entry failed for {entry.symbol}: {execution.error or 'unknown error'}",
                token_address=entry.token_address,
                symbol=entry.symbol,
                chain=entry.chain,
                severity="error",
            )
            return

        stop_price = executed_price * (1 - self.config.stop_loss_bps / 10_000)
        take_price = executed_price * (1 + self.config.take_profit_bps / 10_000)
        position = await self.db.add_lag_position(
            token_address=entry.token_address,
            symbol=entry.symbol,
            chain=entry.chain,
            entry_price=executed_price,
            quantity_token=quantity,
            notional_usd=notional,
            stop_price=stop_price,
            take_price=take_price,
            dry_run=self.config.dry_run,
        )
        await self.db.record_lag_execution(
            position_id=position.id,
            token_address=entry.token_address,
            symbol=entry.symbol,
            chain=entry.chain,
            action="buy",
            requested_notional_usd=notional,
            executed_price=executed_price,
            quantity_token=quantity,
            tx_hash=execution.tx_hash,
            success=True,
            error=None,
            metadata={"raw": execution.raw},
        )
        await self.db.record_lag_event(
            event_type="entry_opened",
            message=f"Opened {entry.symbol} lag position at {executed_price:.10f}",
            token_address=entry.token_address,
            symbol=entry.symbol,
            chain=entry.chain,
            data={
                "position_id": position.id,
                "entry_price": executed_price,
                "quantity_token": quantity,
                "dry_run": self.config.dry_run,
            },
        )
        cycle_result.entries_opened.append(position)

    async def _process_exits(
        self,
        cycle_result: LagCycleResult,
        now: datetime,
    ) -> None:
        positions = await self.db.list_open_lag_positions(chain=self.config.chain)
        for position in positions:
            try:
                await self._evaluate_exit(position, cycle_result, now)
            except (OSError, IOError) as exc:
                # Fatal I/O errors (DB, network socket) — re-raise to fail cycle
                raise
            except Exception as exc:
                err = f"Exit check failed for {position.symbol}: {exc}"
                cycle_result.errors.append(err)
                await self.db.record_lag_event(
                    event_type="exit_check_failed",
                    message=err,
                    token_address=position.token_address,
                    symbol=position.symbol,
                    chain=position.chain,
                    severity="error",
                )

    async def _evaluate_exit(
        self,
        position: LagPosition,
        cycle_result: LagCycleResult,
        now: datetime,
    ) -> None:
        quote = await self.execution.get_quote(
            token_address=position.token_address,
            notional_usd=position.notional_usd,
            side="sell",
            input_price_usd=self._native_price_usd,
            quantity_token=position.quantity_token,
        )
        current_price = quote.price
        close_reason = self._exit_reason(position, current_price, now)
        logger.info(
            "Exit check %s: price=%.10f stop=%.10f take=%.10f → %s",
            position.symbol, current_price, position.stop_price,
            position.take_price, close_reason or "hold",
        )
        if close_reason is None:
            return

        # Use actual wallet balance when available to avoid selling more
        # tokens than the wallet holds (quote outAmount ≠ actual received).
        sell_qty = position.quantity_token
        if not self.config.dry_run:
            actual_balance = await self.execution.get_wallet_token_balance(
                position.token_address,
            )
            if actual_balance is not None and actual_balance > 0:
                if actual_balance < sell_qty:
                    logger.info(
                        "Adjusting sell qty for %s: stored=%.6f wallet=%.6f",
                        position.symbol, sell_qty, actual_balance,
                    )
                sell_qty = min(sell_qty, actual_balance)

        requested_notional = current_price * sell_qty
        logger.info(
            "Exit triggered %s (%s): selling qty=%.6f notional=$%.4f",
            position.symbol, close_reason, sell_qty, requested_notional,
        )
        execution = await self.execution.execute_trade(
            token_address=position.token_address,
            notional_usd=requested_notional,
            side="sell",
            quantity_token=sell_qty,
            dry_run=self.config.dry_run,
            quote=quote,
            input_price_usd=self._native_price_usd,
        )
        exit_price = execution.executed_price or current_price
        logger.info(
            "Sell result %s: success=%s tx=%s price=%.10f error=%s",
            position.symbol, execution.success, execution.tx_hash,
            exit_price, execution.error,
        )

        if execution.success:
            realized_pnl = (exit_price - position.entry_price) * sell_qty
            closed = await self.db.close_lag_position(
                position_id=position.id,
                exit_price=exit_price,
                close_reason=close_reason,
                realized_pnl_usd=realized_pnl,
            )
            await self.db.record_lag_execution(
                position_id=position.id,
                token_address=position.token_address,
                symbol=position.symbol,
                chain=position.chain,
                action="sell",
                requested_notional_usd=requested_notional,
                executed_price=exit_price,
                quantity_token=sell_qty,
                tx_hash=execution.tx_hash,
                success=closed,
                error=None if closed else "Position close update failed",
                metadata={"raw": execution.raw},
            )
            if closed:
                self._sell_fail_counts.pop(position.id, None)
                position.exit_price = exit_price
                position.realized_pnl_usd = realized_pnl
                cycle_result.positions_closed.append(position)
                await self.db.record_lag_event(
                    event_type="position_closed",
                    message=f"Closed {position.symbol} ({close_reason}) at {exit_price:.10f}",
                    token_address=position.token_address,
                    symbol=position.symbol,
                    chain=position.chain,
                    data={"realized_pnl_usd": realized_pnl},
                )
        else:
            fail_count = self._sell_fail_counts.get(position.id, 0) + 1
            self._sell_fail_counts[position.id] = fail_count
            err_msg = f"Sell failed for {position.symbol} (attempt {fail_count}): {execution.error or 'unknown error'}"
            cycle_result.errors.append(err_msg)
            logger.warning(err_msg)
            await self.db.record_lag_execution(
                position_id=position.id,
                token_address=position.token_address,
                symbol=position.symbol,
                chain=position.chain,
                action="sell",
                requested_notional_usd=requested_notional,
                executed_price=exit_price,
                quantity_token=sell_qty,
                tx_hash=execution.tx_hash,
                success=False,
                error=execution.error,
                metadata={"raw": execution.raw},
            )
            await self.db.record_lag_event(
                event_type="exit_failed",
                message=err_msg,
                token_address=position.token_address,
                symbol=position.symbol,
                chain=position.chain,
                severity="error",
                data={"fail_count": fail_count},
            )
            if fail_count >= 3:
                await self.db.record_lag_event(
                    event_type="exit_stuck",
                    message=f"⚠️ Position {position.symbol} stuck: {fail_count} consecutive sell failures",
                    token_address=position.token_address,
                    symbol=position.symbol,
                    chain=position.chain,
                    severity="critical",
                    data={"fail_count": fail_count, "position_id": position.id},
                )

    def _exit_reason(
        self,
        position: LagPosition,
        current_price: float,
        now: datetime,
    ) -> Optional[str]:
        if current_price <= position.stop_price:
            return "stop_loss"
        if current_price >= position.take_price:
            return "take_profit"
        age_seconds = (now - position.opened_at).total_seconds()
        if age_seconds >= self.config.max_hold_seconds:
            return "max_hold_time"
        return None

    def _is_native_price_stale(self, now: datetime) -> bool:
        if self._native_price_updated_at is None:
            return True
        age = (now - self._native_price_updated_at).total_seconds()
        return age > _NATIVE_PRICE_STALE_SECONDS

    async def _refresh_native_price(self) -> None:
        """Fetch current native token (e.g. SOL) price in USD via DexScreener."""
        from app.lag_execution import SOL_NATIVE_MINT

        dexscreener = self.mcp_manager.get_client("dexscreener")
        if dexscreener is None:
            logger.warning("DexScreener not available; cannot fetch native price")
            return

        try:
            result = await dexscreener.call_tool(
                "get_token_pools",
                {"chainId": self.config.chain, "tokenAddress": SOL_NATIVE_MINT},
            )
            price, _ = self._parse_reference_result(result)
            if price and price > 0:
                self._native_price_usd = price
                self._native_price_updated_at = datetime.now(timezone.utc)
                logger.debug("Native token price: $%.4f", price)
        except Exception as exc:
            logger.warning("Failed to fetch native token price: %s", exc)

    async def _fetch_reference_price(self, token_address: str, chain: str) -> tuple[float, Optional[float]]:
        # Check cache first
        if self._ref_price_cache is None:
            self._ref_price_cache = PriceCache(ttl_seconds=15)
        cached = await self._ref_price_cache.get(chain, token_address)
        if cached is not None:
            return self._parse_reference_result(cached)

        dexscreener = self.mcp_manager.get_client("dexscreener")
        if dexscreener is None:
            raise RuntimeError("DexScreener MCP client is not configured")

        result = await dexscreener.call_tool(
            "get_token_pools",
            {"chainId": chain, "tokenAddress": token_address},
        )
        await self._ref_price_cache.set(chain, token_address, result)
        return self._parse_reference_result(result)

    @staticmethod
    def _parse_reference_result(result: Any) -> tuple[float, Optional[float]]:
        pairs: List[Dict[str, Any]] = []
        if isinstance(result, list):
            pairs = [pair for pair in result if isinstance(pair, dict)]
        elif isinstance(result, dict):
            raw_pairs = result.get("pairs", [])
            if isinstance(raw_pairs, list):
                pairs = [pair for pair in raw_pairs if isinstance(pair, dict)]

        if not pairs:
            raise RuntimeError("DexScreener returned no pairs")

        first = pairs[0]
        price_value = first.get("priceUsd")
        if price_value is None:
            raise RuntimeError("DexScreener pair missing priceUsd")

        try:
            price = float(price_value)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"Invalid DexScreener price: {price_value}") from exc

        liquidity_usd: Optional[float] = None
        liquidity = first.get("liquidity")
        if isinstance(liquidity, dict):
            liq_value = liquidity.get("usd")
            if liq_value is not None:
                try:
                    liquidity_usd = float(liq_value)
                except (TypeError, ValueError):
                    liquidity_usd = None

        return price, liquidity_usd

    @staticmethod
    def _compute_edge_bps(reference_price: float, executable_price: float) -> float:
        if reference_price <= 0:
            raise ValueError("Reference price must be > 0")
        if executable_price <= 0:
            raise ValueError("Executable price must be > 0")
        return ((reference_price - executable_price) / executable_price) * 10_000
