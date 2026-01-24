"""Application configuration management."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment or `.env`."""

    model_config = SettingsConfigDict(
        env_file=(".env",),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    gemini_api_key: str = Field(..., alias="GEMINI_API_KEY")
    gemini_model: str = Field(
        default="gemini-2.5-flash",
        alias="GEMINI_MODEL",
    )

    mcp_dexscreener_cmd: str = Field(
        default="node /path/to/mcp-dexscreener/index.js",
        alias="MCP_DEXSCREENER_CMD",
    )
    mcp_dexpaprika_cmd: str = Field(
        default="dexpaprika-mcp",
        alias="MCP_DEXPAPRIKA_CMD",
    )
    mcp_honeypot_cmd: str = Field(
        default="node /path/to/dex-honeypot-mcp/dist/index.js",
        alias="MCP_HONEYPOT_CMD",
    )
    mcp_rugcheck_cmd: str = Field(
        default="",
        alias="MCP_RUGCHECK_CMD",
    )

    agentic_max_iterations: int = Field(
        default=8, alias="AGENTIC_MAX_ITERATIONS", ge=1, le=15
    )
    agentic_max_tool_calls: int = Field(
        default=30, alias="AGENTIC_MAX_TOOL_CALLS", ge=1, le=100
    )
    agentic_timeout_seconds: int = Field(
        default=90, alias="AGENTIC_TIMEOUT_SECONDS", ge=10, le=300
    )

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # Watchlist settings
    watchlist_db_path: Path = Field(
        default=Path.home() / ".dex-bot" / "watchlist.db",
        alias="WATCHLIST_DB_PATH",
    )
    watchlist_poll_interval: int = Field(
        default=60, alias="WATCHLIST_POLL_INTERVAL", ge=10, le=3600
    )
    watchlist_poll_enabled: bool = Field(
        default=True, alias="WATCHLIST_POLL_ENABLED"
    )

    # Telegram settings
    telegram_bot_token: str = Field(
        default="", alias="TELEGRAM_BOT_TOKEN"
    )
    telegram_chat_id: str = Field(
        default="", alias="TELEGRAM_CHAT_ID"
    )
    telegram_alerts_enabled: bool = Field(
        default=False, alias="TELEGRAM_ALERTS_ENABLED"
    )
    telegram_subscribers_db_path: Path = Field(
        default=Path.home() / ".dex-bot" / "telegram_subscribers.db",
        alias="TELEGRAM_SUBSCRIBERS_DB_PATH",
    )

    # Autonomous agent settings
    autonomous_enabled: bool = Field(
        default=False, alias="AUTONOMOUS_ENABLED"
    )
    autonomous_interval_mins: int = Field(
        default=60, alias="AUTONOMOUS_INTERVAL_MINS", ge=5, le=1440
    )
    autonomous_max_tokens: int = Field(
        default=5, alias="AUTONOMOUS_MAX_TOKENS", ge=1, le=20
    )
    autonomous_chain: str = Field(
        default="solana", alias="AUTONOMOUS_CHAIN"
    )
    autonomous_min_volume_usd: float = Field(
        default=10000.0, alias="AUTONOMOUS_MIN_VOLUME_USD", ge=0
    )
    autonomous_min_liquidity_usd: float = Field(
        default=5000.0, alias="AUTONOMOUS_MIN_LIQUIDITY_USD", ge=0
    )


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    """Return cached Settings instance, raising a helpful message on failure."""
    try:
        return Settings()
    except ValidationError as exc:
        raise RuntimeError(f"Invalid configuration: {exc}") from exc


__all__ = ["Settings", "load_settings"]
