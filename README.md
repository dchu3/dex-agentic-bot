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

### Option A: Docker (recommended)

The easiest way to get started. All MCP servers are pre-built and bundled in the image — no need to install Node.js or clone separate repos.

```bash
git clone https://github.com/dchu3/dex-agentic-bot && cd dex-agentic-bot
cp .env.example .env
# Edit .env — set GEMINI_API_KEY (required). MCP paths are pre-configured.
docker compose run --rm bot "search for PEPE on ethereum"
```

**Run modes:**

```bash
# Interactive CLI
docker compose up

# Single query
docker compose run --rm bot "search for PEPE on ethereum"

# Telegram bot only
docker compose run --rm bot --telegram-only

# Portfolio strategy (dry-run)
docker compose run --rm bot --interactive --portfolio

# Rebuild to get latest MCP server updates
docker compose build --no-cache
```

Data (SQLite databases) is persisted in a Docker volume (`dex-bot-data`) across container restarts.

### Option B: Manual Setup

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

# Optional (defaults shown)
GEMINI_MODEL=gemini-3-flash-preview

# MCP Servers (token data sources)
MCP_DEXSCREENER_CMD=node /path/to/dex-screener-mcp/dist/index.js
MCP_DEXPAPRIKA_CMD=dexpaprika-mcp
MCP_HONEYPOT_CMD=node /path/to/dex-honeypot-mcp/dist/index.js
MCP_RUGCHECK_CMD=node /path/to/dex-rugcheck-mcp/dist/index.js
MCP_SOLANA_RPC_CMD=node /path/to/solana-rpc-mcp/dist/index.js
MCP_BLOCKSCOUT_CMD=node /path/to/dex-blockscout-mcp/dist/index.js
MCP_TRADER_CMD=node /path/to/dex-trader-mcp/dist/index.js

# Timeout (seconds) for MCP tool calls (default: 90)
MCP_CALL_TIMEOUT=90

# Solana RPC (for token decimal lookups and tx verification)
# The public endpoint is heavily rate-limited and blocks cloud IPs.
# Use a private provider such as Helius (https://helius.dev).
SOLANA_RPC_URL=https://mainnet.helius-rpc.com/?api-key=your-key

# Telegram (optional)
TELEGRAM_BOT_TOKEN=your-telegram-bot-token
TELEGRAM_CHAT_ID=your-chat-id
TELEGRAM_PRIVATE_MODE=true

# Portfolio strategy (optional)
PORTFOLIO_ENABLED=false
PORTFOLIO_DRY_RUN=true
PORTFOLIO_POSITION_SIZE_USD=5.0
PORTFOLIO_MAX_POSITIONS=5
PORTFOLIO_TAKE_PROFIT_PCT=0.0
PORTFOLIO_STOP_LOSS_PCT=17.0
PORTFOLIO_TRAILING_STOP_PCT=11.0
PORTFOLIO_SELL_PCT=45.0
```

> **Trader MCP — additional configuration required**
>
> The `dex-trader-mcp` server has its own `.env` in its project directory. Set `SOLANA_RPC_URL` (private RPC such as Helius/QuickNode) and optionally `JUPITER_API_BASE` + `JUPITER_API_KEY` there. The public Solana RPC (`api.mainnet-beta.solana.com`) and Jupiter Lite API (`lite-api.jup.ag`) block cloud/VPN IPs. See the [dex-trader-mcp README](https://github.com/dchu3/dex-trader-mcp) for details.

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
| `/analyze <address>` | Analyze a token (quick summary) |
| `/full <address>` | Detailed analysis report |
| `/help` | Show help message |
| `/status` | Check bot status |
| `/subscribe` | Subscribe to price alerts (legacy, not shown in bot help) |
| `/unsubscribe` | Unsubscribe from price alerts (legacy, not shown in bot help) |

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
1. **SOL trend gate**: Skip discovery if SOL has dropped faster than the configured threshold in the lookback window
2. **Discovery** (every 20 min): DexScreener trending → volume/liquidity/market cap filter → rugcheck safety → insider/sniper detection → heuristic momentum scoring → Gemini AI per-candidate buy/skip decision → buy approved candidates
3. **Exit monitoring** (every 40s): Check TP/SL thresholds, update trailing stops, close expired positions
4. **Risk guards**: Max positions cap, daily loss limit, cooldown after failures, duplicate prevention

**Discovery filters (configurable via `.env`):**
- `PORTFOLIO_MIN_VOLUME_USD` — Minimum 24h trading volume (default: 380k)
- `PORTFOLIO_MIN_LIQUIDITY_USD` — Minimum liquidity depth (default: 245k)
- `PORTFOLIO_MIN_MARKET_CAP_USD` — Minimum market cap or FDV (default: 1.65M)
- `PORTFOLIO_MIN_TOKEN_AGE_HOURS` — Reject tokens younger than this many hours (default: 11; 0 = disabled)
- `PORTFOLIO_MAX_TOKEN_AGE_HOURS` — Reject tokens older than this many hours (default: 0 = disabled)

**Pre-trade slippage probe (opt-in, live mode only):**

Before opening a position, optionally executes a tiny `buy_and_sell` round-trip to validate that real on-chain slippage matches the quoted slippage. If the deviation exceeds the threshold, the trade is aborted.

- `PORTFOLIO_SLIPPAGE_PROBE_ENABLED` — Enable the probe (default: `false`)
- `PORTFOLIO_SLIPPAGE_PROBE_USD` — Size of the test trade in USD (default: `0.50`)
- `PORTFOLIO_SLIPPAGE_PROBE_MAX_SLIPPAGE_PCT` — Abort if real slippage deviates more than this % from quoted price (default: `5.0`)

By default this runs in **dry-run mode** (`PORTFOLIO_DRY_RUN=true`).

**Partial sell (configurable via `.env`):**

Sell a percentage of the position on any **profitable** exit trigger (e.g. take-profit, trailing stop in profit), while keeping the remainder open with a continued trailing stop:
- `PORTFOLIO_SELL_PCT` — Percentage of the position to sell on profitable exits (default: 45; 100 = full exit)

When set below 100 and the trade is in profit, the bot partially closes the position; the remaining size stays open and the trailing stop continues tracking the remaining balance. If an exit is not profitable (at or below entry), the bot closes 100% of the position regardless of this setting.

**SOL trend gate (configurable via `.env`):**

Pauses discovery when the SOL price is dropping to avoid buying into a market-wide dump:
- `PORTFOLIO_SOL_DUMP_THRESHOLD_PCT` — Skip discovery if SOL dropped more than this % (default: -5.0)
- `PORTFOLIO_SOL_TREND_LOOKBACK_MINS` — Lookback window for the trend check (default: 60)

**Insider / sniper detection (configurable via `.env`):**

Analyzes top token holders via Solana RPC before buying. Tokens with suspicious concentration are rejected or flagged for AI review:
- `PORTFOLIO_INSIDER_CHECK_ENABLED` — Enable the check (default: `true`)
- `PORTFOLIO_INSIDER_MAX_CONCENTRATION_PCT` — Hard-reject if top-holder concentration exceeds this % (default: 50)
- `PORTFOLIO_INSIDER_MAX_CREATOR_PCT` — Hard-reject if creator holds more than this % (default: 30)
- `PORTFOLIO_INSIDER_WARN_CONCENTRATION_PCT` — Soft-flag for AI review above this % (default: 30)
- `PORTFOLIO_INSIDER_WARN_CREATOR_PCT` — Soft-flag for AI review above this % (default: 10)

**Shadow audit & decision logging (configurable via `.env`):**

Observability features for evaluating the discovery pipeline alongside normal trading behavior:
- `PORTFOLIO_SHADOW_AUDIT_ENABLED` — Record approved candidates as `shadow_positions` for an additional audit log, in parallel with normal portfolio execution (to avoid real trades, keep `PORTFOLIO_DRY_RUN=true` and do not pass `--portfolio-live`; default: `false`)
- `PORTFOLIO_SHADOW_CHECK_MINUTES` — Delay (in minutes) after a shadow position is created before it becomes eligible for a one-time price check (default: 30)
- `PORTFOLIO_DECISION_LOG_ENABLED` — Persist per-candidate reason codes for pipeline analysis (default: `false`)

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
| `-o, --output` | Output format (`text`, `json`, `table`; default: `table`) |
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
