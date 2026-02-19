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

    # Solana RPC URL for on-chain lookups (e.g. token decimals)
    solana_rpc_url: str = Field(
        default="https://api.mainnet-beta.solana.com",
        alias="SOLANA_RPC_URL",
    )

    # Portfolio strategy settings (discover → hold → exit)
    portfolio_enabled: bool = Field(
        default=False, alias="PORTFOLIO_ENABLED"
    )
    portfolio_dry_run: bool = Field(
        default=True, alias="PORTFOLIO_DRY_RUN"
    )
    portfolio_chain: str = Field(
        default="solana", alias="PORTFOLIO_CHAIN"
    )
    portfolio_max_positions: int = Field(
        default=5, alias="PORTFOLIO_MAX_POSITIONS", ge=1, le=50
    )
    portfolio_position_size_usd: float = Field(
        default=5.0, alias="PORTFOLIO_POSITION_SIZE_USD", ge=0.01
    )
    portfolio_take_profit_pct: float = Field(
        default=15.0, alias="PORTFOLIO_TAKE_PROFIT_PCT", ge=0.0, le=500.0
    )
    portfolio_stop_loss_pct: float = Field(
        default=8.0, alias="PORTFOLIO_STOP_LOSS_PCT", ge=0.1, le=100.0
    )
    portfolio_trailing_stop_pct: float = Field(
        default=5.0, alias="PORTFOLIO_TRAILING_STOP_PCT", ge=0.1, le=100.0
    )
    portfolio_max_hold_hours: int = Field(
        default=24, alias="PORTFOLIO_MAX_HOLD_HOURS", ge=1, le=720
    )
    portfolio_discovery_interval_mins: int = Field(
        default=30, alias="PORTFOLIO_DISCOVERY_INTERVAL_MINS", ge=5, le=1440
    )
    portfolio_price_check_seconds: int = Field(
        default=60, alias="PORTFOLIO_PRICE_CHECK_SECONDS", ge=10, le=3600
    )
    portfolio_daily_loss_limit_usd: float = Field(
        default=50.0, alias="PORTFOLIO_DAILY_LOSS_LIMIT_USD", ge=0
    )
    portfolio_min_volume_usd: float = Field(
        default=50000.0, alias="PORTFOLIO_MIN_VOLUME_USD", ge=0
    )
    portfolio_min_liquidity_usd: float = Field(
        default=25000.0, alias="PORTFOLIO_MIN_LIQUIDITY_USD", ge=0
    )
    portfolio_min_market_cap_usd: float = Field(
        default=250000.0, alias="PORTFOLIO_MIN_MARKET_CAP_USD", ge=0
    )
    portfolio_min_token_age_hours: float = Field(
        default=4.0, alias="PORTFOLIO_MIN_TOKEN_AGE_HOURS", ge=0
    )
    portfolio_cooldown_seconds: int = Field(
        default=300, alias="PORTFOLIO_COOLDOWN_SECONDS", ge=0, le=86400
    )
    portfolio_min_momentum_score: float = Field(
        default=50.0, alias="PORTFOLIO_MIN_MOMENTUM_SCORE", ge=0, le=100
    )
    portfolio_max_slippage_bps: int = Field(
        default=100, alias="PORTFOLIO_MAX_SLIPPAGE_BPS", ge=1, le=5000
    )
    portfolio_quote_mint: str = Field(
        default="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        alias="PORTFOLIO_QUOTE_MINT",
    )
    portfolio_quote_method: str = Field(
        default="", alias="PORTFOLIO_QUOTE_METHOD"
    )
    portfolio_execute_method: str = Field(
        default="", alias="PORTFOLIO_EXECUTE_METHOD"
    )
    portfolio_slippage_probe_enabled: bool = Field(
        default=False, alias="PORTFOLIO_SLIPPAGE_PROBE_ENABLED"
    )
    portfolio_slippage_probe_usd: float = Field(
        default=0.50, alias="PORTFOLIO_SLIPPAGE_PROBE_USD", ge=0.10
    )
    portfolio_slippage_probe_max_slippage_pct: float = Field(
        default=5.0, alias="PORTFOLIO_SLIPPAGE_PROBE_MAX_SLIPPAGE_PCT", ge=0.1, le=100.0
    )


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    """Return cached Settings instance, raising a helpful message on failure."""
    try:
        return Settings()
    except ValidationError as exc:
        raise RuntimeError(f"Invalid configuration: {exc}") from exc


__all__ = ["Settings", "load_settings"]
