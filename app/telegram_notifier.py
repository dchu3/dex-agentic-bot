"""Telegram bot integration for sending price alerts."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import List, Optional, TYPE_CHECKING

import httpx

from app.telegram_subscribers import SubscriberDB

if TYPE_CHECKING:
    from app.watchlist_poller import TriggeredAlert

TELEGRAM_API_BASE = "https://api.telegram.org/bot"

HELP_MESSAGE = """🤖 <b>DEX Agentic Bot</b>

I send you price alerts when your watched tokens cross thresholds.

<b>Commands (in CLI):</b>
• <code>/watch &lt;token&gt; [chain]</code> - Add to watchlist
• <code>/alert &lt;token&gt; above|below &lt;price&gt;</code> - Set alert
• <code>/watchlist</code> - View watched tokens
• <code>/alerts</code> - View triggered alerts

<b>Telegram Commands:</b>
• /subscribe - Subscribe to price alerts
• /unsubscribe - Unsubscribe from alerts
• /help - Show this help
• /status - Check bot status

This bot is notification-only. Use the CLI for full functionality.
"""


class TelegramNotifier:
    """Async Telegram bot client for sending alert notifications."""

    def __init__(
        self,
        bot_token: str,
        chat_id: str = "",
        timeout: float = 10.0,
        poll_interval: float = 2.0,
        subscribers_db_path: Optional[Path] = None,
    ) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id  # Legacy: used for backwards compatibility
        self.timeout = timeout
        self.poll_interval = poll_interval
        self._client: Optional[httpx.AsyncClient] = None
        self._polling_task: Optional[asyncio.Task] = None
        self._last_update_id: int = 0
        self._running: bool = False
        self._subscribers_db = SubscriberDB(subscribers_db_path)

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def close(self) -> None:
        """Close the HTTP client and stop polling."""
        await self.stop_polling()
        await self._subscribers_db.close()
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    @property
    def is_configured(self) -> bool:
        """Check if Telegram credentials are configured."""
        return bool(self.bot_token)

    @property
    def is_polling(self) -> bool:
        """Check if the bot is currently polling for messages."""
        return self._running and self._polling_task is not None

    async def start_polling(self) -> None:
        """Start polling for incoming messages."""
        if self._running or not self.is_configured:
            return
        
        # Auto-subscribe legacy chat_id if configured (backwards compatibility)
        if self.chat_id:
            await self._subscribers_db.add_subscriber(self.chat_id)
        
        self._running = True
        self._polling_task = asyncio.create_task(self._poll_loop())

    async def stop_polling(self) -> None:
        """Stop polling for incoming messages."""
        self._running = False
        if self._polling_task:
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass
            self._polling_task = None

    async def _poll_loop(self) -> None:
        """Main polling loop for incoming messages."""
        while self._running:
            try:
                await self._poll_updates()
            except asyncio.CancelledError:
                break
            except Exception:
                pass
            
            try:
                await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                break

    async def _poll_updates(self) -> None:
        """Fetch and process new messages from Telegram."""
        client = await self._get_client()
        url = f"{TELEGRAM_API_BASE}{self.bot_token}/getUpdates"
        params = {
            "offset": self._last_update_id + 1,
            "timeout": 1,
            "allowed_updates": ["message"],
        }
        
        try:
            response = await client.get(url, params=params)
            data = response.json()
            
            if not data.get("ok"):
                return
            
            for update in data.get("result", []):
                self._last_update_id = update.get("update_id", self._last_update_id)
                await self._handle_update(update)
        except Exception:
            pass

    async def _handle_update(self, update: dict) -> None:
        """Handle an incoming update from Telegram."""
        message = update.get("message", {})
        chat_id = str(message.get("chat", {}).get("id", ""))
        text = message.get("text", "").strip()
        username = message.get("from", {}).get("username")
        
        if not chat_id or not text:
            return
        
        # Handle commands from any user
        if text.startswith("/"):
            await self._handle_command(text.lower(), chat_id, username)

    async def _handle_command(
        self, command: str, chat_id: str, username: Optional[str] = None
    ) -> None:
        """Handle a bot command."""
        cmd = command.split()[0].split("@")[0]
        
        if cmd in ("/help", "/start"):
            await self.send_message_to(chat_id, HELP_MESSAGE)
        elif cmd == "/status":
            await self._send_status(chat_id)
        elif cmd == "/subscribe":
            await self._handle_subscribe(chat_id, username)
        elif cmd == "/unsubscribe":
            await self._handle_unsubscribe(chat_id)

    async def _handle_subscribe(
        self, chat_id: str, username: Optional[str] = None
    ) -> None:
        """Handle /subscribe command."""
        was_subscribed = await self._subscribers_db.is_subscribed(chat_id)
        await self._subscribers_db.add_subscriber(chat_id, username)
        
        if was_subscribed:
            message = "✅ You're already subscribed to price alerts."
        else:
            message = (
                "✅ <b>Subscribed!</b>\n\n"
                "You will now receive price alerts when watched tokens cross thresholds.\n\n"
                "Use /unsubscribe to stop receiving alerts."
            )
        await self.send_message_to(chat_id, message)

    async def _handle_unsubscribe(self, chat_id: str) -> None:
        """Handle /unsubscribe command."""
        removed = await self._subscribers_db.remove_subscriber(chat_id)
        
        if removed:
            message = (
                "🔕 <b>Unsubscribed</b>\n\n"
                "You will no longer receive price alerts.\n\n"
                "Use /subscribe to re-enable alerts."
            )
        else:
            message = "ℹ️ You weren't subscribed to alerts."
        await self.send_message_to(chat_id, message)

    async def _send_status(self, chat_id: str) -> None:
        """Send bot status message."""
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        is_subscribed = await self._subscribers_db.is_subscribed(chat_id)
        subscriber_count = await self._subscribers_db.get_subscriber_count()
        sub_status = "✅ Subscribed" if is_subscribed else "❌ Not subscribed"
        message = (
            "✅ <b>Bot Status</b>\n\n"
            f"<b>Status:</b> Online\n"
            f"<b>Time:</b> {timestamp}\n"
            f"<b>Your alerts:</b> {sub_status}\n"
            f"<b>Total subscribers:</b> {subscriber_count}\n\n"
            "Monitoring your watchlist for price alerts."
        )
        await self.send_message_to(chat_id, message)

    async def test_connection(self) -> bool:
        """Test if the bot token is valid by calling getMe."""
        if not self.is_configured:
            return False

        try:
            client = await self._get_client()
            url = f"{TELEGRAM_API_BASE}{self.bot_token}/getMe"
            response = await client.get(url)
            data = response.json()
            return data.get("ok", False)
        except Exception:
            return False

    async def send_message(
        self,
        text: str,
        parse_mode: str = "HTML",
        disable_notification: bool = False,
    ) -> bool:
        """Send a message to the legacy configured chat (backwards compatibility).
        
        Args:
            text: Message text (supports HTML formatting)
            parse_mode: Message parse mode (HTML or Markdown)
            disable_notification: Send silently
            
        Returns:
            True if message was sent successfully
        """
        if not self.is_configured or not self.chat_id:
            return False
        return await self.send_message_to(
            self.chat_id, text, parse_mode, disable_notification
        )

    async def send_message_to(
        self,
        chat_id: str,
        text: str,
        parse_mode: str = "HTML",
        disable_notification: bool = False,
    ) -> bool:
        """Send a message to a specific chat.
        
        Args:
            chat_id: Target chat ID
            text: Message text (supports HTML formatting)
            parse_mode: Message parse mode (HTML or Markdown)
            disable_notification: Send silently
            
        Returns:
            True if message was sent successfully
        """
        if not self.is_configured:
            return False

        try:
            client = await self._get_client()
            url = f"{TELEGRAM_API_BASE}{self.bot_token}/sendMessage"
            payload = {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_notification": disable_notification,
            }
            response = await client.post(url, json=payload)
            data = response.json()
            return data.get("ok", False)
        except Exception:
            return False

    async def broadcast_message(
        self,
        text: str,
        parse_mode: str = "HTML",
        disable_notification: bool = False,
    ) -> int:
        """Send a message to all subscribers.
        
        Args:
            text: Message text (supports HTML formatting)
            parse_mode: Message parse mode (HTML or Markdown)
            disable_notification: Send silently
            
        Returns:
            Number of successfully sent messages
        """
        subscribers = await self._subscribers_db.get_all_subscribers()
        success_count = 0
        for sub in subscribers:
            if await self.send_message_to(
                sub.chat_id, text, parse_mode, disable_notification
            ):
                success_count += 1
        return success_count

    async def send_alert(self, alert: "TriggeredAlert") -> bool:
        """Send a formatted price alert to all subscribers.
        
        Args:
            alert: The triggered alert to send
            
        Returns:
            True if alert was sent to at least one subscriber
        """
        message = self._format_alert(alert)
        sent_count = await self.broadcast_message(message)
        return sent_count > 0

    def _format_alert(self, alert: "TriggeredAlert") -> str:
        """Format a TriggeredAlert as a Telegram message."""
        if alert.alert_type == "above":
            emoji = "🔺"
            direction = "Crossed above"
        else:
            emoji = "🔻"
            direction = "Dropped below"

        threshold = self._format_price(alert.threshold)
        current = self._format_price(alert.current_price)
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

        # Build market cap line if available
        market_cap_line = ""
        if alert.market_cap is not None:
            market_cap_line = f"<b>Market Cap:</b> {self._format_market_cap(alert.market_cap)}\n"

        # Build liquidity line if available
        liquidity_line = ""
        if alert.liquidity is not None:
            liquidity_line = f"<b>Liquidity:</b> {self._format_liquidity(alert.liquidity)}\n"

        return (
            f"🔔 <b>Price Alert</b>\n\n"
            f"<b>Token:</b> {alert.symbol}\n"
            f"<b>Chain:</b> {alert.chain}\n"
            f"<b>Type:</b> {emoji} {direction} {threshold}\n"
            f"<b>Current Price:</b> {current}\n"
            f"{market_cap_line}"
            f"{liquidity_line}"
            f"<b>Contract:</b> <code>{alert.token_address}</code>\n\n"
            f"⏰ {timestamp}"
        )

    @staticmethod
    def _format_price(price: float) -> str:
        """Format price with appropriate precision."""
        if price >= 1:
            return f"${price:,.4f}"
        elif price >= 0.0001:
            return f"${price:.6f}"
        else:
            return f"${price:.10f}"

    @staticmethod
    def _format_market_cap(market_cap: float) -> str:
        """Format market cap with appropriate suffix (K, M, B, T)."""
        if market_cap >= 1_000_000_000_000:
            return f"${market_cap / 1_000_000_000_000:.2f}T"
        elif market_cap >= 1_000_000_000:
            return f"${market_cap / 1_000_000_000:.2f}B"
        elif market_cap >= 1_000_000:
            return f"${market_cap / 1_000_000:.2f}M"
        elif market_cap >= 1_000:
            return f"${market_cap / 1_000:.2f}K"
        else:
            return f"${market_cap:,.0f}"

    @staticmethod
    def _format_liquidity(liquidity: float) -> str:
        """Format liquidity with appropriate suffix (K, M, B)."""
        if liquidity >= 1_000_000_000:
            return f"${liquidity / 1_000_000_000:.2f}B"
        elif liquidity >= 1_000_000:
            return f"${liquidity / 1_000_000:.2f}M"
        elif liquidity >= 1_000:
            return f"${liquidity / 1_000:.2f}K"
        else:
            return f"${liquidity:,.0f}"

    # --- Autonomous Watchlist Notifications ---

    async def send_token_added(
        self,
        symbol: str,
        chain: str,
        price: float,
        momentum_score: float,
        alert_above: float,
        alert_below: float,
        reasoning: str,
    ) -> bool:
        """Send notification when a token is added to autonomous watchlist."""
        price_fmt = self._format_price(price)
        above_fmt = self._format_price(alert_above)
        below_fmt = self._format_price(alert_below)
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

        message = (
            f"📈 <b>New Position Added</b>\n\n"
            f"<b>Token:</b> {symbol}\n"
            f"<b>Chain:</b> {chain}\n"
            f"<b>Entry Price:</b> {price_fmt}\n"
            f"<b>Momentum Score:</b> {momentum_score:.0f}/100\n\n"
            f"<b>Triggers:</b>\n"
            f"  🎯 Take Profit: {above_fmt}\n"
            f"  🛑 Stop Loss: {below_fmt}\n\n"
            f"<b>Reasoning:</b>\n{reasoning}\n\n"
            f"⏰ {timestamp}"
        )
        return await self.broadcast_message(message) > 0

    async def send_token_removed(
        self,
        symbol: str,
        chain: str,
        reasoning: str,
    ) -> bool:
        """Send notification when a token is removed from autonomous watchlist."""
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

        message = (
            f"📉 <b>Position Closed</b>\n\n"
            f"<b>Token:</b> {symbol}\n"
            f"<b>Chain:</b> {chain}\n\n"
            f"<b>Reason:</b>\n{reasoning}\n\n"
            f"⏰ {timestamp}"
        )
        return await self.broadcast_message(message) > 0

    async def send_trigger_updated(
        self,
        symbol: str,
        chain: str,
        old_above: Optional[float],
        new_above: Optional[float],
        old_below: Optional[float],
        new_below: Optional[float],
        reasoning: str,
    ) -> bool:
        """Send notification when price triggers are updated."""
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

        lines = [
            f"🔄 <b>Triggers Updated</b>\n",
            f"<b>Token:</b> {symbol}",
            f"<b>Chain:</b> {chain}\n",
        ]

        if new_above is not None:
            old_fmt = self._format_price(old_above) if old_above else "—"
            new_fmt = self._format_price(new_above)
            lines.append(f"<b>Take Profit:</b> {old_fmt} → {new_fmt}")

        if new_below is not None:
            old_fmt = self._format_price(old_below) if old_below else "—"
            new_fmt = self._format_price(new_below)
            lines.append(f"<b>Stop Loss:</b> {old_fmt} → {new_fmt}")

        lines.extend([
            f"\n<b>Reason:</b>\n{reasoning}",
            f"\n⏰ {timestamp}",
        ])

        return await self.broadcast_message("\n".join(lines)) > 0

    async def send_watchlist_summary(
        self,
        entries: list,
        cycle_number: int,
    ) -> bool:
        """Send periodic watchlist summary."""
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

        lines = [
            f"📊 <b>Watchlist Summary</b> (Cycle #{cycle_number})\n",
            f"⏰ {timestamp}\n",
        ]

        if not entries:
            lines.append("No tokens in autonomous watchlist.")
        else:
            lines.append(f"<b>{len(entries)} Active Positions:</b>\n")
            for entry in entries:
                price_fmt = self._format_price(entry.last_price) if entry.last_price else "—"
                score = entry.momentum_score or 0
                lines.append(
                    f"• <b>{entry.symbol}</b> @ {price_fmt} (Score: {score:.0f})"
                )

        return await self.broadcast_message("\n".join(lines)) > 0
