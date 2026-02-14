# Token Safety & Analysis Bot

A Telegram bot that provides comprehensive token safety checks and market analysis. Send any token address and get an AI-powered report with price data, liquidity info, safety checks, and investment insights.

> [!WARNING]
> **API Cost & Security Notice**
> This bot uses the Gemini API (or other LLM APIs) which **incur usage costs** with every request. Each token analysis triggers multiple API calls that count toward your billing. Set spending limits in your API provider's dashboard to avoid unexpected charges.
>
> **Never commit API keys to source control.** If deploying publicly, be aware that anyone with access can trigger API calls at your expense. The bot defaults to **private mode** — configure your `TELEGRAM_CHAT_ID` before use. If you choose to disable private mode, do so with caution and monitor your API usage closely.

## Features

- 🔍 **Instant Analysis** - Send a token address, get a detailed report
- 🛡️ **Safety Checks** - Honeypot detection (EVM) and Rugcheck (Solana)
- 📊 **Market Data** - Price, volume, liquidity, market cap via DexScreener
- ⚡ **Lag-Edge Strategy (Solana)** - Optional auto-execution loop via trader MCP
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
TELEGRAM_PRIVATE_MODE=true           # Private by default; set to false to allow public access

# MCP Servers (token data sources)
MCP_DEXSCREENER_CMD=node /path/to/dex-screener-mcp/dist/index.js
MCP_DEXPAPRIKA_CMD=dexpaprika-mcp
MCP_HONEYPOT_CMD=node /path/to/dex-honeypot-mcp/dist/index.js
MCP_RUGCHECK_CMD=node /path/to/dex-rugcheck-mcp/dist/index.js
MCP_SOLANA_RPC_CMD=node /path/to/solana-rpc-mcp/dist/index.js
MCP_BLOCKSCOUT_CMD=node /path/to/dex-blockscout-mcp/dist/index.js
MCP_TRADER_CMD=node /path/to/dex-trader-mcp/dist/index.js

# Optional: Lag strategy (Solana-first)
LAG_STRATEGY_ENABLED=false
LAG_STRATEGY_DRY_RUN=true
LAG_STRATEGY_INTERVAL_SECONDS=20
LAG_STRATEGY_MIN_EDGE_BPS=30
LAG_STRATEGY_MAX_POSITION_USD=25
LAG_STRATEGY_MAX_OPEN_POSITIONS=2
LAG_STRATEGY_DAILY_LOSS_LIMIT_USD=50
```

#### Private Mode

By default, the bot runs in **private mode** — only messages from your configured `TELEGRAM_CHAT_ID` are processed.

To set up private mode:
1. Set `TELEGRAM_CHAT_ID` to your Telegram chat ID in `.env`
2. Only messages from that chat ID will be processed

To make the bot public (use with caution — see warning above):
1. Set `TELEGRAM_PRIVATE_MODE=false` in `.env`
2. Anyone will be able to send token addresses and trigger API calls at your expense

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

### Lag Strategy (Interactive CLI, Solana)

The lag strategy runs in interactive mode and monitors **Solana tokens in your watchlist** (or autonomous watchlist), then opens/closes positions via trader MCP.

1. Configure trader MCP and wallet (in the trader MCP project's `.env`, not this repo's `.env`).
2. Add Solana tokens to watchlist (example: `/watch BONK solana`).
3. Start interactive mode with lag scheduler enabled:

```bash
./scripts/start.sh --interactive --lag-strategy --lag-interval 20
```

By default this runs in **dry-run mode** (`LAG_STRATEGY_DRY_RUN=true`), so no real orders are sent.

> **Note:** The lag strategy uses SOL as the payment currency for all trades via Jupiter. Native SOL and USDC are automatically excluded from the token monitoring list since they cannot be traded against themselves. Do not add SOL or USDC to your watchlist for lag trading.

Useful `/lag` commands:
- `/lag status` - Scheduler/risk status and cycle summary
- `/lag run` - Run one cycle immediately
- `/lag start` / `/lag stop` - Start or stop scheduler
- `/lag positions` - Show open lag positions
- `/lag close <id|all>` - Manually close position(s)
- `/lag events` - Show recent lag strategy events

To enable live execution, either set `LAG_STRATEGY_DRY_RUN=false` in `.env` or start with:

```bash
./scripts/start.sh --interactive --lag-strategy --lag-live
```

Recommended risk controls to tune first: `LAG_STRATEGY_MIN_EDGE_BPS`, `LAG_STRATEGY_MAX_POSITION_USD`, `LAG_STRATEGY_MAX_OPEN_POSITIONS`, `LAG_STRATEGY_DAILY_LOSS_LIMIT_USD`.

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
| `--lag-strategy` | Enable lag-edge strategy scheduler |
| `--lag-interval` | Lag strategy cycle interval in seconds |
| `--lag-live` | Run lag strategy with live execution (disable dry-run) |

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
    ┌────────┼────────┬────────┬────────┐
    ▼        ▼        ▼        ▼        ▼
DexScreener DexPaprika Honeypot Rugcheck Blockscout
   (price)   (pools)  (EVM)   (Solana)  (Base)
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
| [dex-screener-mcp](https://github.com/dchu3/dex-screener-mcp) | Token prices, pools, volume | All |
| [dexpaprika-mcp](https://github.com/coinpaprika/dexpaprika-mcp) | Pool details, OHLCV data | All |
| [dex-honeypot-mcp](https://github.com/dchu3/dex-honeypot-mcp) | Honeypot detection | Ethereum, BSC, Base |
| [dex-rugcheck-mcp](https://github.com/dchu3/dex-rugcheck-mcp) | Token safety | Solana |
| [solana-rpc-mcp](https://github.com/dchu3/solana-rpc-mcp) | Direct Solana RPC queries | Solana |
| [dex-blockscout-mcp](https://github.com/dchu3/dex-blockscout-mcp) | Block explorer data | Base, Ethereum |
| [dex-trader-mcp](https://github.com/dchu3/dex-trader-mcp) | Token trading via Jupiter | Solana |

Each MCP server subprocess automatically runs with its project root as the working directory (detected via `package.json` or `pyproject.toml`). This means servers can load their own `.env` files independently — for example, `dex-trader-mcp` reads `SOLANA_PRIVATE_KEY` from its own `.env`, not from this bot's `.env`.

## License

MIT
