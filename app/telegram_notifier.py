"""Telegram bot integration for sending price alerts."""

from __future__ import annotations

from datetime import datetime
from typing import Optional, TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from app.watchlist_poller import TriggeredAlert

TELEGRAM_API_BASE = "https://api.telegram.org/bot"


class TelegramNotifier:
    """Async Telegram bot client for sending alert notifications."""

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        timeout: float = 10.0,
    ) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    @property
    def is_configured(self) -> bool:
        """Check if Telegram credentials are configured."""
        return bool(self.bot_token and self.chat_id)

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
