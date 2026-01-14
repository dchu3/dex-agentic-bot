"""Background scheduler for autonomous watchlist management."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from app.autonomous_agent import (
        AutonomousWatchlistAgent,
        AutonomousCycleResult,
        TokenCandidate,
        WatchlistReview,
    )
    from app.mcp_client import MCPManager
    from app.telegram_notifier import TelegramNotifier
    from app.watchlist import WatchlistDB

# Type alias for log callback
LogCallback = Callable[[str, str, Optional[Dict[str, Any]]], None]


class AutonomousScheduler:
    """Background scheduler for autonomous watchlist management cycles."""

    def __init__(
        self,
        agent: "AutonomousWatchlistAgent",
        db: "WatchlistDB",
        telegram: Optional["TelegramNotifier"] = None,
        interval_seconds: int = 3600,  # 60 minutes default
        max_tokens: int = 5,
        verbose: bool = False,
        log_callback: Optional[LogCallback] = None,
    ) -> None:
        self.agent = agent
        self.db = db
        self.telegram = telegram
        self.interval_seconds = interval_seconds
        self.max_tokens = max_tokens
        self.verbose = verbose
        self.log_callback = log_callback

        self._task: Optional[asyncio.Task[None]] = None
        self._running = False
        self._last_cycle: Optional[datetime] = None
        self._last_result: Optional["AutonomousCycleResult"] = None
        self._cycle_count = 0

    @property
    def is_running(self) -> bool:
        """Check if the scheduler is running."""
        return self._running and self._task is not None and not self._task.done()

    @property
    def last_cycle(self) -> Optional[datetime]:
        """Get timestamp of last cycle."""
        return self._last_cycle

    @property
    def last_result(self) -> Optional["AutonomousCycleResult"]:
        """Get result of last cycle."""
        return self._last_result

    @property
    def cycle_count(self) -> int:
        """Get total number of cycles run."""
        return self._cycle_count

    def _log(self, level: str, message: str, data: Optional[Dict[str, Any]] = None) -> None:
        """Log a message if verbose mode is enabled."""
        if self.verbose and self.log_callback:
            self.log_callback(level, message, data)

    async def start(self) -> None:
        """Start the autonomous scheduler."""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._schedule_loop())
        self._log("info", f"Autonomous scheduler started (interval: {self.interval_seconds}s)")

    async def stop(self) -> None:
        """Stop the autonomous scheduler."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._log("info", "Autonomous scheduler stopped")

    async def run_cycle_now(self) -> "AutonomousCycleResult":
        """Manually trigger an autonomous cycle immediately."""
        return await self._run_cycle()

    async def _schedule_loop(self) -> None:
        """Main scheduling loop."""
        while self._running:
            try:
                await self._run_cycle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._log("error", f"Cycle failed: {str(e)}")

            # Wait for next interval
            try:
                await asyncio.sleep(self.interval_seconds)
            except asyncio.CancelledError:
                break

    async def _run_cycle(self) -> "AutonomousCycleResult":
        """Execute a single autonomous cycle."""
        from app.autonomous_agent import AutonomousCycleResult

        self._cycle_count += 1
        self._last_cycle = datetime.utcnow()
        self._log("info", f"Starting autonomous cycle #{self._cycle_count}")

        result = AutonomousCycleResult(timestamp=self._last_cycle)

        try:
            # Step 1: Review existing watchlist
            existing_entries = await self.db.list_autonomous_entries()
            reviews = await self.agent.review_watchlist(existing_entries)

            # Step 2: Process reviews - remove, update, or keep
            slots_to_fill = await self._process_reviews(reviews, result)

            # Step 3: If we have slots, discover new tokens
            current_count = await self.db.count_autonomous_entries()
            available_slots = self.max_tokens - current_count

            if available_slots > 0:
                candidates = await self.agent.discover_tokens()
                await self._add_new_tokens(candidates, available_slots, result)

            # Step 4: Generate summary
            result.summary = self._generate_summary(result)
            self._log("info", f"Cycle complete: {result.summary}")

            # Step 5: Send Telegram notification
            if self.telegram and self.telegram.is_configured:
                await self._send_cycle_notification(result)

        except Exception as e:
            result.errors.append(str(e))
            self._log("error", f"Cycle error: {str(e)}")

        self._last_result = result
        return result

    async def _process_reviews(
        self, reviews: List["WatchlistReview"], result: "AutonomousCycleResult"
    ) -> int:
        """Process review decisions and return number of slots freed."""
        slots_freed = 0

        for review in reviews:
            try:
                if review.action == "remove":
                    # Remove the token
                    removed = await self.db.remove_autonomous_entry(
                        review.token_address, "solana"
                    )
                    if removed:
                        result.tokens_removed.append(review.symbol)
                        slots_freed += 1
                        self._log("info", f"Removed {review.symbol}: {review.reasoning}")

                elif review.action == "update":
                    # Update triggers and score
                    await self.db.update_autonomous_entry(
                        entry_id=review.entry_id,
                        alert_above=review.new_alert_above,
                        alert_below=review.new_alert_below,
                        momentum_score=review.new_momentum_score,
                        review_notes=review.reasoning,
                    )
                    result.tokens_updated.append(review)
                    self._log("info", f"Updated {review.symbol}: {review.reasoning}")

                elif review.action == "keep":
                    # Just update the review timestamp and notes
                    await self.db.update_autonomous_entry(
                        entry_id=review.entry_id,
                        review_notes=review.reasoning,
                    )
                    self._log("info", f"Keeping {review.symbol}: {review.reasoning}")

            except Exception as e:
                result.errors.append(f"Failed to process {review.symbol}: {str(e)}")

        return slots_freed

    async def _add_new_tokens(
        self,
        candidates: List["TokenCandidate"],
        max_to_add: int,
        result: "AutonomousCycleResult",
    ) -> None:
        """Add new token candidates to the watchlist."""
        added = 0

        # Sort by momentum score descending
        sorted_candidates = sorted(
            candidates, key=lambda c: c.momentum_score, reverse=True
        )

        for candidate in sorted_candidates:
            if added >= max_to_add:
                break

            try:
                # Check if already in watchlist
                existing = await self.db.get_entry(
                    token_address=candidate.token_address, chain=candidate.chain
                )
                if existing:
                    self._log("info", f"Skipping {candidate.symbol}: already in watchlist")
                    continue

                # Add to watchlist
                await self.db.add_autonomous_entry(
                    token_address=candidate.token_address,
                    symbol=candidate.symbol,
                    chain=candidate.chain,
                    alert_above=candidate.alert_above,
                    alert_below=candidate.alert_below,
                    momentum_score=candidate.momentum_score,
                    review_notes=candidate.reasoning,
                )

                result.tokens_added.append(candidate)
                added += 1
                self._log(
                    "info",
                    f"Added {candidate.symbol} @ ${candidate.current_price:.8f} "
                    f"(score: {candidate.momentum_score})",
                )

            except Exception as e:
                result.errors.append(f"Failed to add {candidate.symbol}: {str(e)}")

    def _generate_summary(self, result: "AutonomousCycleResult") -> str:
        """Generate a human-readable summary of the cycle."""
        parts = []

        if result.tokens_added:
            symbols = [c.symbol for c in result.tokens_added]
            parts.append(f"Added: {', '.join(symbols)}")

        if result.tokens_removed:
            parts.append(f"Removed: {', '.join(result.tokens_removed)}")

        if result.tokens_updated:
            symbols = [r.symbol for r in result.tokens_updated]
            parts.append(f"Updated: {', '.join(symbols)}")

        if result.errors:
            parts.append(f"Errors: {len(result.errors)}")

        return " | ".join(parts) if parts else "No changes"

    async def _send_cycle_notification(self, result: "AutonomousCycleResult") -> None:
        """Send Telegram notification about the cycle results."""
        if not self.telegram:
            return

        message = self._format_cycle_message(result)
        try:
            await self.telegram.send_message(message)
        except Exception as e:
            self._log("error", f"Failed to send Telegram notification: {str(e)}")

    def _format_cycle_message(self, result: "AutonomousCycleResult") -> str:
        """Format cycle results as a Telegram message."""
        timestamp = result.timestamp.strftime("%Y-%m-%d %H:%M UTC")
        lines = [
            "🤖 <b>Autonomous Watchlist Update</b>",
            f"⏰ {timestamp}",
            "",
        ]

        # Added tokens
        if result.tokens_added:
            lines.append("📈 <b>New Positions:</b>")
            for candidate in result.tokens_added:
                price_fmt = self._format_price(candidate.current_price)
                change_emoji = "🟢" if candidate.price_change_24h >= 0 else "🔴"
                lines.append(
                    f"  • <b>{candidate.symbol}</b> @ {price_fmt} "
                    f"{change_emoji} {candidate.price_change_24h:+.1f}%"
                )
                lines.append(
                    f"    📊 Score: {candidate.momentum_score:.0f} | "
                    f"Vol: ${candidate.volume_24h:,.0f}"
                )
            lines.append("")

        # Removed tokens
        if result.tokens_removed:
            lines.append("📉 <b>Closed Positions:</b>")
            for symbol in result.tokens_removed:
                lines.append(f"  • {symbol}")
            lines.append("")

        # Updated tokens
        if result.tokens_updated:
            lines.append("🔄 <b>Updated Triggers:</b>")
            for review in result.tokens_updated:
                above_fmt = self._format_price(review.new_alert_above) if review.new_alert_above else "—"
                below_fmt = self._format_price(review.new_alert_below) if review.new_alert_below else "—"
                lines.append(
                    f"  • <b>{review.symbol}</b>: ↑{above_fmt} ↓{below_fmt}"
                )
            lines.append("")

        # Errors
        if result.errors:
            lines.append(f"⚠️ {len(result.errors)} error(s) during cycle")

        # Summary
        lines.append(f"📋 {result.summary}")

        return "\n".join(lines)

    @staticmethod
    def _format_price(price: Optional[float]) -> str:
        """Format price with appropriate precision."""
        if price is None:
            return "—"
        if price >= 1:
            return f"${price:,.4f}"
        elif price >= 0.0001:
            return f"${price:.6f}"
        else:
            return f"${price:.10f}"

    def get_status(self) -> Dict[str, Any]:
        """Get current scheduler status."""
        return {
            "running": self.is_running,
            "interval_seconds": self.interval_seconds,
            "max_tokens": self.max_tokens,
            "cycle_count": self._cycle_count,
            "last_cycle": self._last_cycle.isoformat() if self._last_cycle else None,
            "last_summary": self._last_result.summary if self._last_result else None,
        }
