"""Token safety and market analysis module."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

from google import genai
from google.genai import types

from app.formatting import format_price, format_large_number

if TYPE_CHECKING:
    from app.mcp_client import MCPManager

# Type alias for log callback
LogCallback = Callable[[str, str, Optional[Dict[str, Any]]], None]

# Regex patterns for address detection
EVM_ADDRESS_PATTERN = re.compile(r"^0x[a-fA-F0-9]{40}$")
SOLANA_ADDRESS_PATTERN = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


@dataclass
class TokenData:
    """Raw data collected about a token."""

    address: str
    chain: str
    symbol: Optional[str] = None
    name: Optional[str] = None
    price_usd: Optional[float] = None
    price_change_24h: Optional[float] = None
    volume_24h: Optional[float] = None
    liquidity_usd: Optional[float] = None
    market_cap: Optional[float] = None
    fdv: Optional[float] = None
    pools: List[Dict[str, Any]] = field(default_factory=list)
    safety_data: Optional[Dict[str, Any]] = None
    safety_status: str = "Unknown"  # Safe, Risky, Honeypot, Unknown
    raw_dexscreener: Optional[Dict[str, Any]] = None
    errors: List[str] = field(default_factory=list)


@dataclass
class AnalysisReport:
    """Complete analysis report for a token."""

    token_data: TokenData
    ai_analysis: str
    generated_at: datetime
    telegram_message: str
    tweet_message: str = ""


def detect_chain(address: str) -> Optional[str]:
    """Detect blockchain from address format.
    
    Args:
        address: Token address to analyze
        
    Returns:
        Chain name ('ethereum', 'solana') or None if unrecognized
    """
    address = address.strip()
    
    if EVM_ADDRESS_PATTERN.match(address):
        return "ethereum"  # Default EVM chain, can be overridden
    
    if SOLANA_ADDRESS_PATTERN.match(address):
        # Additional check: Solana addresses don't start with 0x
        # and typically have mixed case
        if not address.startswith("0x"):
            return "solana"
    
    return None


def is_valid_token_address(text: str) -> bool:
    """Check if text looks like a valid token address.
    
    Args:
        text: Text to check
        
    Returns:
        True if text appears to be a token address
    """
    text = text.strip()
    return bool(EVM_ADDRESS_PATTERN.match(text) or SOLANA_ADDRESS_PATTERN.match(text))


# System prompt for token analysis
ANALYSIS_SYSTEM_PROMPT = """You are a crypto token analyst providing comprehensive safety and market analysis reports.

## Your Task
Analyze the provided token data and generate a clear, actionable report for the user.

## Analysis Focus Areas
1. **Safety Assessment**: Evaluate honeypot/rugcheck results, identify red flags
2. **Market Health**: Analyze liquidity depth, volume trends, price stability
3. **Risk Factors**: Identify potential concerns (low liquidity, high taxes, centralized ownership)
4. **Overall Rating**: Provide a clear safety verdict

## Response Format
Provide a concise analysis in 2-3 paragraphs:
1. Safety summary and any red flags
2. Market/liquidity analysis
3. Overall assessment and recommendation

Keep it brief but informative. Use plain text, no markdown formatting.
Do NOT repeat the raw data - the user already sees that in the report header.
Focus on INSIGHTS and INTERPRETATION of the data.
"""

TWEET_ANALYSIS_SYSTEM_PROMPT = """You are a crypto token analyst. Provide a single punchy sentence summarizing the token's safety and market outlook.

Keep it under 100 characters. No markdown. No disclaimers. Be direct and opinionated.
Examples: "Solid liquidity and clean contract — looks healthy.", "Low liquidity and high sell tax — proceed with caution."
"""


class TokenAnalyzer:
    """Analyzes tokens for safety and market data using MCP tools and AI."""

    def __init__(
        self,
        api_key: str,
        mcp_manager: "MCPManager",
        model_name: str = "gemini-2.5-flash",
        verbose: bool = False,
        log_callback: Optional[LogCallback] = None,
    ) -> None:
        self.mcp_manager = mcp_manager
        self.model_name = model_name
        self.verbose = verbose
        self.log_callback = log_callback
        self.client = genai.Client(api_key=api_key)

    def _log(self, level: str, message: str, data: Optional[Dict[str, Any]] = None) -> None:
        """Log a message if verbose mode is enabled."""
        if self.verbose and self.log_callback:
            self.log_callback(level, message, data)

    async def analyze(self, address: str, chain: Optional[str] = None) -> AnalysisReport:
        """Analyze a token and generate a comprehensive report.
        
        Args:
            address: Token contract address
            chain: Blockchain (auto-detected if not provided)
            
        Returns:
            Complete analysis report with AI insights
        """
        # Auto-detect chain if not provided
        if not chain:
            chain = detect_chain(address)
            if not chain:
                # Default to ethereum for unknown formats
                chain = "ethereum"
        
        self._log("info", f"Analyzing token {address} on {chain}")
        
        # Collect data from MCP tools
        token_data = await self._collect_token_data(address, chain)
        
        # Generate AI analysis
        ai_analysis = await self._generate_ai_analysis(token_data)
        
        # Generate tweet-length AI verdict
        tweet_verdict = await self._generate_tweet_verdict(token_data)
        
        # Format as Telegram messages
        telegram_message = self._format_telegram_report(token_data, ai_analysis)
        tweet_message = self._format_tweet_report(token_data, tweet_verdict)
        
        return AnalysisReport(
            token_data=token_data,
            ai_analysis=ai_analysis,
            generated_at=datetime.now(timezone.utc),
            telegram_message=telegram_message,
            tweet_message=tweet_message,
        )

    async def _collect_token_data(self, address: str, chain: str) -> TokenData:
        """Collect token data from various MCP sources."""
        token_data = TokenData(address=address, chain=chain)
        
        # Run data collection in parallel
        tasks = [
            self._fetch_dexscreener_data(address, chain, token_data),
            self._fetch_safety_data(address, chain, token_data),
        ]
        
        await asyncio.gather(*tasks, return_exceptions=True)
        
        return token_data

    async def _fetch_dexscreener_data(
        self, address: str, chain: str, token_data: TokenData
    ) -> None:
        """Fetch token data from DexScreener."""
        try:
            client = self.mcp_manager.get_client("dexscreener")
            if not client:
                token_data.errors.append("DexScreener client not available")
                return
            
            # For EVM addresses with default chain, use search_pairs to auto-detect actual chain
            if chain in ("ethereum", "eth") and EVM_ADDRESS_PATTERN.match(address):
                self._log("tool", f"→ dexscreener_search_pairs({address})")
                result = await client.call_tool("search_pairs", {"query": address})
                self._log("tool", "✓ dexscreener_search_pairs")
            else:
                self._log("tool", f"→ dexscreener_get_token_pools({chain}, {address})")
                result = await client.call_tool("get_token_pools", {
                    "chainId": chain,
                    "tokenAddress": address,
                })
                self._log("tool", "✓ dexscreener_get_token_pools")
            
            if not result:
                token_data.errors.append("No data from DexScreener")
                return
            
            # Handle MCP error strings
            if isinstance(result, str):
                if result.startswith("MCP error"):
                    token_data.errors.append(f"DexScreener: {result}")
                    return
                # Try to parse as JSON if it's a string
                try:
                    import json
                    result = json.loads(result)
                except (json.JSONDecodeError, TypeError):
                    token_data.errors.append(f"DexScreener: unexpected response format")
                    return
            
            token_data.raw_dexscreener = result
            
            # Parse the response - handle both list and dict formats
            pairs = []
            if isinstance(result, list):
                pairs = result
            elif isinstance(result, dict):
                pairs = result.get("pairs", []) or [result]
            
            if not pairs:
                token_data.errors.append("No pairs found for token")
                return
            
            # Get data from first/best pair
            best_pair = pairs[0]
            
            # Update chain from actual result (important for EVM auto-detection)
            actual_chain = best_pair.get("chainId")
            if actual_chain:
                token_data.chain = actual_chain
            
            # Extract base token info
            base_token = best_pair.get("baseToken", {})
            token_data.symbol = base_token.get("symbol", "Unknown")
            token_data.name = base_token.get("name", "Unknown")
            
            # Price data
            token_data.price_usd = self._safe_float(best_pair.get("priceUsd"))
            price_change = best_pair.get("priceChange", {})
            token_data.price_change_24h = self._safe_float(price_change.get("h24"))
            
            # Volume and liquidity
            volume = best_pair.get("volume", {})
            token_data.volume_24h = self._safe_float(volume.get("h24"))
            token_data.liquidity_usd = self._safe_float(
                best_pair.get("liquidity", {}).get("usd")
            )
            
            # Market cap / FDV
            token_data.market_cap = self._safe_float(best_pair.get("marketCap"))
            token_data.fdv = self._safe_float(best_pair.get("fdv"))
            
            # Collect pool info
            for pair in pairs[:5]:  # Top 5 pools
                pool_info = {
                    "dex": pair.get("dexId", "Unknown"),
                    "pair": pair.get("pairAddress", ""),
                    "liquidity": self._safe_float(pair.get("liquidity", {}).get("usd")),
                    "volume_24h": self._safe_float(pair.get("volume", {}).get("h24")),
                }
                token_data.pools.append(pool_info)
                
        except Exception as e:
            self._log("error", f"DexScreener fetch failed: {str(e)}")
            token_data.errors.append(f"DexScreener error: {str(e)}")

    async def _fetch_safety_data(
        self, address: str, chain: str, token_data: TokenData
    ) -> None:
        """Fetch safety data from appropriate source based on chain."""
        try:
            if chain == "solana":
                await self._fetch_rugcheck_data(address, token_data)
            elif chain in ("ethereum", "eth", "bsc", "base"):
                await self._fetch_honeypot_data(address, chain, token_data)
            else:
                token_data.safety_status = "Unverified"
                token_data.safety_data = {"note": f"Safety checks not available for {chain}"}
        except Exception as e:
            self._log("error", f"Safety check failed: {str(e)}")
            token_data.safety_status = "Unknown"
            token_data.errors.append(f"Safety check error: {str(e)}")

    async def _fetch_honeypot_data(
        self, address: str, chain: str, token_data: TokenData
    ) -> None:
        """Fetch honeypot data for EVM chains."""
        client = self.mcp_manager.get_client("honeypot")
        if not client:
            token_data.safety_status = "Unverified"
            token_data.errors.append("Honeypot client not available")
            return
        
        # Map chain names
        chain_map = {"ethereum": "ethereum", "eth": "ethereum", "bsc": "bsc", "base": "base"}
        api_chain = chain_map.get(chain.lower(), "ethereum")
        
        self._log("tool", f"→ honeypot_check_honeypot({address}, {api_chain})")
        result = await client.call_tool("check_honeypot", {
            "address": address,
            "chain": api_chain,
        })
        self._log("tool", "✓ honeypot_check_honeypot")
        
        token_data.safety_data = result
        
        # Parse safety status
        if isinstance(result, dict):
            is_honeypot = result.get("isHoneypot", False)
            honeypot_result = result.get("honeypotResult", {})
            
            if is_honeypot or honeypot_result.get("isHoneypot"):
                token_data.safety_status = "Honeypot"
            else:
                # Check for high taxes or other risks
                simulation = result.get("simulationResult", {})
                buy_tax = self._safe_float(simulation.get("buyTax", 0))
                sell_tax = self._safe_float(simulation.get("sellTax", 0))
                
                if buy_tax > 10 or sell_tax > 10:
                    token_data.safety_status = "Risky"
                else:
                    token_data.safety_status = "Safe"

    async def _fetch_rugcheck_data(
        self, address: str, token_data: TokenData
    ) -> None:
        """Fetch rugcheck data for Solana tokens."""
        client = self.mcp_manager.get_client("rugcheck")
        if not client:
            token_data.safety_status = "Unverified"
            token_data.errors.append("Rugcheck client not available")
            return
        
        self._log("tool", f"→ rugcheck_get_token_summary({address})")
        result = await client.call_tool("get_token_summary", {"token_address": address})
        self._log("tool", "✓ rugcheck_get_token_summary")
        
        # Handle MCP error strings
        if isinstance(result, str):
            if result.startswith("MCP error"):
                token_data.safety_status = "Unknown"
                token_data.errors.append(f"Rugcheck: {result}")
                return
        
        token_data.safety_data = result
        
        # Parse safety status from rugcheck response
        # Response format: {"score": 1, "score_normalised": 1, "risks": [], "lpLockedPct": ...}
        if isinstance(result, dict):
            score = result.get("score_normalised", result.get("score", 0))
            risks = result.get("risks", [])
            
            # Score interpretation: lower is better (fewer risks)
            if score <= 500 and not risks:
                token_data.safety_status = "Safe"
            elif score <= 2000 or len(risks) <= 2:
                token_data.safety_status = "Risky"
            else:
                token_data.safety_status = "Dangerous"

    async def _generate_ai_analysis(self, token_data: TokenData) -> str:
        """Generate AI analysis of the token data."""
        # Build context for AI
        context = self._build_analysis_context(token_data)
        
        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=context,
                config=types.GenerateContentConfig(
                    system_instruction=ANALYSIS_SYSTEM_PROMPT,
                ),
            )
            
            # Safely extract text from response
            if response and response.candidates:
                candidate = response.candidates[0]
                if candidate and candidate.content and candidate.content.parts:
                    for part in candidate.content.parts:
                        if hasattr(part, "text") and part.text:
                            return part.text.strip()
            
            return "Unable to generate AI analysis."
            
        except Exception as e:
            self._log("error", f"AI analysis failed: {str(e)}")
            return f"AI analysis unavailable: {str(e)}"

    def _build_analysis_context(self, token_data: TokenData) -> str:
        """Build context string for AI analysis."""
        lines = [
            f"Token: {token_data.symbol or 'Unknown'} ({token_data.name or 'Unknown'})",
            f"Chain: {token_data.chain}",
            f"Address: {token_data.address}",
            "",
            "=== Market Data ===",
            f"Price: ${(token_data.price_usd or 0):.10f}",
            f"24h Change: {(token_data.price_change_24h or 0):.2f}%",
            f"24h Volume: ${(token_data.volume_24h or 0):,.0f}",
            f"Liquidity: ${(token_data.liquidity_usd or 0):,.0f}",
            f"Market Cap: ${(token_data.market_cap or 0):,.0f}",
            "",
            "=== Safety Data ===",
            f"Status: {token_data.safety_status}",
        ]
        
        # Add safety details
        if token_data.safety_data:
            if isinstance(token_data.safety_data, dict):
                # Honeypot data
                sim = token_data.safety_data.get("simulationResult", {})
                if sim:
                    lines.append(f"Buy Tax: {sim.get('buyTax', 'N/A')}%")
                    lines.append(f"Sell Tax: {sim.get('sellTax', 'N/A')}%")
                
                # Rugcheck data
                risks = token_data.safety_data.get("risks", [])
                if risks:
                    lines.append(f"Risks: {', '.join(str(r) for r in risks[:5])}")
        
        # Add pool info
        if token_data.pools:
            lines.append("")
            lines.append("=== Top Pools ===")
            for pool in token_data.pools[:3]:
                lines.append(
                    f"- {pool['dex']}: ${(pool.get('liquidity') or 0):,.0f} liquidity"
                )
        
        # Add errors if any
        if token_data.errors:
            lines.append("")
            lines.append("=== Data Issues ===")
            for err in token_data.errors:
                lines.append(f"- {err}")
        
        return "\n".join(lines)

    @staticmethod
    def _build_dexscreener_url(chain: str, pair_address: Optional[str], token_address: str) -> str:
        """Build DexScreener URL for the token.
        
        Args:
            chain: Blockchain name (e.g., 'solana', 'ethereum')
            pair_address: Liquidity pair address (preferred)
            token_address: Token contract address (fallback for search)
            
        Returns:
            DexScreener URL (pair page if available, otherwise search page)
        """
        if pair_address:
            return f"https://dexscreener.com/{chain.lower()}/{pair_address}"
        return f"https://dexscreener.com/search?q={token_address}"

    async def _generate_tweet_verdict(self, token_data: TokenData) -> str:
        """Generate a single-sentence AI verdict for the tweet summary."""
        context = self._build_analysis_context(token_data)
        
        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=context,
                config=types.GenerateContentConfig(
                    system_instruction=TWEET_ANALYSIS_SYSTEM_PROMPT,
                ),
            )
            
            if response and response.candidates:
                candidate = response.candidates[0]
                if candidate and candidate.content and candidate.content.parts:
                    for part in candidate.content.parts:
                        if hasattr(part, "text") and part.text:
                            return part.text.strip()
            
            return "Unable to generate verdict."
            
        except Exception as e:
            self._log("error", f"Tweet verdict generation failed: {str(e)}")
            return "Verdict unavailable."

    def _format_tweet_report(self, token_data: TokenData, tweet_verdict: str) -> str:
        """Format a concise tweet-friendly Telegram message (~500 chars)."""
        safety_emoji = {
            "Safe": "✅",
            "Risky": "⚠️",
            "Honeypot": "❌",
            "Dangerous": "❌",
            "Unverified": "❓",
            "Unknown": "❓",
        }.get(token_data.safety_status, "❓")
        
        change = token_data.price_change_24h or 0
        change_emoji = "🟢" if change >= 0 else "🔴"
        
        price_fmt = format_price(token_data.price_usd)
        mcap_fmt = format_large_number(token_data.market_cap)
        volume_fmt = format_large_number(token_data.volume_24h)
        liquidity_fmt = format_large_number(token_data.liquidity_usd)
        
        lines = [
            f"🔍 <b>{token_data.symbol or 'Unknown'}</b> ({token_data.chain.capitalize()})",
            f"<code>{token_data.address}</code>",
            f"💰 {price_fmt} {change_emoji} {change:+.2f}%",
            f"📊 MCap: {mcap_fmt} | Vol: {volume_fmt}",
            f"💧 Liq: {liquidity_fmt}",
            f"🛡️ {safety_emoji} {token_data.safety_status}",
            f"🤖 {tweet_verdict}",
        ]
        
        return "\n".join(lines)

    def _format_telegram_report(self, token_data: TokenData, ai_analysis: str) -> str:
        """Format the analysis as a Telegram HTML message."""
        # Safety emoji
        safety_emoji = {
            "Safe": "✅",
            "Risky": "⚠️",
            "Honeypot": "❌",
            "Dangerous": "❌",
            "Unverified": "❓",
            "Unknown": "❓",
        }.get(token_data.safety_status, "❓")
        
        # Price change emoji
        change = token_data.price_change_24h or 0
        change_emoji = "🟢" if change >= 0 else "🔴"
        
        # Format numbers
        price_fmt = format_price(token_data.price_usd)
        volume_fmt = format_large_number(token_data.volume_24h)
        liquidity_fmt = format_large_number(token_data.liquidity_usd)
        mcap_fmt = format_large_number(token_data.market_cap)
        
        # Build DexScreener URL
        pair_address = token_data.pools[0].get("pair") if token_data.pools else None
        dexscreener_url = self._build_dexscreener_url(
            token_data.chain, pair_address, token_data.address
        )
        
        lines = [
            "🔍 <b>Token Analysis Report</b>",
            "",
            f"<b>Token:</b> {token_data.symbol or 'Unknown'}",
            f"<b>Chain:</b> {token_data.chain.capitalize()}",
            f"<b>Address:</b> <code>{token_data.address}</code>",
            f'📊 <a href="{dexscreener_url}">View on DexScreener</a>',
            "",
            "━━━ 💰 <b>Price &amp; Market</b> ━━━",
            f"<b>Price:</b> {price_fmt}",
            f"<b>24h Change:</b> {change_emoji} {change:+.2f}%",
            f"<b>Market Cap:</b> {mcap_fmt}",
            f"<b>Volume 24h:</b> {volume_fmt}",
            "",
            "━━━ 💧 <b>Liquidity</b> ━━━",
            f"<b>Total:</b> {liquidity_fmt}",
        ]
        
        # Add top pool
        if token_data.pools:
            top_pool = token_data.pools[0]
            pool_liq = format_large_number(top_pool.get("liquidity"))
            lines.append(f"<b>Top Pool:</b> {top_pool['dex']} ({pool_liq})")
        
        # Safety section
        lines.extend([
            "",
            "━━━ 🛡️ <b>Safety Check</b> ━━━",
            f"<b>Status:</b> {safety_emoji} {token_data.safety_status}",
        ])
        
        # Add safety details
        if token_data.safety_data and isinstance(token_data.safety_data, dict):
            sim = token_data.safety_data.get("simulationResult", {})
            if sim:
                buy_tax = sim.get("buyTax", "N/A")
                sell_tax = sim.get("sellTax", "N/A")
                lines.append(f"<b>Buy Tax:</b> {buy_tax}%")
                lines.append(f"<b>Sell Tax:</b> {sell_tax}%")
        
        # AI Analysis section
        lines.extend([
            "",
            "━━━ 🤖 <b>AI Analysis</b> ━━━",
            ai_analysis,
        ])
        
        # Footer
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines.extend([
            "",
            f"⏰ {timestamp}",
        ])
        
        return "\n".join(lines)

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        """Safely convert value to float."""
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None
