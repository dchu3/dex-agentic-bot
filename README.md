# Token Safety & Analysis Bot

A Telegram bot that provides comprehensive token safety checks and market analysis. Send any token address and get an AI-powered report with price data, liquidity info, safety checks, and investment insights.

## Features

- 🔍 **Instant Analysis** - Send a token address, get a detailed report
- 🛡️ **Safety Checks** - Honeypot detection (EVM) and Rugcheck (Solana)
- 📊 **Market Data** - Price, volume, liquidity, market cap via DexScreener
- 🤖 **AI Insights** - Gemini-powered analysis and risk assessment
- ⚡ **Multi-Chain** - Supports Ethereum, BSC, Base, and Solana

## Quick Start

### 1. Setup

```bash
# Install dependencies
./scripts/install.sh

# Configure environment
cp .env.example .env
# Edit .env with your API keys
```

### 2. Configuration

Add to your `.env`:

```env
# Required
GEMINI_API_KEY=your-gemini-api-key
TELEGRAM_BOT_TOKEN=your-telegram-bot-token

# Optional: Restrict bot to specific chat (private mode)
TELEGRAM_CHAT_ID=your-chat-id        # Your Telegram chat ID
TELEGRAM_PRIVATE_MODE=true           # Set to true to restrict access

# MCP Servers (token data sources)
MCP_DEXSCREENER_CMD=npx @mcp-dexscreener/server
MCP_DEXPAPRIKA_CMD=dexpaprika-mcp
MCP_HONEYPOT_CMD=node /path/to/dex-honeypot-mcp/dist/index.js
MCP_RUGCHECK_CMD=node /path/to/dex-rugcheck-mcp/dist/index.js
MCP_SOLANA_RPC_CMD=node /path/to/solana-rpc-mcp/dist/index.js
```

#### Private Mode

By default, the bot is **public** - anyone can send token addresses and receive reports.

To restrict the bot to only your personal use:
1. Set `TELEGRAM_PRIVATE_MODE=true` in `.env`
2. Set `TELEGRAM_CHAT_ID` to your Telegram chat ID
3. Only messages from that chat ID will be processed

To find your chat ID, send a message to your bot and check the logs, or use [@userinfobot](https://t.me/userinfobot).

### 3. Run the Bot

```bash
# Run Telegram bot only (recommended for production)
./scripts/start.sh --telegram-only

# Or run with CLI interface
./scripts/start.sh --interactive
```

## Usage

### Telegram Bot

1. Start a chat with your bot on Telegram
2. Send any token address:
   - EVM: `0x6982508145454Ce325dDbE47a25d4ec3d2311933`
   - Solana: `DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263`
3. Receive a comprehensive analysis report

### Supported Address Formats

| Chain | Format | Example |
|-------|--------|---------|
| Ethereum/BSC/Base | `0x` + 40 hex chars | `0x6982508145454Ce325dDbE47a25d4ec3d2311933` |
| Solana | Base58, 32-44 chars | `DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263` |

Chain is auto-detected from the address format.

### Telegram Commands

| Command | Description |
|---------|-------------|
| `/analyze <address>` | Analyze a token (same as sending address directly) |
| `/help` | Show help message |
| `/status` | Check bot status |

### Example Report

```
🔍 Token Analysis Report

Token: PEPE
Chain: Ethereum
Address: 0x6982508145454Ce325dDbE47a25d4ec3d2311933

━━━ 💰 Price & Market ━━━
Price: $0.00001234
24h Change: 🟢 +5.2%
Market Cap: $5.2B
Volume 24h: $234M

━━━ 💧 Liquidity ━━━
Total: $45.2M
Top Pool: Uniswap V3 ($23M)

━━━ 🛡️ Safety Check ━━━
Status: ✅ Safe
Buy Tax: 0%
Sell Tax: 0%

━━━ 🤖 AI Analysis ━━━
This token shows healthy trading characteristics with
deep liquidity and no concerning tax mechanisms...

⏰ 2026-02-03 16:30 UTC
```

## CLI Options

| Option | Description |
|--------|-------------|
| `--telegram-only` | Run only the Telegram bot (no CLI) |
| `-i, --interactive` | Start interactive CLI mode |
| `-v, --verbose` | Show debug information |
| `--no-telegram` | Disable Telegram in interactive mode |

## Architecture

```
Telegram Message (token address)
         │
         ▼
┌─────────────────────────┐
│   TelegramNotifier      │
│   - Address detection   │
│   - Message routing     │
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│    TokenAnalyzer        │
│   - Chain detection     │
│   - Parallel MCP calls  │
│   - AI synthesis        │
└────────────┬────────────┘
             │
    ┌────────┼────────┬────────┐
    ▼        ▼        ▼        ▼
DexScreener DexPaprika Honeypot Rugcheck
   (price)   (pools)  (EVM)   (Solana)
             │
             ▼
       Gemini AI
   (Report synthesis)
             │
             ▼
   Telegram Report Message
```

## Prerequisites

- **Python 3.10+**
- **Node.js 18+** (for MCP servers)
- **Telegram Bot Token** (from [@BotFather](https://t.me/BotFather))
- **Gemini API Key** (from [Google AI Studio](https://makersuite.google.com/app/apikey))

## Development

```bash
# Activate virtual environment
source .venv/bin/activate

# Run tests
pytest

# Run CLI directly
python -m app --telegram-only
```

## MCP Servers

This bot uses MCP (Model Context Protocol) servers for data:

| Server | Purpose | Chains |
|--------|---------|--------|
| [mcp-dexscreener](https://github.com/janswist/mcp-dexscreener) | Token prices, pools, volume | All |
| [dexpaprika-mcp](https://github.com/coinpaprika/dexpaprika-mcp) | Pool details, OHLCV data | All |
| [dex-honeypot-mcp](https://github.com/dchu3/dex-honeypot-mcp) | Honeypot detection | Ethereum, BSC, Base |
| [dex-rugcheck-mcp](https://github.com/dchu3/dex-rugcheck-mcp) | Token safety | Solana |
| [solana-rpc-mcp](https://github.com/dchu3/solana-rpc-mcp) | Direct Solana RPC queries | Solana |

## License

MIT
