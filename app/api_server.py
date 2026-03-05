"""FastAPI HTTP server wrapping TokenAnalyzer for the paid analysis service."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.config import load_settings
from app.mcp_client import MCPManager
from app.token_analyzer import TokenAnalyzer, AnalysisReport

logger = logging.getLogger(__name__)

_mcp_manager: Optional[MCPManager] = None
_token_analyzer: Optional[TokenAnalyzer] = None


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[type-arg]
    global _mcp_manager, _token_analyzer
    settings = load_settings()

    _mcp_manager = MCPManager(
        dexscreener_cmd=settings.mcp_dexscreener_cmd,
        dexpaprika_cmd=settings.mcp_dexpaprika_cmd,
        honeypot_cmd=settings.mcp_honeypot_cmd,
        rugcheck_cmd=settings.mcp_rugcheck_cmd,
        solana_rpc_cmd=settings.mcp_solana_rpc_cmd,
        blockscout_cmd=settings.mcp_blockscout_cmd,
        call_timeout=float(settings.mcp_call_timeout),
        solana_rpc_url=settings.solana_rpc_url,
    )

    await _mcp_manager.start()

    _token_analyzer = TokenAnalyzer(
        api_key=settings.gemini_api_key,
        mcp_manager=_mcp_manager,
        model_name=settings.gemini_model,
    )

    logger.info("Analysis server ready")
    yield

    if _mcp_manager:
        await _mcp_manager.shutdown()


app = FastAPI(title="DEX Analysis API", lifespan=lifespan)


class AnalyzeRequest(BaseModel):
    address: str
    chain: Optional[str] = None


class AnalyzeResponse(BaseModel):
    address: str
    chain: str
    symbol: Optional[str] = None
    name: Optional[str] = None
    safety_status: str
    ai_analysis: str
    telegram_message: str
    generated_at: str


@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze_token(request: AnalyzeRequest) -> AnalyzeResponse:
    if not _token_analyzer:
        raise HTTPException(status_code=503, detail="Analysis service not ready")

    address = request.address.strip()
    if not address:
        raise HTTPException(status_code=400, detail="address is required")

    try:
        report: AnalysisReport = await _token_analyzer.analyze(address, request.chain)
    except Exception as exc:
        logger.exception("Analysis failed for %s", address)
        raise HTTPException(status_code=500, detail=f"Analysis failed: {exc}") from exc

    return AnalyzeResponse(
        address=report.token_data.address,
        chain=report.token_data.chain,
        symbol=report.token_data.symbol,
        name=report.token_data.name,
        safety_status=report.token_data.safety_status,
        ai_analysis=report.ai_analysis,
        telegram_message=report.telegram_message,
        generated_at=report.generated_at.isoformat(),
    )


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {"status": "ok", "ready": _token_analyzer is not None}
