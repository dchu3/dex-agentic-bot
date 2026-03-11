"""Tests for FastAPI analysis server endpoints."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

import app.api_server as api_server
from app.token_analyzer import (
    AnalysisReport,
    TokenData,
    StructuredAnalysisReport,
)

_VALID_EVM_ADDR = "0x" + "a" * 40


def _make_structured_report(**overrides):
    """Build a StructuredAnalysisReport with sensible defaults."""
    defaults = dict(
        token="PEPE",
        chain="ethereum",
        address=_VALID_EVM_ADDR,
        timestamp="2026-01-01T00:00:00Z",
        price_data={
            "price_usd": 0.00001234,
            "change_24h_percent": 5.5,
            "market_cap_usd": 5000000000,
            "volume_24h_usd": 1000000,
            "fdv_usd": 5000000000,
        },
        liquidity={
            "total_usd": 5000000,
            "top_pool": "uniswap",
            "top_pool_liquidity_usd": 3000000,
        },
        safety={
            "status": "safe",
            "risk_score": 0.0,
            "risk_level": "low",
            "flags": [],
        },
        holder_snapshot=None,
        ai_analysis={
            "key_strengths": ["good liquidity"],
            "key_risks": ["meme volatility"],
            "whale_signal": "none detected",
            "narrative_momentum": "positive",
        },
        verdict={
            "action": "buy",
            "confidence": "medium",
            "one_sentence": "Solid token with good fundamentals.",
        },
        human_readable="🔍 Token Analysis Report\nPEPE on Ethereum",
    )
    defaults.update(overrides)
    return StructuredAnalysisReport(**defaults)


@asynccontextmanager
async def _noop_lifespan(_app):
    yield


@pytest.fixture(autouse=True)
def reset_server_state(monkeypatch):
    """Reset module-level state so tests do not depend on app lifespan startup."""
    monkeypatch.setattr(api_server, "_token_analyzer", None)
    monkeypatch.setattr(api_server, "_mcp_manager", None)
    monkeypatch.setattr(api_server.app.router, "lifespan_context", _noop_lifespan)


@pytest.mark.asyncio
async def test_analyze_returns_503_when_service_not_ready():
    transport = ASGITransport(app=api_server.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/analyze", json={"address": _VALID_EVM_ADDR})

    assert response.status_code == 503
    assert response.json()["detail"] == "Analysis service not ready"


@pytest.mark.asyncio
async def test_health_returns_not_ready_by_default():
    transport = ASGITransport(app=api_server.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "ready": False}


@pytest.mark.asyncio
async def test_health_returns_ready_when_analyzer_set(monkeypatch):
    monkeypatch.setattr(api_server, "_token_analyzer", MagicMock())

    transport = ASGITransport(app=api_server.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "ready": True}


@pytest.mark.asyncio
async def test_analyze_returns_400_for_blank_address(monkeypatch):
    mock_analyzer = MagicMock()
    mock_analyzer.analyze = AsyncMock()
    monkeypatch.setattr(api_server, "_token_analyzer", mock_analyzer)

    transport = ASGITransport(app=api_server.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/analyze", json={"address": "   ", "chain": "solana"})

    assert response.status_code == 400
    assert response.json()["detail"] == "address is required"
    mock_analyzer.analyze.assert_not_awaited()


@pytest.mark.asyncio
async def test_analyze_happy_path_returns_structured_response(monkeypatch):
    structured = _make_structured_report()
    mock_analyzer = MagicMock()
    mock_analyzer.analyze = AsyncMock(
        return_value=AnalysisReport(
            token_data=TokenData(
                address=_VALID_EVM_ADDR,
                chain="ethereum",
                symbol="PEPE",
                name="Pepe",
                safety_status="Safe",
            ),
            ai_analysis="Looks healthy.",
            generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            telegram_message="Token Analysis Report",
            structured=structured,
        )
    )
    monkeypatch.setattr(api_server, "_token_analyzer", mock_analyzer)

    transport = ASGITransport(app=api_server.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/analyze", json={"address": f" {_VALID_EVM_ADDR} ", "chain": " ETH "})

    assert response.status_code == 200
    data = response.json()

    # Verify structured response shape
    assert data["token"] == "PEPE"
    assert data["chain"] == "ethereum"
    assert data["address"] == _VALID_EVM_ADDR
    assert "price_data" in data
    assert data["price_data"]["price_usd"] == 0.00001234
    assert "liquidity" in data
    assert "safety" in data
    assert data["safety"]["status"] == "safe"
    assert "ai_analysis" in data
    assert "key_strengths" in data["ai_analysis"]
    assert "verdict" in data
    assert data["verdict"]["action"] == "buy"
    assert "human_readable" in data

    mock_analyzer.analyze.assert_awaited_once_with(
        _VALID_EVM_ADDR,
        "ethereum",
        structured=True,
        legacy_output=False,
    )


@pytest.mark.asyncio
async def test_analyze_with_holder_snapshot(monkeypatch):
    structured = _make_structured_report(
        holder_snapshot={
            "top_10_holders_percent": 38.4,
            "concentration_risk": "medium",
        }
    )
    mock_analyzer = MagicMock()
    mock_analyzer.analyze = AsyncMock(
        return_value=AnalysisReport(
            token_data=TokenData(address=_VALID_EVM_ADDR, chain="ethereum", safety_status="Safe"),
            ai_analysis="Report.",
            generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            telegram_message="Report",
            structured=structured,
        )
    )
    monkeypatch.setattr(api_server, "_token_analyzer", mock_analyzer)

    transport = ASGITransport(app=api_server.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/analyze", json={"address": _VALID_EVM_ADDR})

    assert response.status_code == 200
    data = response.json()
    assert data["holder_snapshot"]["top_10_holders_percent"] == 38.4
    assert data["holder_snapshot"]["concentration_risk"] == "medium"


@pytest.mark.asyncio
async def test_analyze_internal_error_returns_generic_message(monkeypatch):
    mock_analyzer = MagicMock()
    mock_analyzer.analyze = AsyncMock(side_effect=RuntimeError("secret failure details"))
    monkeypatch.setattr(api_server, "_token_analyzer", mock_analyzer)

    transport = ASGITransport(app=api_server.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/analyze", json={"address": _VALID_EVM_ADDR, "chain": "ethereum"})

    assert response.status_code == 500
    assert response.json()["detail"] == "Analysis failed due to an internal error"
