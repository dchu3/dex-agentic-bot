# DEX Agentic Bot

A blockchain-agnostic CLI tool for querying token and pool information across DEXs. Powered by Gemini AI and MCP servers for DexScreener and DexPaprika.

## Prerequisites

- **Python 3.10+**
- **Node.js 18+** (for MCP servers)
- **npm** (comes with Node.js)

## Features

- 🤖 **Agentic Mode** - Gemini AI decides which tools to call based on your query
- 🔗 **Blockchain Agnostic** - Works with Ethereum, Base, Solana, Arbitrum, and more
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
MCP_DEXPAPRIKA_CMD=npx dexpaprika-mcp
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

## CLI Options

| Option | Description |
|--------|-------------|
| `-i, --interactive` | Start interactive REPL mode |
| `-o, --output {text,json,table}` | Output format (default: table) |
| `-v, --verbose` | Show debug information |
| `--stdin` | Read query from stdin |

## Interactive Commands

| Command | Description |
|---------|-------------|
| `/quit` | Exit the CLI |
| `/clear` | Clear conversation context |
| `/context` | View stored tokens |
| `/help` | Show available commands |

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
          ┌───────────────┴───────────────┐
          ▼                               ▼
┌─────────────────┐             ┌─────────────────┐
│   DexScreener   │             │   DexPaprika    │
│     (MCP)       │             │     (MCP)       │
├─────────────────┤             ├─────────────────┤
│ • searchPairs   │             │ • getNetworkPools│
│ • getTrending   │             │ • getPoolDetails │
│ • getTokenInfo  │             │ • getNetworks    │
└─────────────────┘             └─────────────────┘
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

## License

MIT