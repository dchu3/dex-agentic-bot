"""Application configuration management."""

from __future__ import annotations

from functools import lru_cache
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


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    """Return cached Settings instance, raising a helpful message on failure."""
    try:
        return Settings()
    except ValidationError as exc:
        raise RuntimeError(f"Invalid configuration: {exc}") from exc


__all__ = ["Settings", "load_settings"]
