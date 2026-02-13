"""Background scheduler for lag-edge strategy cycles."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, TYPE_CHECKING

from app.formatting import format_price

if TYPE_CHECKING:
    from app.lag_strategy import LagCycleResult, LagStrategyEngine
    from app.telegram_notifier import TelegramNotifier

LogCallback = Callable[[str, str, Optional[Dict[str, Any]]], None]


class LagStrategyScheduler:
    """Runs lag strategy cycles on a fixed interval."""

    def __init__(
        self,
        engine: "LagStrategyEngine",
        interval_seconds: int,
        telegram: Optional["TelegramNotifier"] = None,
        verbose: bool = False,
        log_callback: Optional[LogCallback] = None,
    ) -> None:
        self.engine = engine
        self.interval_seconds = interval_seconds
        self.telegram = telegram
        self.verbose = verbose
        self.log_callback = log_callback

        self._task: Optional[asyncio.Task[None]] = None
        self._running = False
        self._cycle_count = 0
        self._last_cycle: Optional[datetime] = None
        self._last_result: Optional["LagCycleResult"] = None

    def _log(self, level: str, message: str, data: Optional[Dict[str, Any]] = None) -> None:
        if self.verbose and self.log_callback:
            self.log_callback(level, message, data)

    @property
    def is_running(self) -> bool:
        return self._running and self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        self._log("info", f"Lag strategy scheduler started ({self.interval_seconds}s)")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._log("info", "Lag strategy scheduler stopped")

    async def run_cycle_now(self) -> "LagCycleResult":
        return await self._run_cycle()

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._run_cycle()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._log("error", f"Lag cycle failed: {exc}")

            try:
                await asyncio.sleep(self.interval_seconds)
            except asyncio.CancelledError:
                break

    async def _run_cycle(self) -> "LagCycleResult":
        from app.lag_strategy import LagCycleResult

        self._cycle_count += 1
        self._last_cycle = datetime.now(timezone.utc)
        self._log("info", f"Starting lag cycle #{self._cycle_count}")
        result: LagCycleResult = await self.engine.run_cycle()
        self._last_result = result

        if self.telegram and self.telegram.is_configured:
            if result.entries_opened or result.positions_closed or result.errors:
                await self._send_notification(result)

        self._log("info", f"Lag cycle #{self._cycle_count}: {result.summary}")
        return result

    async def _send_notification(self, result: "LagCycleResult") -> None:
        if not self.telegram:
            return
        message = self._format_message(result)
        try:
            await self.telegram.send_message(message)
        except Exception as exc:
            self._log("error", f"Failed to send lag Telegram notification: {exc}")

    def _format_message(self, result: "LagCycleResult") -> str:
        lines = [
            "⚡ <b>Lag Strategy Cycle</b>",
            f"⏰ {result.timestamp.strftime('%Y-%m-%d %H:%M UTC')}",
            f"📊 {result.summary}",
            "",
        ]

        if result.entries_opened:
            lines.append("🟢 <b>Opened Positions</b>")
            for position in result.entries_opened:
                lines.append(
                    f"• {position.symbol}: entry {format_price(position.entry_price)} "
                    f"qty {position.quantity_token:.4f}"
                )
            lines.append("")

        if result.positions_closed:
            lines.append("🔴 <b>Closed Positions</b>")
            for position in result.positions_closed:
                pnl = position.realized_pnl_usd if position.realized_pnl_usd is not None else 0.0
                lines.append(f"• {position.symbol}: PnL ${pnl:,.2f}")
            lines.append("")

        if result.errors:
            lines.append(f"⚠️ {len(result.errors)} error(s)")

        return "\n".join(lines)

    def get_status(self) -> Dict[str, Any]:
        return {
            "running": self.is_running,
            "interval_seconds": self.interval_seconds,
            "cycle_count": self._cycle_count,
            "last_cycle": self._last_cycle.isoformat() if self._last_cycle else None,
            "last_summary": self._last_result.summary if self._last_result else None,
        }
