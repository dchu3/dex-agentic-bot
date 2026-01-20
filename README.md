# DEX Agentic Bot

A blockchain-agnostic CLI tool for querying token and pool information across DEXs. Powered by Gemini AI and MCP servers for DexScreener, DexPaprika, and Honeypot detection.

## Prerequisites

- **Python 3.10+**
- **Node.js 18+** (for MCP servers)
- **npm** (comes with Node.js)

## Features

- 🤖 **Agentic Mode** - Gemini AI decides which tools to call based on your query
- 🔗 **Blockchain Agnostic** - Works with Ethereum, Base, Solana, Arbitrum, and more
- 🛡️ **Honeypot Detection** - Automatic safety checks for tokens on Ethereum, BSC, and Base
- 📊 **Table Output** - Results displayed in clean, formatted tables
- 💬 **Interactive Mode** - REPL with conversation memory

## Quick Start

### Installation

```bash
./scripts/install.sh
```

### Configuration

Create a `.env` file (copy from `.env.example`):

```env
GEMINI_API_KEY=your-key-here
GEMINI_MODEL=gemini-2.5-flash

# MCP Server commands
MCP_DEXSCREENER_CMD=npx @mcp-dexscreener/server
MCP_DEXPAPRIKA_CMD=dexpaprika-mcp
MCP_HONEYPOT_CMD=node /path/to/dex-honeypot-mcp/dist/index.js
```

### Usage

```bash
# Single query
./scripts/start.sh "search for PEPE on ethereum"

# Interactive mode
./scripts/start.sh --interactive

# JSON output for scripting
./scripts/start.sh --output json "top pools on base"
```

## Example Queries

| Query | Description |
|-------|-------------|
| `search for PEPE` | Search tokens by name/symbol |
| `trending tokens` | Get latest trending tokens |
| `top pools on base` | Top pools by volume on Base |
| `new pools on ethereum` | Recently created pools |
| `token info for 0x...` | Get info for specific token |
| `is 0x... a honeypot on base` | Check token safety (Ethereum/BSC/Base only) |
| `get 7-day OHLCV for SOL/USDC on Raydium` | Get OHLCV data for a pool (returns table) |
| `compare Uniswap vs SushiSwap volume` | Compare trading volume between DEXs |
| `network dexes on ethereum` | List DEXs on a network via DexPaprika |

> **Note:** Chart plotting is not available. OHLCV data is returned as formatted tables.

## CLI Options

| Option | Description |
|--------|-------------|
| `-i, --interactive` | Start interactive REPL mode |
| `-o, --output {text,json,table}` | Output format (default: table) |
| `-v, --verbose` | Show debug information |
| `--stdin` | Read query from stdin |
| `--no-honeypot` | Disable honeypot MCP server (faster startup) |
| `--no-rugcheck` | Disable rugcheck MCP server (faster startup) |
| `--no-polling` | Disable background price polling for watchlist alerts |
| `--poll-interval` | Watchlist polling interval in seconds (default: 60, min: 10, max: 3600) |
| `--no-telegram` | Disable Telegram notifications |
| `--autonomous` | Enable autonomous watchlist management |
| `--autonomous-interval` | Autonomous cycle interval in minutes (default: 60) |

## Interactive Commands

| Command | Description |
|---------|-------------|
| `/quit` | Exit the CLI |
| `/clear` | Clear conversation context |
| `/context` | View stored tokens |
| `/watch <token> [chain]` | Add token to watchlist |
| `/unwatch <token>` | Remove token from watchlist |
| `/watchlist` | Show all watched tokens with prices |
| `/alert <token> above\|below <price>` | Set price alert threshold |
| `/alerts` | Show triggered alerts |
| `/alerts clear` | Acknowledge all alerts |
| `/alerts history` | Show alert history |
| `/autonomous` | Show autonomous management commands |
| `/autonomous status` | Show autonomous scheduler status |
| `/autonomous run` | Trigger immediate autonomous cycle |
| `/autonomous start` | Start autonomous scheduler |
| `/autonomous stop` | Stop autonomous scheduler |
| `/autonomous list` | List autonomously managed tokens |
| `/autonomous clear` | Remove all autonomous tokens |
| `/help` | Show available commands |

## Watchlist & Alerts

The bot includes a persistent watchlist with background price monitoring and alerts.

### Features

- **Persistent Storage**: Watchlist stored in SQLite at `~/.dex-bot/watchlist.db`
- **Background Polling**: Automatic price checks every 60 seconds (configurable)
- **Price Alerts**: Set thresholds to be notified when prices cross above or below targets
- **Alert History**: All triggered alerts are logged and can be reviewed
- **Telegram Notifications**: Receive alerts via Telegram bot (optional)

### Configuration

Add these optional settings to your `.env`:

```env
# Watchlist settings
WATCHLIST_DB_PATH=~/.dex-bot/watchlist.db
WATCHLIST_POLL_INTERVAL=60          # Seconds between price checks
WATCHLIST_POLL_ENABLED=true         # Enable/disable background polling

# Telegram notifications (optional)
TELEGRAM_BOT_TOKEN=your-bot-token   # From @BotFather
TELEGRAM_CHAT_ID=your-chat-id       # Your Telegram user/chat ID
TELEGRAM_ALERTS_ENABLED=true        # Enable Telegram notifications
```

### Telegram Setup

To receive alerts via Telegram:

1. **Create a bot**: Message [@BotFather](https://t.me/BotFather) on Telegram
   - Send `/newbot` and follow the prompts
   - Copy the bot token (e.g., `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`)

2. **Get your chat ID**: Message [@userinfobot](https://t.me/userinfobot)
   - It will reply with your user ID (e.g., `987654321`)

3. **Start your bot**: Open your bot in Telegram and click "Start"

4. **Configure**: Add to your `.env`:
   ```env
   TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
   TELEGRAM_CHAT_ID=987654321
   TELEGRAM_ALERTS_ENABLED=true
   ```

When alerts trigger, you'll receive messages like:
```
🔔 Price Alert

Token: PEPE
Chain: ethereum
Type: 🔺 Crossed above $0.00002
Current Price: $0.000021

⏰ 2026-01-11 11:15:00 UTC
```

### Example Usage

```bash
# Start interactive mode
./scripts/start.sh --interactive

# With custom polling interval (5 minutes)
./scripts/start.sh --interactive --poll-interval 300

# Add a token to watchlist (from recent search results)
> /watch PEPE ethereum

# Set a price alert
> /alert PEPE above 0.00002

# View your watchlist
> /watchlist

# Background alerts appear automatically:
# 🔺 ALERT: PEPE (ethereum) crossed above $0.00002 (current: $0.000021)

# View and clear alerts
> /alerts
> /alerts clear
```

## Autonomous Watchlist Management

The bot includes an **autonomous agent** that can automatically discover, manage, and monitor Solana tokens with upward momentum potential.

### Features

- **Automatic Discovery**: Finds trending Solana tokens with strong momentum indicators
- **Smart Triggers**: Sets take-profit (10% above) and stop-loss (5% below) automatically
- **Hourly Reviews**: Re-evaluates positions every 60 minutes (configurable)
- **Position Management**: Maintains up to 5 tokens, replacing underperformers
- **Trailing Stops**: Automatically raises stop-loss as price increases
- **Telegram Alerts**: Sends notifications for adds, removes, and trigger updates

### How It Works

```
Hour 0 (Discovery):
  Agent searches trending Solana tokens
  → Analyzes volume, price momentum, liquidity
  → Runs rugcheck safety analysis
  → Adds top 5 candidates with triggers:
    BONK: price=$0.00002, ↑$0.000022, ↓$0.000019

Hour 1 (Review):
  Agent reviews current positions
  → BONK: +15% ✅ KEEP, raise stop to $0.000021
  → WIF: -8% ❌ REPLACE with ZEUS (better momentum)
  → Updates triggers, sends Telegram notification
```

### Configuration

Add to your `.env`:

```env
# Autonomous Agent settings
AUTONOMOUS_ENABLED=true
AUTONOMOUS_INTERVAL_MINS=60     # Cycle interval (5-1440 minutes)
AUTONOMOUS_MAX_TOKENS=5         # Max tokens in watchlist (1-20)
AUTONOMOUS_CHAIN=solana         # Target blockchain
AUTONOMOUS_MIN_VOLUME_USD=10000 # Minimum 24h volume
AUTONOMOUS_MIN_LIQUIDITY_USD=5000  # Minimum liquidity
```

### Usage

```bash
# Start with autonomous mode enabled
./scripts/start.sh --interactive --autonomous

# Or with custom interval (30 minutes)
./scripts/start.sh --interactive --autonomous --autonomous-interval 30
```

### Commands

```bash
# Check autonomous scheduler status
> /autonomous status

# Manually trigger a cycle
> /autonomous run

# View autonomously managed tokens
> /autonomous list

# Start/stop the scheduler
> /autonomous start
> /autonomous stop

# Clear all autonomous positions
> /autonomous clear
```

### Telegram Notifications

When enabled, you'll receive messages like:

```
🤖 Autonomous Watchlist Update
⏰ 2026-01-14 12:00 UTC

📈 New Positions:
  • BONK @ $0.00002345 🟢 +15.2%
    📊 Score: 78 | Vol: $1,234,567

🔄 Updated Triggers:
  • WIF: ↑$0.0012 ↓$0.00098

📋 Added: BONK | Updated: WIF
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        User Query (CLI)                     │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                    Gemini Agentic Agent                     │
│  - Analyzes user query                                      │
│  - Selects tools dynamically                                │
│  - Multi-turn reasoning                                     │
│  - Table-formatted responses                                │
└─────────────────────────┬───────────────────────────────────┘
                          │
        ┌─────────────────┼─────────────────┬─────────────────┐
        ▼                 ▼                 ▼                 ▼
┌───────────────┐ ┌───────────────┐ ┌───────────────┐ ┌───────────────┐
│  DexScreener  │ │  DexPaprika   │ │   Honeypot    │ │   Rugcheck    │
│    (MCP)      │ │    (MCP)      │ │    (MCP)      │ │    (MCP)      │
├───────────────┤ ├───────────────┤ ├───────────────┤ ├───────────────┤
│ • searchPairs │ │ • getPools    │ │ • check_      │ │ • token       │
│ • getTrending │ │ • getDetails  │ │   honeypot    │ │   safety      │
│ • getTokenInfo│ │ • getNetworks │ │ (ETH/BSC/Base)│ │   (Solana)    │
└───────────────┘ └───────────────┘ └───────────────┘ └───────────────┘
```

## Development

```bash
# Activate virtual environment
source .venv/bin/activate

# Run tests
pytest

# Run the CLI directly
python -m app "your query"
```

## MCP Server: DexScreener

This project uses the DexScreener MCP server for querying DEX data.

**GitHub Repository:** [https://github.com/janswist/mcp-dexscreener](https://github.com/janswist/mcp-dexscreener)

### Installing DexScreener MCP Locally

```bash
# Clone the repository
git clone https://github.com/janswist/mcp-dexscreener.git
cd mcp-dexscreener

# Install dependencies
npm install

# Run the server (STDIO mode)
npm start

# Or run SSE mode for remote hosting
node index-sse.js
```

### Available DexScreener Tools

| Tool | Description | Rate Limit |
|------|-------------|------------|
| `getLatestTokenProfiles` | Get the latest token profiles | 60/min |
| `getLatestBoostedTokens` | Get the latest boosted tokens | 60/min |
| `getMostActiveBoostedTokens` | Get tokens with most active boosts | 60/min |
| `checkTokenOrders` | Check orders paid for a token | 60/min |
| `getPairByChainAndAddress` | Get pairs by chain and pair address | 300/min |
| `searchPairs` | Search for pairs matching a query | 300/min |
| `getTokenPools` | Get pools for a given token address | 300/min |
| `getPairsByToken` | Get pairs by token address | 300/min |

## MCP Server: DexPaprika

This project uses the DexPaprika MCP server for querying pool and token data across networks.

### Installing DexPaprika MCP

```bash
pip install dexpaprika-mcp
```

Or install from source: [https://github.com/coinpaprika/dexpaprika-mcp](https://github.com/coinpaprika/dexpaprika-mcp)

### Available DexPaprika Tools

| Tool | Description |
|------|-------------|
| `getNetworks` | Get all supported blockchain networks |
| `getNetworkDexes` | Get DEXs available on a specific network |
| `getNetworkPools` | Get top pools on a network (by volume, liquidity, etc.) |
| `getDexPools` | Get pools from a specific DEX |
| `getPoolDetails` | Get detailed info for a specific pool |
| `getPoolOHLCV` | Get OHLCV price data for a pool (requires `start` date) |
| `getPoolTransactions` | Get recent transactions for a pool |
| `getTokenDetails` | Get detailed info for a token |
| `getTokenPools` | Get pools containing a specific token |
| `getTokenMultiPrices` | Get prices for multiple tokens |
| `search` | Search across all networks for tokens/pools |
| `getStats` | Get ecosystem statistics |

### Configuring for Claude Desktop

Add to your `claude_desktop_config.json`:

**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows:** `%AppData%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "dexscreener": {
      "command": "node",
      "args": ["/path/to/mcp-dexscreener/index.js"]
    }
  }
}
```

### Configuring for VS Code / Cursor

Add to your MCP settings:

```json
{
  "mcp": {
    "servers": {
      "dexscreener": {
        "command": "node",
        "args": ["/path/to/mcp-dexscreener/index.js"]
      }
    }
  }
}
```

## MCP Server: Honeypot Detection

This project uses a honeypot detection MCP server to check token safety using the [honeypot.is](https://honeypot.is) API.

### Supported Chains

| Chain | Status |
|-------|--------|
| Ethereum | ✅ Supported |
| BSC | ✅ Supported |
| Base | ✅ Supported |
| Solana | ❌ Not supported (use Rugcheck instead) |
| Other chains | ❌ Not supported (marked as Unverified) |

### Safety Status Meanings

| Status | Meaning |
|--------|---------|
| ✅ Safe | Honeypot check passed - low risk, not a honeypot |
| ⚠️ Risky | Honeypot check shows concerns - high taxes or medium/high risk |
| ❌ Honeypot | Confirmed honeypot - avoid trading |
| Unverified | Chain not supported or check failed |

### Installing Honeypot MCP

```bash
# Clone the repository
git clone https://github.com/dchu3/dex-honeypot-mcp.git
cd dex-honeypot-mcp

# Install dependencies
npm install

# Build
npm run build
```

### Configuration

Add to your `.env`:

```env
MCP_HONEYPOT_CMD=node /path/to/dex-honeypot-mcp/dist/index.js
```

Optional: Set `HONEYPOT_API_KEY` environment variable for higher rate limits (see [honeypot.is docs](https://docs.honeypot.is)).

### Available Tool

| Tool | Description | Parameters |
|------|-------------|------------|
| `check_honeypot` | Check if a token is a honeypot | `address` (required), `chain` (optional: ethereum/bsc/base) |

## MCP Server: Rugcheck (Solana)

This project uses the Rugcheck MCP server to check Solana token safety using the [rugcheck.xyz](https://rugcheck.xyz) API.

### Supported Chains

| Chain | Status |
|-------|--------|
| Solana | ✅ Supported |
| Other chains | ❌ Not supported (use Honeypot for EVM chains) |

### Safety Status Meanings

| Status | Meaning |
|--------|---------|
| ✅ Safe | Rugcheck passed - low risk indicators |
| ⚠️ Risky | Rugcheck shows concerns - potential risks detected |
| ❌ Rug | Confirmed rug pull risk - avoid trading |
| Unverified | Check failed or token not found |

### Installing Rugcheck MCP

```bash
# Clone the repository
git clone https://github.com/dchu3/dex-rugcheck-mcp.git
cd dex-rugcheck-mcp

# Install dependencies
npm install

# Build
npm run build
```

### Configuration

Add to your `.env`:

```env
MCP_RUGCHECK_CMD=node /path/to/dex-rugcheck-mcp/dist/index.js
```

### Available Tools

The Rugcheck MCP server provides tools to get token safety summaries for Solana tokens. Refer to the server documentation for specific tool details.

## License

MIT
