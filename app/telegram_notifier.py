"""Telegram bot integration for sending price alerts."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional, TYPE_CHECKING

import httpx

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
• /help - Show this help
• /status - Check bot status

This bot is notification-only. Use the CLI for full functionality.
"""


class TelegramNotifier:
    """Async Telegram bot client for sending alert notifications."""

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        timeout: float = 10.0,
        poll_interval: float = 2.0,
    ) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.timeout = timeout
        self.poll_interval = poll_interval
        self._client: Optional[httpx.AsyncClient] = None
        self._polling_task: Optional[asyncio.Task] = None
        self._last_update_id: int = 0
        self._running: bool = False

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def close(self) -> None:
        """Close the HTTP client and stop polling."""
        await self.stop_polling()
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    @property
    def is_configured(self) -> bool:
        """Check if Telegram credentials are configured."""
        return bool(self.bot_token and self.chat_id)

    @property
    def is_polling(self) -> bool:
        """Check if the bot is currently polling for messages."""
        return self._running and self._polling_task is not None

    async def start_polling(self) -> None:
        """Start polling for incoming messages."""
        if self._running or not self.is_configured:
            return
        
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
        
        # Only respond to messages from the configured chat
        if chat_id != self.chat_id:
            return
        
        # Handle commands
        if text.startswith("/"):
            await self._handle_command(text.lower())

    async def _handle_command(self, command: str) -> None:
        """Handle a bot command."""
        cmd = command.split()[0].split("@")[0]
        
        if cmd in ("/help", "/start"):
            await self.send_message(HELP_MESSAGE)
        elif cmd == "/status":
            await self._send_status()

    async def _send_status(self) -> None:
        """Send bot status message."""
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        message = (
            "✅ <b>Bot Status</b>\n\n"
            f"<b>Status:</b> Online\n"
            f"<b>Time:</b> {timestamp}\n\n"
            "Monitoring your watchlist for price alerts."
        )
        await self.send_message(message)

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
        """Send a message to the configured chat.
        
        Args:
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
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_notification": disable_notification,
            }
            response = await client.post(url, json=payload)
            data = response.json()
            return data.get("ok", False)
        except Exception:
            return False

    async def send_alert(self, alert: "TriggeredAlert") -> bool:
        """Send a formatted price alert to Telegram.
        
        Args:
            alert: The triggered alert to send
            
        Returns:
            True if alert was sent successfully
        """
        message = self._format_alert(alert)
        return await self.send_message(message)

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

        return (
            f"🔔 <b>Price Alert</b>\n\n"
            f"<b>Token:</b> {alert.symbol}\n"
            f"<b>Chain:</b> {alert.chain}\n"
            f"<b>Type:</b> {emoji} {direction} {threshold}\n"
            f"<b>Current Price:</b> {current}\n\n"
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
