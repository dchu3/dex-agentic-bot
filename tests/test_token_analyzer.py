"""Tests for token analyzer module."""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.token_analyzer import (
    TokenAnalyzer,
    TokenData,
    AnalysisReport,
    detect_chain,
    is_valid_token_address,
    normalize_chain_identifier,
    EVM_ADDRESS_PATTERN,
    SOLANA_ADDRESS_PATTERN,
)
from app.formatting import format_price, format_large_number


class TestAddressDetection:
    """Tests for address detection functions."""

    def test_detect_evm_address(self):
        """Test EVM address detection."""
        # Valid EVM addresses
        assert detect_chain("0x6982508145454Ce325dDbE47a25d4ec3d2311933") == "ethereum"
        assert detect_chain("0xdAC17F958D2ee523a2206206994597C13D831ec7") == "ethereum"
        assert detect_chain("0x0000000000000000000000000000000000000000") == "ethereum"
        
    def test_detect_solana_address(self):
        """Test Solana address detection."""
        # Valid Solana addresses
        assert detect_chain("DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263") == "solana"
        assert detect_chain("So11111111111111111111111111111111111111112") == "solana"
        assert detect_chain("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v") == "solana"

    def test_detect_invalid_address(self):
        """Test invalid address detection."""
        assert detect_chain("not_an_address") is None
        assert detect_chain("0x123") is None  # Too short
        assert detect_chain("") is None
        assert detect_chain("hello world") is None

    def test_is_valid_token_address_evm(self):
        """Test EVM address validation."""
        assert is_valid_token_address("0x6982508145454Ce325dDbE47a25d4ec3d2311933")
        assert is_valid_token_address("  0x6982508145454Ce325dDbE47a25d4ec3d2311933  ")  # With whitespace
        assert not is_valid_token_address("0x123")  # Too short
        assert not is_valid_token_address("0xGGGG508145454Ce325dDbE47a25d4ec3d2311933")  # Invalid hex

    def test_is_valid_token_address_solana(self):
        """Test Solana address validation."""
        assert is_valid_token_address("DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263")
        assert is_valid_token_address("So11111111111111111111111111111111111111112")
        assert not is_valid_token_address("abc")  # Too short

    def test_is_valid_token_address_invalid(self):
        """Test invalid address validation."""
        assert not is_valid_token_address("not_an_address")
        assert not is_valid_token_address("")
        assert not is_valid_token_address("search for PEPE")

    def test_normalize_chain_identifier(self):
        """Test chain alias normalization."""
        assert normalize_chain_identifier(" ETH ") == "ethereum"
        assert normalize_chain_identifier("Ethereum") == "ethereum"
        assert normalize_chain_identifier(" SOL ") == "solana"
        assert normalize_chain_identifier("Binance Smart Chain") == "bsc"
        assert normalize_chain_identifier("  ") is None


class TestTokenData:
    """Tests for TokenData dataclass."""

    def test_create_token_data(self):
        """Test creating TokenData."""
        data = TokenData(
            address="0x6982508145454Ce325dDbE47a25d4ec3d2311933",
            chain="ethereum",
            symbol="PEPE",
            name="Pepe",
            price_usd=0.00001234,
        )
        assert data.address == "0x6982508145454Ce325dDbE47a25d4ec3d2311933"
        assert data.chain == "ethereum"
        assert data.symbol == "PEPE"
        assert data.safety_status == "Unverified"
        assert data.pools == []
        assert data.errors == []

    def test_token_data_defaults(self):
        """Test TokenData default values."""
        data = TokenData(address="0x123", chain="ethereum")
        assert data.symbol is None
        assert data.price_usd is None
        assert data.safety_data is None


class TestTokenAnalyzer:
    """Tests for TokenAnalyzer class."""

    @pytest.fixture
    def mock_mcp_manager(self):
        """Create a mock MCP manager."""
        manager = MagicMock()
        
        # Mock dexscreener client
        dexscreener = AsyncMock()
        dexscreener.call_tool = AsyncMock(return_value={
            "pairs": [{
                "baseToken": {"symbol": "PEPE", "name": "Pepe"},
                "priceUsd": "0.00001234",
                "priceChange": {"h24": 5.5},
                "volume": {"h24": 1000000},
                "liquidity": {"usd": 5000000},
                "marketCap": 5000000000,
                "dexId": "uniswap",
                "pairAddress": "0xabc",
                "pairCreatedAt": "2024-01-01T00:00:00Z",
            }]
        })
        
        # Mock honeypot client
        honeypot = AsyncMock()
        honeypot.call_tool = AsyncMock(return_value={
            "isHoneypot": False,
            "simulationResult": {"buyTax": 0, "sellTax": 0},
        })
        
        # Mock rugcheck client
        rugcheck = AsyncMock()
        rugcheck.call_tool = AsyncMock(return_value={
            "riskLevel": "low",
            "risks": [],
            "score_normalised": 100,
        })
        
        # Mock blockscout client (returns no holders by default)
        blockscout = AsyncMock()
        blockscout.call_tool = AsyncMock(return_value={"items": []})
        
        # Mock solana client (returns no holders by default)
        solana = AsyncMock()
        solana.call_tool = AsyncMock(return_value={"value": []})
        
        def get_client(name):
            clients = {
                "dexscreener": dexscreener,
                "honeypot": honeypot,
                "rugcheck": rugcheck,
                "blockscout": blockscout,
                "solana": solana,
            }
            return clients.get(name)
        
        manager.get_client = get_client
        return manager

    def test_format_price(self):
        """Test price formatting."""
        assert format_price(1.5) == "$1.5000"
        assert format_price(0.001) == "$0.001000"
        assert format_price(0.00000001) == "$0.0000000100"
        assert format_price(None) == "N/A"

    def test_format_large_number(self):
        """Test large number formatting."""
        assert format_large_number(1_500_000_000) == "$1.50B"
        assert format_large_number(5_000_000) == "$5.00M"
        assert format_large_number(50_000) == "$50.00K"
        assert format_large_number(500) == "$500"
        assert format_large_number(None) == "N/A"

    def test_safe_float(self):
        """Test safe float conversion."""
        assert TokenAnalyzer._safe_float("1.5") == 1.5
        assert TokenAnalyzer._safe_float(1.5) == 1.5
        assert TokenAnalyzer._safe_float(None) is None
        assert TokenAnalyzer._safe_float("invalid") is None

    def test_extract_supply_uses_amount_and_decimals_when_ui_amount_missing(self, mock_mcp_manager):
        """Supply extraction should normalize raw amount using decimals."""
        with patch("app.token_analyzer.genai"):
            analyzer = TokenAnalyzer(api_key="test-key", mcp_manager=mock_mcp_manager)

        supply = analyzer._extract_supply({
            "value": {
                "amount": "1234500000",
                "decimals": 6,
                "uiAmount": None,
                "uiAmountString": None,
            }
        })

        assert supply == 1234.5

    @pytest.mark.asyncio
    async def test_solana_holder_fallback_uses_consistent_ui_units(self, mock_mcp_manager):
        """Largest-account fallback should normalize raw amounts before pct math."""
        solana = mock_mcp_manager.get_client("solana")
        solana.call_tool = AsyncMock(side_effect=[
            {
                "value": [
                    {
                        "amount": "2500000000",
                        "decimals": 6,
                        "uiAmount": None,
                        "uiAmountString": None,
                    },
                    {
                        "amount": "1000000000",
                        "decimals": 6,
                        "uiAmount": None,
                        "uiAmountString": None,
                    },
                ]
            },
            {
                "value": {
                    "amount": "10000000000",
                    "decimals": 6,
                    "uiAmount": None,
                    "uiAmountString": None,
                }
            },
        ])

        with patch("app.token_analyzer.genai"):
            analyzer = TokenAnalyzer(api_key="test-key", mcp_manager=mock_mcp_manager)

        token_data = TokenData(address="So11111111111111111111111111111111111111112", chain="solana")
        await analyzer._fetch_holder_data_solana(token_data.address, token_data)

        assert token_data.top_10_holders_pct == 35.0
        assert token_data.holder_concentration_risk == "medium"

    @pytest.mark.asyncio
    async def test_analyze_evm_token(self, mock_mcp_manager):
        """Test analyzing an EVM token."""
        with patch("app.token_analyzer.genai") as mock_genai:
            # Mock Gemini responses: structured JSON + free-text (tweet reuses verdict)
            structured_response = MagicMock()
            structured_candidate = MagicMock()
            structured_content = MagicMock()
            structured_part = MagicMock()
            structured_part.text = json.dumps({
                "key_strengths": ["good liquidity"],
                "key_risks": ["meme volatility"],
                "whale_signal": "none detected",
                "narrative_momentum": "positive",
                "action": "buy",
                "confidence": "medium",
                "one_sentence": "Solid token.",
            })
            structured_content.parts = [structured_part]
            structured_candidate.content = structured_content
            structured_response.candidates = [structured_candidate]

            text_response = MagicMock()
            text_candidate = MagicMock()
            text_content = MagicMock()
            text_part = MagicMock()
            text_part.text = "This token appears to be safe with good liquidity."
            text_content.parts = [text_part]
            text_candidate.content = text_content
            text_response.candidates = [text_candidate]

            mock_client = MagicMock()
            mock_client.models.generate_content = MagicMock(
                side_effect=[structured_response, text_response]
            )
            mock_genai.Client.return_value = mock_client
            
            analyzer = TokenAnalyzer(
                api_key="test-key",
                mcp_manager=mock_mcp_manager,
            )
            
            report = await analyzer.analyze(
                "0x6982508145454Ce325dDbE47a25d4ec3d2311933",
                "ethereum"
            )
            
            assert isinstance(report, AnalysisReport)
            assert report.token_data.chain == "ethereum"
            assert report.token_data.symbol == "PEPE"
            assert report.token_data.safety_status == "Safe"
            assert report.structured is not None
            assert report.structured.token == "PEPE"
            assert report.structured.safety["status"] == "safe"
            assert report.structured.verdict["action"] == "buy"
            assert "Token Analysis Report" in report.telegram_message

    @pytest.mark.asyncio
    async def test_analyze_solana_token(self, mock_mcp_manager):
        """Test analyzing a Solana token."""
        with patch("app.token_analyzer.genai") as mock_genai:
            structured_part = MagicMock()
            structured_part.text = json.dumps({
                "key_strengths": ["low risk"],
                "key_risks": [],
                "whale_signal": "unknown",
                "narrative_momentum": "neutral",
                "action": "hold",
                "confidence": "low",
                "one_sentence": "Low risk Solana token.",
            })
            structured_content = MagicMock()
            structured_content.parts = [structured_part]
            structured_candidate = MagicMock()
            structured_candidate.content = structured_content
            structured_response = MagicMock()
            structured_response.candidates = [structured_candidate]

            text_part = MagicMock()
            text_part.text = "This Solana token has low risk indicators."
            text_content = MagicMock()
            text_content.parts = [text_part]
            text_candidate = MagicMock()
            text_candidate.content = text_content
            text_response = MagicMock()
            text_response.candidates = [text_candidate]

            mock_client = MagicMock()
            mock_client.models.generate_content = MagicMock(
                side_effect=[structured_response, text_response]
            )
            mock_genai.Client.return_value = mock_client
            
            analyzer = TokenAnalyzer(
                api_key="test-key",
                mcp_manager=mock_mcp_manager,
            )
            
            report = await analyzer.analyze(
                "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
                "solana"
            )
            
            assert isinstance(report, AnalysisReport)
            assert report.token_data.chain == "solana"
            assert report.token_data.safety_status == "Safe"
            assert report.structured is not None
            assert report.structured.chain == "solana"

    @pytest.mark.asyncio
    async def test_analyze_normalizes_chain_alias(self, mock_mcp_manager):
        """Explicit chain input should be normalized before routing."""
        with patch("app.token_analyzer.genai") as mock_genai:
            structured_response = MagicMock()
            structured_candidate = MagicMock()
            structured_content = MagicMock()
            structured_part = MagicMock()
            structured_part.text = json.dumps({
                "key_strengths": [],
                "key_risks": [],
                "whale_signal": "unknown",
                "narrative_momentum": "neutral",
                "action": "hold",
                "confidence": "low",
                "one_sentence": "Analysis complete.",
            })
            structured_content.parts = [structured_part]
            structured_candidate.content = structured_content
            structured_response.candidates = [structured_candidate]

            text_response = MagicMock()
            text_candidate = MagicMock()
            text_content = MagicMock()
            text_part = MagicMock()
            text_part.text = "Analysis complete."
            text_content.parts = [text_part]
            text_candidate.content = text_content
            text_response.candidates = [text_candidate]

            mock_client = MagicMock()
            mock_client.models.generate_content = MagicMock(
                side_effect=[structured_response, text_response]
            )
            mock_genai.Client.return_value = mock_client

            analyzer = TokenAnalyzer(api_key="test-key", mcp_manager=mock_mcp_manager)
            report = await analyzer.analyze(
                "0x6982508145454Ce325dDbE47a25d4ec3d2311933",
                " ETH ",
            )

            assert report.token_data.chain == "ethereum"

    @pytest.mark.asyncio
    async def test_analyze_structured_only_skips_legacy_generation(self, mock_mcp_manager):
        """Structured-only mode should skip legacy Gemini calls and formatting work."""
        with patch("app.token_analyzer.genai") as mock_genai:
            structured_response = MagicMock()
            structured_candidate = MagicMock()
            structured_content = MagicMock()
            structured_part = MagicMock()
            structured_part.text = json.dumps({
                "key_strengths": ["good liquidity"],
                "key_risks": ["meme volatility"],
                "whale_signal": "none detected",
                "narrative_momentum": "positive",
                "action": "buy",
                "confidence": "medium",
                "one_sentence": "Solid token for structured consumers.",
            })
            structured_content.parts = [structured_part]
            structured_candidate.content = structured_content
            structured_response.candidates = [structured_candidate]

            mock_client = MagicMock()
            mock_client.models.generate_content = MagicMock(
                side_effect=[structured_response]
            )
            mock_genai.Client.return_value = mock_client

            analyzer = TokenAnalyzer(api_key="test-key", mcp_manager=mock_mcp_manager)
            report = await analyzer.analyze(
                "0x6982508145454Ce325dDbE47a25d4ec3d2311933",
                "ethereum",
                structured=True,
                legacy_output=False,
            )

            assert report.structured is not None
            assert report.ai_analysis == ""
            assert report.telegram_message == report.structured.human_readable
            assert report.tweet_message == "Solid token for structured consumers."
            assert mock_client.models.generate_content.call_count == 1

    @pytest.mark.asyncio
    async def test_analyze_raises_when_both_outputs_disabled(self, mock_mcp_manager):
        """Both structured=False and legacy_output=False should raise ValueError."""
        with patch("app.token_analyzer.genai"):
            analyzer = TokenAnalyzer(api_key="test-key", mcp_manager=mock_mcp_manager)

        with pytest.raises(ValueError, match="At least one"):
            await analyzer.analyze(
                "0x6982508145454Ce325dDbE47a25d4ec3d2311933",
                "ethereum",
                structured=False,
                legacy_output=False,
            )

    def test_extract_solana_ui_amount_precision(self, mock_mcp_manager):
        """Decimal math should handle large Solana balances without float precision loss."""
        with patch("app.token_analyzer.genai"):
            analyzer = TokenAnalyzer(api_key="test-key", mcp_manager=mock_mcp_manager)

        # 2^53 + 1 cannot be represented exactly as float64
        large_amount = str(2**53 + 1)
        result = analyzer._extract_solana_ui_amount({
            "amount": large_amount,
            "decimals": 9,
        })
        expected = (2**53 + 1) / (10**9)
        assert result is not None
        assert abs(result - expected) < 1e-3

    @pytest.mark.asyncio
    async def test_analyze_auto_detect_chain(self, mock_mcp_manager):
        """Test chain auto-detection during analysis."""
        with patch("app.token_analyzer.genai") as mock_genai:
            structured_part = MagicMock()
            structured_part.text = json.dumps({
                "key_strengths": [], "key_risks": [],
                "whale_signal": "unknown", "narrative_momentum": "neutral",
                "action": "hold", "confidence": "low",
                "one_sentence": "Analysis complete.",
            })
            structured_content = MagicMock()
            structured_content.parts = [structured_part]
            structured_candidate = MagicMock()
            structured_candidate.content = structured_content
            structured_response = MagicMock()
            structured_response.candidates = [structured_candidate]

            text_part = MagicMock()
            text_part.text = "Analysis complete."
            text_content = MagicMock()
            text_content.parts = [text_part]
            text_candidate = MagicMock()
            text_candidate.content = text_content
            text_response = MagicMock()
            text_response.candidates = [text_candidate]
            
            mock_client = MagicMock()
            mock_client.models.generate_content = MagicMock(
                side_effect=[structured_response, text_response,
                             structured_response, text_response]
            )
            mock_genai.Client.return_value = mock_client
            
            analyzer = TokenAnalyzer(
                api_key="test-key",
                mcp_manager=mock_mcp_manager,
            )
            
            # EVM address - should auto-detect as ethereum
            report = await analyzer.analyze("0x6982508145454Ce325dDbE47a25d4ec3d2311933")
            assert report.token_data.chain == "ethereum"
            
            # Solana address - should auto-detect as solana
            report = await analyzer.analyze("DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263")
            assert report.token_data.chain == "solana"

    @pytest.mark.asyncio
    async def test_non_dict_pair_data_handled_gracefully(self, mock_mcp_manager):
        """Non-dict elements in pairs array should produce error, not crash."""
        # Override dexscreener to return pairs with non-dict elements
        dexscreener = mock_mcp_manager.get_client("dexscreener")
        dexscreener.call_tool = AsyncMock(return_value={
            "pairs": ["not-a-dict", None, 42]
        })

        with patch("app.token_analyzer.genai") as mock_genai:
            structured_part = MagicMock()
            structured_part.text = json.dumps({
                "key_strengths": [], "key_risks": [],
                "whale_signal": "unknown", "narrative_momentum": "neutral",
                "action": "hold", "confidence": "low",
                "one_sentence": "Analysis complete.",
            })
            structured_content = MagicMock()
            structured_content.parts = [structured_part]
            structured_candidate = MagicMock()
            structured_candidate.content = structured_content
            structured_response = MagicMock()
            structured_response.candidates = [structured_candidate]

            text_part = MagicMock()
            text_part.text = "Analysis complete."
            text_content = MagicMock()
            text_content.parts = [text_part]
            text_candidate = MagicMock()
            text_candidate.content = text_content
            text_response = MagicMock()
            text_response.candidates = [text_candidate]

            mock_client = MagicMock()
            mock_client.models.generate_content = MagicMock(
                side_effect=[structured_response, text_response]
            )
            mock_genai.Client.return_value = mock_client

            analyzer = TokenAnalyzer(
                api_key="test-key",
                mcp_manager=mock_mcp_manager,
            )

            report = await analyzer.analyze(
                "0x6982508145454Ce325dDbE47a25d4ec3d2311933",
                "ethereum",
            )
            assert any("Invalid pair data" in e for e in report.token_data.errors)


class TestTelegramReportFormatting:
    """Tests for Telegram report formatting."""

    def test_format_telegram_report_structure(self):
        """Test that Telegram report has expected structure."""
        token_data = TokenData(
            address="0x6982508145454Ce325dDbE47a25d4ec3d2311933",
            chain="ethereum",
            symbol="PEPE",
            name="Pepe",
            price_usd=0.00001234,
            price_change_24h=5.5,
            volume_24h=1000000,
            liquidity_usd=5000000,
            market_cap=5000000000,
            safety_status="Safe",
            pools=[{"dex": "uniswap", "liquidity": 3000000}],
        )
        
        with patch("app.token_analyzer.genai"):
            analyzer = TokenAnalyzer(
                api_key="test-key",
                mcp_manager=MagicMock(),
            )
            
            report = analyzer._format_telegram_report(token_data, "AI analysis here")
            
            # Check expected sections
            assert "Token Analysis Report" in report
            assert "PEPE" in report
            assert "Ethereum" in report
            assert "Price &amp; Market" in report
            assert "Liquidity" in report
            assert "Safety Check" in report
            assert "AI Analysis" in report
            assert "✅ Safe" in report
            assert "AI analysis here" in report

    def test_format_tweet_report_structure(self):
        """Test that tweet report has expected concise structure."""
        token_data = TokenData(
            address="0x6982508145454Ce325dDbE47a25d4ec3d2311933",
            chain="ethereum",
            symbol="PEPE",
            name="Pepe",
            price_usd=0.00001234,
            price_change_24h=5.5,
            volume_24h=1000000,
            liquidity_usd=5000000,
            market_cap=5000000000,
            safety_status="Safe",
            pools=[{"dex": "uniswap", "pair": "0xabc", "liquidity": 3000000}],
        )
        
        with patch("app.token_analyzer.genai"):
            analyzer = TokenAnalyzer(
                api_key="test-key",
                mcp_manager=MagicMock(),
            )
            
            report = analyzer._format_tweet_report(token_data, "Looks solid.")
            
            assert "PEPE" in report
            assert "Ethereum" in report
            assert "✅ Safe" in report
            assert "Looks solid." in report
            assert "+5.50%" in report
            # Should NOT contain detailed sections
            assert "Token Analysis Report" not in report
            assert "Liquidity" not in report
            assert "AI Analysis" not in report

    def test_format_tweet_report_length(self):
        """Test that tweet report stays concise."""
        token_data = TokenData(
            address="0x6982508145454Ce325dDbE47a25d4ec3d2311933",
            chain="ethereum",
            symbol="PEPE",
            name="Pepe",
            price_usd=0.00001234,
            price_change_24h=-12.3,
            market_cap=5000000000,
            safety_status="Risky",
            pools=[{"dex": "uniswap", "pair": "0xabc", "liquidity": 3000000}],
        )
        
        with patch("app.token_analyzer.genai"):
            analyzer = TokenAnalyzer(
                api_key="test-key",
                mcp_manager=MagicMock(),
            )
            
            report = analyzer._format_tweet_report(token_data, "High sell tax detected.")
            
            # Should be under 500 chars (excluding HTML tags)
            import re
            plain_text = re.sub(r"<[^>]+>", "", report)
            assert len(plain_text) < 500
            assert "🔴" in report
            assert "⚠️" in report


class TestRugcheckResultHandling:
    """Tests for rugcheck result type handling in _fetch_rugcheck_data."""

    @pytest.fixture
    def analyzer_with_rugcheck(self):
        """Create analyzer with mock rugcheck client."""
        manager = MagicMock()
        rugcheck = AsyncMock()
        manager.get_client = lambda name: rugcheck if name == "rugcheck" else None
        
        with patch("app.token_analyzer.genai"):
            analyzer = TokenAnalyzer(
                api_key="test-key",
                mcp_manager=manager,
            )
        return analyzer, rugcheck

    @pytest.mark.asyncio
    async def test_dict_result_safe(self, analyzer_with_rugcheck):
        """Test dict result with low score is parsed as Safe."""
        analyzer, rugcheck = analyzer_with_rugcheck
        rugcheck.call_tool = AsyncMock(return_value={
            "score_normalised": 100,
            "risks": [],
        })
        token_data = TokenData(address="So1ana", chain="solana")
        await analyzer._fetch_rugcheck_data("So1ana", token_data)
        assert token_data.safety_status == "Safe"

    @pytest.mark.asyncio
    async def test_dict_result_risky(self, analyzer_with_rugcheck):
        """Test dict result with moderate score is parsed as Risky."""
        analyzer, rugcheck = analyzer_with_rugcheck
        rugcheck.call_tool = AsyncMock(return_value={
            "score_normalised": 1500,
            "risks": [{"name": "low_lp"}],
        })
        token_data = TokenData(address="So1ana", chain="solana")
        await analyzer._fetch_rugcheck_data("So1ana", token_data)
        assert token_data.safety_status == "Risky"

    @pytest.mark.asyncio
    async def test_dict_result_dangerous(self, analyzer_with_rugcheck):
        """Test dict result with high score is parsed as Dangerous."""
        analyzer, rugcheck = analyzer_with_rugcheck
        rugcheck.call_tool = AsyncMock(return_value={
            "score_normalised": 5000,
            "risks": [{"name": "a"}, {"name": "b"}, {"name": "c"}],
        })
        token_data = TokenData(address="So1ana", chain="solana")
        await analyzer._fetch_rugcheck_data("So1ana", token_data)
        assert token_data.safety_status == "Dangerous"

    @pytest.mark.asyncio
    async def test_list_result_unwrapped(self, analyzer_with_rugcheck):
        """Test list result is unwrapped and parsed correctly."""
        analyzer, rugcheck = analyzer_with_rugcheck
        rugcheck.call_tool = AsyncMock(return_value=[{
            "score_normalised": 200,
            "risks": [],
        }])
        token_data = TokenData(address="So1ana", chain="solana")
        await analyzer._fetch_rugcheck_data("So1ana", token_data)
        assert token_data.safety_status == "Safe"
        assert isinstance(token_data.safety_data, dict)

    @pytest.mark.asyncio
    async def test_empty_list_result(self, analyzer_with_rugcheck):
        """Test empty list result sets Unverified."""
        analyzer, rugcheck = analyzer_with_rugcheck
        rugcheck.call_tool = AsyncMock(return_value=[])
        token_data = TokenData(address="So1ana", chain="solana")
        await analyzer._fetch_rugcheck_data("So1ana", token_data)
        assert token_data.safety_status == "Unverified"
        assert any("unexpected" in e.lower() for e in token_data.errors)

    @pytest.mark.asyncio
    async def test_mcp_error_string(self, analyzer_with_rugcheck):
        """Test MCP error string sets Unverified."""
        analyzer, rugcheck = analyzer_with_rugcheck
        rugcheck.call_tool = AsyncMock(return_value="MCP error: tool not found")
        token_data = TokenData(address="So1ana", chain="solana")
        await analyzer._fetch_rugcheck_data("So1ana", token_data)
        assert token_data.safety_status == "Unverified"
        assert any("MCP error" in e for e in token_data.errors)

    @pytest.mark.asyncio
    async def test_json_string_result(self, analyzer_with_rugcheck):
        """Test JSON string result is parsed."""
        analyzer, rugcheck = analyzer_with_rugcheck
        import json
        rugcheck.call_tool = AsyncMock(return_value=json.dumps({
            "score_normalised": 300,
            "risks": [],
        }))
        token_data = TokenData(address="So1ana", chain="solana")
        await analyzer._fetch_rugcheck_data("So1ana", token_data)
        assert token_data.safety_status == "Safe"

    @pytest.mark.asyncio
    async def test_non_json_string_result(self, analyzer_with_rugcheck):
        """Test non-JSON, non-MCP-error string sets Unverified."""
        analyzer, rugcheck = analyzer_with_rugcheck
        rugcheck.call_tool = AsyncMock(return_value="some unexpected text")
        token_data = TokenData(address="So1ana", chain="solana")
        await analyzer._fetch_rugcheck_data("So1ana", token_data)
        assert token_data.safety_status == "Unverified"
        assert any("unexpected" in e.lower() for e in token_data.errors)

    @pytest.mark.asyncio
    async def test_no_rugcheck_client(self):
        """Test missing rugcheck client sets Unverified."""
        manager = MagicMock()
        manager.get_client = lambda name: None
        
        with patch("app.token_analyzer.genai"):
            analyzer = TokenAnalyzer(api_key="test-key", mcp_manager=manager)
        
        token_data = TokenData(address="So1ana", chain="solana")
        await analyzer._fetch_rugcheck_data("So1ana", token_data)
        assert token_data.safety_status == "Unverified"
        assert any("not available" in e.lower() for e in token_data.errors)


class TestEVMHolderConcentration:
    """Tests for _fetch_holder_data_evm via Blockscout."""

    @pytest.fixture
    def analyzer_with_blockscout(self):
        """Analyzer with blockscout mock, no other clients."""
        manager = MagicMock()
        blockscout = AsyncMock()
        manager.get_client = lambda name: blockscout if name == "blockscout" else None

        with patch("app.token_analyzer.genai"):
            analyzer = TokenAnalyzer(api_key="test-key", mcp_manager=manager)

        return analyzer, blockscout

    @pytest.mark.asyncio
    async def test_low_concentration(self, analyzer_with_blockscout):
        """<30% total -> concentration_risk='low'."""
        analyzer, blockscout = analyzer_with_blockscout
        blockscout.call_tool = AsyncMock(return_value={
            "items": [{"percentage": 5.0}, {"percentage": 4.5}, {"percentage": 4.0}]
        })
        token_data = TokenData(address="0xABC", chain="ethereum")
        await analyzer._fetch_holder_data_evm("0xABC", "ethereum", token_data)

        assert token_data.top_10_holders_pct == 13.5
        assert token_data.holder_concentration_risk == "low"

    @pytest.mark.asyncio
    async def test_medium_concentration(self, analyzer_with_blockscout):
        """>=30% and <60% total -> concentration_risk='medium'."""
        analyzer, blockscout = analyzer_with_blockscout
        blockscout.call_tool = AsyncMock(return_value={
            "items": [{"percentage": 20.0}, {"percentage": 15.0}, {"percentage": 5.0}]
        })
        token_data = TokenData(address="0xABC", chain="ethereum")
        await analyzer._fetch_holder_data_evm("0xABC", "ethereum", token_data)

        assert token_data.top_10_holders_pct == 40.0
        assert token_data.holder_concentration_risk == "medium"

    @pytest.mark.asyncio
    async def test_high_concentration(self, analyzer_with_blockscout):
        """>=60% total -> concentration_risk='high'."""
        analyzer, blockscout = analyzer_with_blockscout
        blockscout.call_tool = AsyncMock(return_value={
            "items": [{"percentage": 40.0}, {"percentage": 25.0}]
        })
        token_data = TokenData(address="0xABC", chain="ethereum")
        await analyzer._fetch_holder_data_evm("0xABC", "ethereum", token_data)

        assert token_data.top_10_holders_pct == 65.0
        assert token_data.holder_concentration_risk == "high"

    @pytest.mark.asyncio
    async def test_empty_items_no_concentration(self, analyzer_with_blockscout):
        """Empty items leaves holder data unset."""
        analyzer, blockscout = analyzer_with_blockscout
        blockscout.call_tool = AsyncMock(return_value={"items": []})
        token_data = TokenData(address="0xABC", chain="ethereum")
        await analyzer._fetch_holder_data_evm("0xABC", "ethereum", token_data)

        assert token_data.top_10_holders_pct is None
        assert token_data.holder_concentration_risk is None

    @pytest.mark.asyncio
    async def test_no_blockscout_client(self):
        """Missing blockscout client leaves holder data unset."""
        manager = MagicMock()
        manager.get_client = lambda name: None

        with patch("app.token_analyzer.genai"):
            analyzer = TokenAnalyzer(api_key="test-key", mcp_manager=manager)

        token_data = TokenData(address="0xABC", chain="ethereum")
        await analyzer._fetch_holder_data_evm("0xABC", "ethereum", token_data)

        assert token_data.top_10_holders_pct is None
