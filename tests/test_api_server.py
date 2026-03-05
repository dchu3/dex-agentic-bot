"""Tests for FastAPI analysis server endpoints."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

import app.api_server as api_server
from app.token_analyzer import AnalysisReport, TokenData


@pytest.fixture(autouse=True)
def reset_server_state(monkeypatch):
    """Reset module-level state so tests do not depend on app lifespan startup."""
    monkeypatch.setattr(api_server, "_token_analyzer", None)
    monkeypatch.setattr(api_server, "_mcp_manager", None)


@pytest.mark.asyncio
async def test_analyze_returns_503_when_service_not_ready():
    transport = ASGITransport(app=api_server.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/analyze", json={"address": "0x123"})

    assert response.status_code == 503
    assert response.json()["detail"] == "Analysis service not ready"


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
async def test_analyze_happy_path_normalizes_chain(monkeypatch):
    mock_analyzer = MagicMock()
    mock_analyzer.analyze = AsyncMock(
        return_value=AnalysisReport(
            token_data=TokenData(
                address="0x123",
                chain="ethereum",
                symbol="PEPE",
                name="Pepe",
                safety_status="Safe",
            ),
            ai_analysis="Looks healthy.",
            generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            telegram_message="Token Analysis Report",
        )
    )
    monkeypatch.setattr(api_server, "_token_analyzer", mock_analyzer)

    transport = ASGITransport(app=api_server.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/analyze", json={"address": " 0x123 ", "chain": "   "})

    assert response.status_code == 200
    assert response.json()["address"] == "0x123"
    assert response.json()["chain"] == "ethereum"
    mock_analyzer.analyze.assert_awaited_once_with("0x123", None)


@pytest.mark.asyncio
async def test_analyze_internal_error_returns_generic_message(monkeypatch):
    mock_analyzer = MagicMock()
    mock_analyzer.analyze = AsyncMock(side_effect=RuntimeError("secret failure details"))
    monkeypatch.setattr(api_server, "_token_analyzer", mock_analyzer)

    transport = ASGITransport(app=api_server.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/analyze", json={"address": "0x123", "chain": "ethereum"})

    assert response.status_code == 500
    assert response.json()["detail"] == "Analysis failed due to an internal error"
