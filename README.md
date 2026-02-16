# Token Safety & Analysis Bot

> [!CAUTION]
> **⚠️ HIGH RISK – USE AT YOUR OWN RISK**
>
> This is experimental code. Trading bots can lose ALL your money very quickly.
> The author provides NO WARRANTY and is NOT LIABLE for ANY financial losses, bugs, or bad decisions.
> Do NOT use real money without extensive backtesting and forward testing on paper/demo accounts.
> Past performance (if shown) does NOT indicate future results.

An AI-powered CLI and Telegram bot for token safety checks, market analysis, and autonomous portfolio management. Uses Gemini AI with MCP servers for multi-chain data retrieval.

> [!WARNING]
> **API Cost & Security Notice**
> This bot uses the Gemini API which **incurs usage costs** with every request. Set spending limits in your API provider's dashboard to avoid unexpected charges.
>
> **Never commit API keys to source control.** The Telegram bot defaults to **private mode** — configure your `TELEGRAM_CHAT_ID` before use.

## Features

- 🔍 **AI Token Analysis** — Send a token address (CLI or Telegram), get a detailed safety & market report
- 🛡️ **Safety Checks** — Honeypot detection (EVM) and Rugcheck (Solana)
- 📊 **Market Data** — Price, volume, liquidity, market cap via DexScreener & DexPaprika
- 📈 **Portfolio Strategy** — Autonomous token discovery → buy → hold → exit at TP/SL with trailing stops (Solana)
- 🤖 **Gemini AI** — Natural language queries, intelligent tool selection, risk assessment
- ⚡ **Multi-Chain** — Supports Ethereum, BSC, Base, and Solana

## Quick Start

### 1. Setup

```bash
./scripts/install.sh
cp .env.example .env
# Edit .env with your API keys
```

### 2. Configuration

Key settings in `.env`:

```env
# Required
GEMINI_API_KEY=your-gemini-api-key

# MCP Servers (token data sources)
MCP_DEXSCREENER_CMD=node /path/to/dex-screener-mcp/dist/index.js
MCP_DEXPAPRIKA_CMD=dexpaprika-mcp
MCP_HONEYPOT_CMD=node /path/to/dex-honeypot-mcp/dist/index.js
MCP_RUGCHECK_CMD=node /path/to/dex-rugcheck-mcp/dist/index.js
MCP_SOLANA_RPC_CMD=node /path/to/solana-rpc-mcp/dist/index.js
MCP_BLOCKSCOUT_CMD=node /path/to/dex-blockscout-mcp/dist/index.js
MCP_TRADER_CMD=node /path/to/dex-trader-mcp/dist/index.js

# Telegram (optional)
TELEGRAM_BOT_TOKEN=your-telegram-bot-token
TELEGRAM_CHAT_ID=your-chat-id
TELEGRAM_PRIVATE_MODE=true

# Portfolio strategy (optional)
PORTFOLIO_ENABLED=false
PORTFOLIO_DRY_RUN=true
PORTFOLIO_POSITION_SIZE_USD=5.0
PORTFOLIO_MAX_POSITIONS=5
PORTFOLIO_TAKE_PROFIT_PCT=15.0
PORTFOLIO_STOP_LOSS_PCT=8.0
PORTFOLIO_TRAILING_STOP_PCT=5.0
```

See `.env.example` for the full list of settings.

#### Telegram Private Mode

By default, only messages from your configured `TELEGRAM_CHAT_ID` are processed. Set `TELEGRAM_PRIVATE_MODE=false` to allow public access (use with caution — anyone can trigger API calls at your expense).

To find your chat ID, send a message to your bot and check the logs, or use [@userinfobot](https://t.me/userinfobot).

### 3. Run

```bash
# Telegram bot only (recommended for production)
./scripts/start.sh --telegram-only

# Interactive CLI
./scripts/start.sh --interactive

# Single query
./scripts/start.sh "search for PEPE on ethereum"

# Portfolio strategy (dry-run)
./scripts/start.sh --interactive --portfolio

# Portfolio strategy (live)
./scripts/start.sh --interactive --portfolio --portfolio-live
```

## Usage

### Telegram Bot

Send any token address to your bot:
- EVM: `0x6982508145454Ce325dDbE47a25d4ec3d2311933`
- Solana: `DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263`

| Command | Description |
|---------|-------------|
| `/analyze <address>` | Analyze a token |
| `/help` | Show help message |
| `/status` | Check bot status |

### Interactive CLI Commands

| Command | Description |
|---------|-------------|
| `/help` | Show available commands |
| `/clear` | Clear conversation history |
| `/context` | Show current conversation context |
| `/quit` | Exit the CLI |
| `/portfolio <subcommand>` | Portfolio strategy management |

#### Portfolio Subcommands

| Subcommand | Description |
|------------|-------------|
| `/portfolio status` | Scheduler/risk status and config summary |
| `/portfolio run` | Run one discovery cycle immediately |
| `/portfolio check` | Run one exit check cycle immediately |
| `/portfolio start` / `stop` | Start or stop the scheduler |
| `/portfolio positions` | Show open positions with unrealized PnL |
| `/portfolio close <id\|all>` | Manually close position(s) |
| `/portfolio set [param] [value]` | Show or change tunable runtime parameters |
| `/portfolio history` | Show recent closed positions with PnL |
| `/portfolio reset` | Delete closed positions and reset daily PnL |

### Portfolio Strategy

The portfolio strategy autonomously discovers promising Solana tokens, buys small positions, and exits when take-profit, stop-loss, or trailing stop conditions are met.

**How it works:**
1. **Discovery** (every 30 min): DexScreener trending → volume/liquidity filter → rugcheck safety → Gemini AI momentum scoring → buy top candidates
2. **Exit monitoring** (every 60s): Check TP/SL thresholds, update trailing stops, close expired positions
3. **Risk guards**: Max positions cap, daily loss limit, cooldown after failures, duplicate prevention

By default this runs in **dry-run mode** (`PORTFOLIO_DRY_RUN=true`).

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
| `-i, --interactive` | Start interactive CLI mode |
| `-v, --verbose` | Show debug information |
| `-o, --output` | Output format (`text`, `json`) |
| `--stdin` | Read query from stdin |
| `--telegram-only` | Run only the Telegram bot (no CLI) |
| `--no-telegram` | Disable Telegram in interactive mode |
| `--no-honeypot` | Disable honeypot MCP server |
| `--no-rugcheck` | Disable rugcheck MCP server |
| `--no-blockscout` | Disable blockscout MCP server |
| `--no-trader` | Disable trader MCP server |
| `--portfolio` | Enable portfolio strategy scheduler |
| `--portfolio-live` | Run portfolio strategy with live execution |

## Architecture

```
┌──────────────────────────────────────────────────┐
│           User Query (CLI / Telegram)            │
└──────────────────────┬───────────────────────────┘
                       │
          ┌────────────┴────────────┐
          ▼                         ▼
┌──────────────────┐     ┌──────────────────┐
│  AgenticPlanner  │     │  TokenAnalyzer   │
│  (interactive    │     │  (Telegram bot   │
│   CLI queries)   │     │   token reports) │
└────────┬─────────┘     └────────┬─────────┘
         │                        │
         └───────────┬────────────┘
                     ▼
         ┌───────────────────────┐
         │    MCP Clients        │
         │  (JSON-RPC / stdio)   │
         └───────────┬───────────┘
                     │
    ┌────────┬───────┼───────┬────────┬────────┐
    ▼        ▼       ▼       ▼        ▼        ▼
DexScreener DexPap Honeypot Rugcheck Blockscout Trader
  (price)  (pools)  (EVM)  (Solana)   (Base)  (Solana)
```

**Portfolio Strategy** runs as a separate subsystem:

```
PortfolioScheduler (discovery every 30min + exit checks every 60s)
    │
    ├── PortfolioDiscovery → DexScreener + Rugcheck + Gemini AI scoring
    │
    ├── PortfolioStrategy  → trailing stop updates, TP/SL/timeout checks
    │
    └── TraderExecution    → buy/sell via trader MCP
    │
    └── Database (SQLite)  → ~/.dex-bot/portfolio.db
```

## Prerequisites

- **Python 3.10+**
- **Node.js 18+** (for MCP servers)
- **Gemini API Key** (from [Google AI Studio](https://makersuite.google.com/app/apikey))
- **Telegram Bot Token** (optional, from [@BotFather](https://t.me/BotFather))

## Development

```bash
source .venv/bin/activate
pytest
python -m app "your query"
```

## MCP Servers

| Server | Purpose | Chains |
|--------|---------|--------|
| [dex-screener-mcp](https://github.com/dchu3/dex-screener-mcp) | Token prices, pools, volume | All |
| [dexpaprika-mcp](https://github.com/coinpaprika/dexpaprika-mcp) | Pool details, OHLCV data | All |
| [dex-honeypot-mcp](https://github.com/dchu3/dex-honeypot-mcp) | Honeypot detection | Ethereum, BSC, Base |
| [dex-rugcheck-mcp](https://github.com/dchu3/dex-rugcheck-mcp) | Token safety | Solana |
| [solana-rpc-mcp](https://github.com/dchu3/solana-rpc-mcp) | Direct Solana RPC queries | Solana |
| [dex-blockscout-mcp](https://github.com/dchu3/dex-blockscout-mcp) | Block explorer data | Base, Ethereum |
| [dex-trader-mcp](https://github.com/dchu3/dex-trader-mcp) | Token trading via Jupiter | Solana |

Each MCP server runs with its project root as the working directory and loads its own `.env` independently.

## License

MIT
