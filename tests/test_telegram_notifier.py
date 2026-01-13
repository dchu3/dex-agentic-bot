"""Tests for Telegram notifier."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.telegram_notifier import TelegramNotifier
from app.watchlist_poller import TriggeredAlert


@pytest.fixture
def notifier():
    """Create a test notifier with mock credentials."""
    return TelegramNotifier(
        bot_token="123456789:ABCdefGHIjklMNOpqrsTUVwxyz",
        chat_id="987654321",
    )


@pytest.fixture
def unconfigured_notifier():
    """Create a notifier without credentials."""
    return TelegramNotifier(bot_token="", chat_id="")


@pytest.fixture
def sample_alert():
    """Create a sample triggered alert."""
    return TriggeredAlert(
        symbol="PEPE",
        chain="ethereum",
        alert_type="above",
        threshold=0.00002,
        current_price=0.000021,
        token_address="0x6982508145454ce325ddbe47a25d4ec3d2311933",
    )


def test_is_configured_true(notifier):
    """Test is_configured returns True when credentials are set."""
    assert notifier.is_configured is True


def test_is_configured_false(unconfigured_notifier):
    """Test is_configured returns False when credentials are empty."""
    assert unconfigured_notifier.is_configured is False


def test_is_configured_partial():
    """Test is_configured returns False when only one credential is set."""
    notifier = TelegramNotifier(bot_token="token", chat_id="")
    assert notifier.is_configured is False

    notifier = TelegramNotifier(bot_token="", chat_id="123")
    assert notifier.is_configured is False


@pytest.mark.asyncio
async def test_send_message_unconfigured(unconfigured_notifier):
    """Test send_message returns False when not configured."""
    result = await unconfigured_notifier.send_message("test")
    assert result is False


@pytest.mark.asyncio
async def test_send_message_success(notifier):
    """Test send_message returns True on successful API call."""
    mock_response = MagicMock()
    mock_response.json.return_value = {"ok": True}

    with patch.object(notifier, "_get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_get_client.return_value = mock_client

        result = await notifier.send_message("Hello, World!")

        assert result is True
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert "sendMessage" in call_args[0][0]
        assert call_args[1]["json"]["text"] == "Hello, World!"
        assert call_args[1]["json"]["chat_id"] == "987654321"


@pytest.mark.asyncio
async def test_send_message_api_failure(notifier):
    """Test send_message returns False when API returns error."""
    mock_response = MagicMock()
    mock_response.json.return_value = {"ok": False, "error": "Bad Request"}

    with patch.object(notifier, "_get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_get_client.return_value = mock_client

        result = await notifier.send_message("Hello, World!")
        assert result is False


@pytest.mark.asyncio
async def test_send_message_network_error(notifier):
    """Test send_message returns False on network error."""
    with patch.object(notifier, "_get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.post.side_effect = Exception("Network error")
        mock_get_client.return_value = mock_client

        result = await notifier.send_message("Hello, World!")
        assert result is False


@pytest.mark.asyncio
async def test_test_connection_success(notifier):
    """Test test_connection returns True when bot token is valid."""
    mock_response = MagicMock()
    mock_response.json.return_value = {"ok": True, "result": {"username": "test_bot"}}

    with patch.object(notifier, "_get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_get_client.return_value = mock_client

        result = await notifier.test_connection()

        assert result is True
        mock_client.get.assert_called_once()
        assert "getMe" in mock_client.get.call_args[0][0]


@pytest.mark.asyncio
async def test_test_connection_invalid_token(notifier):
    """Test test_connection returns False when bot token is invalid."""
    mock_response = MagicMock()
    mock_response.json.return_value = {"ok": False, "error": "Unauthorized"}

    with patch.object(notifier, "_get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_get_client.return_value = mock_client

        result = await notifier.test_connection()
        assert result is False


@pytest.mark.asyncio
async def test_test_connection_unconfigured(unconfigured_notifier):
    """Test test_connection returns False when not configured."""
    result = await unconfigured_notifier.test_connection()
    assert result is False


@pytest.mark.asyncio
async def test_send_alert(notifier, sample_alert):
    """Test send_alert formats and sends the alert."""
    mock_response = MagicMock()
    mock_response.json.return_value = {"ok": True}

    with patch.object(notifier, "_get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_get_client.return_value = mock_client

        result = await notifier.send_alert(sample_alert)

        assert result is True
        call_args = mock_client.post.call_args
        message_text = call_args[1]["json"]["text"]
        
        assert "PEPE" in message_text
        assert "ethereum" in message_text
        assert "Crossed above" in message_text
        assert "$0.00002" in message_text


def test_format_alert_above(notifier, sample_alert):
    """Test alert formatting for above threshold."""
    message = notifier._format_alert(sample_alert)

    assert "🔔" in message
    assert "Price Alert" in message
    assert "PEPE" in message
    assert "ethereum" in message
    assert "🔺" in message
    assert "Crossed above" in message
    assert "0x6982508145454ce325ddbe47a25d4ec3d2311933" in message
    assert "<code>" in message  # Copyable format


def test_format_alert_below(notifier):
    """Test alert formatting for below threshold."""
    alert = TriggeredAlert(
        symbol="WIF",
        chain="solana",
        alert_type="below",
        threshold=1.50,
        current_price=1.45,
        token_address="EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
    )
    message = notifier._format_alert(alert)

    assert "WIF" in message
    assert "solana" in message
    assert "🔻" in message
    assert "Dropped below" in message
    assert "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm" in message


def test_format_price():
    """Test price formatting with different magnitudes."""
    assert TelegramNotifier._format_price(1234.5678) == "$1,234.5678"
    assert TelegramNotifier._format_price(1.5) == "$1.5000"
    assert TelegramNotifier._format_price(0.001234) == "$0.001234"
    assert TelegramNotifier._format_price(0.00001234) == "$0.0000123400"


@pytest.mark.asyncio
async def test_close(notifier):
    """Test close method closes the client and stops polling."""
    # Create a mock client
    mock_client = AsyncMock()
    mock_client.is_closed = False
    notifier._client = mock_client

    await notifier.close()

    mock_client.aclose.assert_called_once()
    assert notifier._client is None


@pytest.mark.asyncio
async def test_start_stop_polling(notifier):
    """Test starting and stopping polling."""
    assert notifier.is_polling is False

    await notifier.start_polling()
    assert notifier.is_polling is True

    await notifier.stop_polling()
    assert notifier.is_polling is False


@pytest.mark.asyncio
async def test_start_polling_unconfigured(unconfigured_notifier):
    """Test that polling doesn't start when not configured."""
    await unconfigured_notifier.start_polling()
    assert unconfigured_notifier.is_polling is False


@pytest.mark.asyncio
async def test_handle_help_command(notifier):
    """Test handling /help command sends help message."""
    mock_response = MagicMock()
    mock_response.json.return_value = {"ok": True}

    with patch.object(notifier, "_get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_get_client.return_value = mock_client

        await notifier._handle_command("/help")

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        message_text = call_args[1]["json"]["text"]

        assert "DEX Agentic Bot" in message_text
        assert "/watch" in message_text
        assert "/alert" in message_text


@pytest.mark.asyncio
async def test_handle_start_command(notifier):
    """Test handling /start command sends help message."""
    mock_response = MagicMock()
    mock_response.json.return_value = {"ok": True}

    with patch.object(notifier, "_get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_get_client.return_value = mock_client

        await notifier._handle_command("/start")

        mock_client.post.assert_called_once()


@pytest.mark.asyncio
async def test_handle_status_command(notifier):
    """Test handling /status command sends status message."""
    mock_response = MagicMock()
    mock_response.json.return_value = {"ok": True}

    with patch.object(notifier, "_get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_get_client.return_value = mock_client

        await notifier._handle_command("/status")

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        message_text = call_args[1]["json"]["text"]

        assert "Bot Status" in message_text
        assert "Online" in message_text


@pytest.mark.asyncio
async def test_handle_update_from_correct_chat(notifier):
    """Test that updates from the configured chat are processed."""
    update = {
        "update_id": 12345,
        "message": {
            "chat": {"id": 987654321},
            "text": "/help",
        }
    }

    with patch.object(notifier, "_handle_command", new_callable=AsyncMock) as mock_handle:
        await notifier._handle_update(update)
        mock_handle.assert_called_once_with("/help")


@pytest.mark.asyncio
async def test_handle_update_from_wrong_chat(notifier):
    """Test that updates from other chats are ignored."""
    update = {
        "update_id": 12345,
        "message": {
            "chat": {"id": 111111111},  # Wrong chat ID
            "text": "/help",
        }
    }

    with patch.object(notifier, "_handle_command", new_callable=AsyncMock) as mock_handle:
        await notifier._handle_update(update)
        mock_handle.assert_not_called()
