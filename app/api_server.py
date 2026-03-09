"""FastAPI HTTP server wrapping TokenAnalyzer for the paid analysis service."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.config import load_settings
from app.mcp_client import MCPManager
from app.token_analyzer import AnalysisReport, TokenAnalyzer, normalize_chain_identifier

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
    try:
        yield
    finally:
        mcp_manager = _mcp_manager
        _token_analyzer = None
        _mcp_manager = None
        if mcp_manager:
            await mcp_manager.shutdown()


app = FastAPI(title="DEX Analysis API", lifespan=lifespan)


class AnalyzeRequest(BaseModel):
    address: str
    chain: Optional[str] = None


class PriceDataResponse(BaseModel):
    price_usd: Optional[float] = None
    change_24h_percent: Optional[float] = None
    market_cap_usd: Optional[float] = None
    volume_24h_usd: Optional[float] = None
    fdv_usd: Optional[float] = None


class LiquidityResponse(BaseModel):
    total_usd: Optional[float] = None
    top_pool: Optional[str] = None
    top_pool_liquidity_usd: Optional[float] = None


class SafetyResponse(BaseModel):
    status: str
    risk_score: Optional[float] = None
    risk_level: str = "unknown"
    flags: List[str] = Field(default_factory=list)


class HolderSnapshotResponse(BaseModel):
    top_10_holders_percent: Optional[float] = None
    concentration_risk: str = "unknown"


class AIAnalysisResponse(BaseModel):
    key_strengths: List[str] = Field(default_factory=list)
    key_risks: List[str] = Field(default_factory=list)
    whale_signal: str = "unknown"
    narrative_momentum: str = "neutral"


class VerdictResponse(BaseModel):
    action: str = "hold"
    confidence: str = "low"
    one_sentence: str = "Insufficient data for analysis."


class AnalyzeResponse(BaseModel):
    token: str
    chain: str
    address: str
    timestamp: str
    price_data: PriceDataResponse
    liquidity: LiquidityResponse
    safety: SafetyResponse
    holder_snapshot: Optional[HolderSnapshotResponse] = None
    ai_analysis: AIAnalysisResponse
    verdict: VerdictResponse
    human_readable: str


@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze_token(request: AnalyzeRequest) -> AnalyzeResponse:
    if not _token_analyzer:
        raise HTTPException(status_code=503, detail="Analysis service not ready")

    address = request.address.strip()
    if not address:
        raise HTTPException(status_code=400, detail="address is required")

    normalized_chain = normalize_chain_identifier(request.chain)

    try:
        report: AnalysisReport = await _token_analyzer.analyze(
            address,
            normalized_chain,
            structured=True,
            legacy_output=False,
        )
    except Exception as exc:
        logger.exception("Analysis failed for %s", address)
        raise HTTPException(status_code=500, detail="Analysis failed due to an internal error") from exc

    structured = report.structured
    if not structured:
        raise HTTPException(status_code=500, detail="Structured report generation failed")

    holder_snapshot = None
    if structured.holder_snapshot:
        holder_snapshot = HolderSnapshotResponse(**structured.holder_snapshot)

    return AnalyzeResponse(
        token=structured.token,
        chain=structured.chain,
        address=structured.address,
        timestamp=structured.timestamp,
        price_data=PriceDataResponse(**structured.price_data),
        liquidity=LiquidityResponse(**structured.liquidity),
        safety=SafetyResponse(**structured.safety),
        holder_snapshot=holder_snapshot,
        ai_analysis=AIAnalysisResponse(**structured.ai_analysis),
        verdict=VerdictResponse(**structured.verdict),
        human_readable=structured.human_readable,
    )


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {"status": "ok", "ready": _token_analyzer is not None}
