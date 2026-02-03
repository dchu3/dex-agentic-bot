"""Tests for token analyzer module."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.token_analyzer import (
    TokenAnalyzer,
    TokenData,
    AnalysisReport,
    detect_chain,
    is_valid_token_address,
    EVM_ADDRESS_PATTERN,
    SOLANA_ADDRESS_PATTERN,
)


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
        assert data.safety_status == "Unknown"
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
        })
        
        def get_client(name):
            clients = {
                "dexscreener": dexscreener,
                "honeypot": honeypot,
                "rugcheck": rugcheck,
            }
            return clients.get(name)
        
        manager.get_client = get_client
        return manager

    def test_format_price(self):
        """Test price formatting."""
        assert TokenAnalyzer._format_price(1.5) == "$1.5000"
        assert TokenAnalyzer._format_price(0.001) == "$0.001000"
        assert TokenAnalyzer._format_price(0.00000001) == "$0.0000000100"
        assert TokenAnalyzer._format_price(None) == "N/A"

    def test_format_large_number(self):
        """Test large number formatting."""
        assert TokenAnalyzer._format_large_number(1_500_000_000) == "$1.50B"
        assert TokenAnalyzer._format_large_number(5_000_000) == "$5.00M"
        assert TokenAnalyzer._format_large_number(50_000) == "$50.00K"
        assert TokenAnalyzer._format_large_number(500) == "$500"
        assert TokenAnalyzer._format_large_number(None) == "N/A"

    def test_safe_float(self):
        """Test safe float conversion."""
        assert TokenAnalyzer._safe_float("1.5") == 1.5
        assert TokenAnalyzer._safe_float(1.5) == 1.5
        assert TokenAnalyzer._safe_float(None) is None
        assert TokenAnalyzer._safe_float("invalid") is None

    @pytest.mark.asyncio
    async def test_analyze_evm_token(self, mock_mcp_manager):
        """Test analyzing an EVM token."""
        with patch("app.token_analyzer.genai") as mock_genai:
            # Mock Gemini response
            mock_response = MagicMock()
            mock_candidate = MagicMock()
            mock_content = MagicMock()
            mock_part = MagicMock()
            mock_part.text = "This token appears to be safe with good liquidity."
            mock_content.parts = [mock_part]
            mock_candidate.content = mock_content
            mock_response.candidates = [mock_candidate]
            
            mock_client = MagicMock()
            mock_client.models.generate_content = MagicMock(return_value=mock_response)
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
            assert "Token Analysis Report" in report.telegram_message

    @pytest.mark.asyncio
    async def test_analyze_solana_token(self, mock_mcp_manager):
        """Test analyzing a Solana token."""
        with patch("app.token_analyzer.genai") as mock_genai:
            # Mock Gemini response
            mock_response = MagicMock()
            mock_candidate = MagicMock()
            mock_content = MagicMock()
            mock_part = MagicMock()
            mock_part.text = "This Solana token has low risk indicators."
            mock_content.parts = [mock_part]
            mock_candidate.content = mock_content
            mock_response.candidates = [mock_candidate]
            
            mock_client = MagicMock()
            mock_client.models.generate_content = MagicMock(return_value=mock_response)
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

    @pytest.mark.asyncio
    async def test_analyze_auto_detect_chain(self, mock_mcp_manager):
        """Test chain auto-detection during analysis."""
        with patch("app.token_analyzer.genai") as mock_genai:
            mock_response = MagicMock()
            mock_candidate = MagicMock()
            mock_content = MagicMock()
            mock_part = MagicMock()
            mock_part.text = "Analysis complete."
            mock_content.parts = [mock_part]
            mock_candidate.content = mock_content
            mock_response.candidates = [mock_candidate]
            
            mock_client = MagicMock()
            mock_client.models.generate_content = MagicMock(return_value=mock_response)
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
