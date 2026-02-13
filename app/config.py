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
    mcp_solana_rpc_cmd: str = Field(
        default="",
        alias="MCP_SOLANA_RPC_CMD",
    )
    mcp_blockscout_cmd: str = Field(
        default="",
        alias="MCP_BLOCKSCOUT_CMD",
    )
    mcp_trader_cmd: str = Field(
        default="",
        alias="MCP_TRADER_CMD",
    )

    agentic_max_iterations: int = Field(
        default=15, alias="AGENTIC_MAX_ITERATIONS", ge=1, le=25
    )
    agentic_max_tool_calls: int = Field(
        default=30, alias="AGENTIC_MAX_TOOL_CALLS", ge=1, le=100
    )
    agentic_timeout_seconds: int = Field(
        default=120, alias="AGENTIC_TIMEOUT_SECONDS", ge=10, le=300
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
        default=False, alias="WATCHLIST_POLL_ENABLED"
    )

    # Price cache settings
    price_cache_ttl_seconds: int = Field(
        default=30, alias="PRICE_CACHE_TTL_SECONDS", ge=5, le=300
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
    telegram_private_mode: bool = Field(
        default=True, alias="TELEGRAM_PRIVATE_MODE"
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

    # Lag strategy settings (Solana-first)
    lag_strategy_enabled: bool = Field(
        default=False, alias="LAG_STRATEGY_ENABLED"
    )
    lag_strategy_dry_run: bool = Field(
        default=True, alias="LAG_STRATEGY_DRY_RUN"
    )
    lag_strategy_interval_seconds: int = Field(
        default=20, alias="LAG_STRATEGY_INTERVAL_SECONDS", ge=5, le=3600
    )
    lag_strategy_chain: str = Field(
        default="solana", alias="LAG_STRATEGY_CHAIN"
    )
    lag_strategy_sample_notional_usd: float = Field(
        default=25.0, alias="LAG_STRATEGY_SAMPLE_NOTIONAL_USD", ge=0.01
    )
    lag_strategy_min_edge_bps: float = Field(
        default=30.0, alias="LAG_STRATEGY_MIN_EDGE_BPS", ge=0.1
    )
    lag_strategy_min_liquidity_usd: float = Field(
        default=10000.0, alias="LAG_STRATEGY_MIN_LIQUIDITY_USD", ge=0
    )
    lag_strategy_max_slippage_bps: int = Field(
        default=100, alias="LAG_STRATEGY_MAX_SLIPPAGE_BPS", ge=1, le=5000
    )
    lag_strategy_max_position_usd: float = Field(
        default=25.0, alias="LAG_STRATEGY_MAX_POSITION_USD", ge=0.01
    )
    lag_strategy_max_open_positions: int = Field(
        default=2, alias="LAG_STRATEGY_MAX_OPEN_POSITIONS", ge=1, le=20
    )
    lag_strategy_cooldown_seconds: int = Field(
        default=180, alias="LAG_STRATEGY_COOLDOWN_SECONDS", ge=0, le=86400
    )
    lag_strategy_take_profit_bps: float = Field(
        default=150.0, alias="LAG_STRATEGY_TAKE_PROFIT_BPS", ge=1
    )
    lag_strategy_stop_loss_bps: float = Field(
        default=80.0, alias="LAG_STRATEGY_STOP_LOSS_BPS", ge=1
    )
    lag_strategy_max_hold_seconds: int = Field(
        default=1800, alias="LAG_STRATEGY_MAX_HOLD_SECONDS", ge=30, le=86400
    )
    lag_strategy_daily_loss_limit_usd: float = Field(
        default=50.0, alias="LAG_STRATEGY_DAILY_LOSS_LIMIT_USD", ge=0
    )
    lag_strategy_quote_method: str = Field(
        default="", alias="LAG_STRATEGY_QUOTE_METHOD"
    )
    lag_strategy_execute_method: str = Field(
        default="", alias="LAG_STRATEGY_EXECUTE_METHOD"
    )
    lag_strategy_quote_mint: str = Field(
        default="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        alias="LAG_STRATEGY_QUOTE_MINT",
    )

    # Alert auto-adjustment settings
    alert_auto_adjust_enabled: bool = Field(
        default=True, alias="ALERT_AUTO_ADJUST_ENABLED"
    )
    alert_take_profit_percent: float = Field(
        default=10.0, alias="ALERT_TAKE_PROFIT_PERCENT", ge=0.1, le=100.0
    )
    alert_stop_loss_percent: float = Field(
        default=5.0, alias="ALERT_STOP_LOSS_PERCENT", ge=0.1, le=100.0
    )


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    """Return cached Settings instance, raising a helpful message on failure."""
    try:
        return Settings()
    except ValidationError as exc:
        raise RuntimeError(f"Invalid configuration: {exc}") from exc


__all__ = ["Settings", "load_settings"]
