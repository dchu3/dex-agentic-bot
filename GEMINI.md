# Project Overview: DEX Agentic Bot

The DEX Agentic Bot is a command-line interface (CLI) tool designed to provide comprehensive token and decentralized exchange (DEX) pool information across multiple blockchain networks. It leverages the Gemini AI model's agentic capabilities to dynamically select and execute various "Micro-Agentic Protocol (MCP)" servers, which act as specialized tools for data retrieval and analysis.

## Key Features:
- **Agentic AI:** Uses Gemini AI for natural language understanding and intelligent tool selection.
- **Blockchain Agnostic:** Supports various blockchains like Ethereum, Base, Solana, Arbitrum, etc.
- **Honeypot Detection:** Integrates with a honeypot detection service for safety checks on tokens (currently for Ethereum, BSC, and Base).
- **Interactive CLI:** Offers a REPL mode with conversation memory and commands for context management.
- **Flexible Output:** Results can be displayed in formatted tables, raw text, or JSON for scripting.

## Architecture:

The core of the application is a Python-based "Agentic Planner" that interacts with the Gemini API. This planner interprets user queries and, using Gemini's native function calling, orchestrates calls to various MCP servers. These servers (e.g., DexScreener, DexPaprika, Honeypot) expose their functionalities as tools, converting their internal schemas into Gemini-compatible `FunctionDeclaration` objects.

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
│  DexScreener  │ │  DexPaprika   │ │   Honeypot    │ │  Blockscout   │
│    (MCP)      │ │    (MCP)      │ │    (MCP)      │ │    (MCP)      │
├───────────────┤ ├───────────────┤ ├───────────────┤ ├───────────────┤
│ • search_pairs│ │ • getPools    │ │ • check_      │ │ • search      │
│ • get_trending│ │ • getDetails  │ │   honeypot    │ │ • get_address │
│ • get_token   │ │ • getNetworks │ │               │ │ • get_token   │
│   _info       │
└───────────────┘ └───────────────┘ └───────────────┘ └───────────────┘
```

## Technologies Used:

- **Python 3.10+:** Main application logic.
- **Google Generative AI SDK:** For interacting with the Gemini API.
- **Pydantic & Pydantic Settings:** For configuration management and data validation.
- **Rich:** For rich terminal output.
- **Node.js 18+ & npm:** Used by some MCP servers (e.g., `dex-screener-mcp`, `dex-honeypot-mcp`).

## Building and Running:

### Prerequisites

- Python 3.10+
- Node.js 18+ (for MCP servers)
- npm (comes with Node.js)

### Installation

To set up the project, run the installation script:

```bash
./scripts/install.sh
```

This script creates a Python virtual environment, upgrades `pip`, and installs all necessary Python dependencies from `requirements.txt`.

### Configuration

Create a `.env` file in the project root by copying `.env.example`. You will need to fill in your `GEMINI_API_KEY` and ensure the paths to the MCP server commands are correctly configured.

Example `.env` content:
```env
GEMINI_API_KEY=your-key-here
GEMINI_MODEL=gemini-2.5-flash

# MCP Server commands (update paths as needed)
MCP_DEXSCREENER_CMD=node /path/to/dex-screener-mcp/dist/index.js
MCP_DEXPAPRIKA_CMD=dexpaprika-mcp
MCP_HONEYPOT_CMD=node /path/to/dex-honeypot-mcp/dist/index.js
MCP_BLOCKSCOUT_CMD=node /path/to/dex-blockscout-mcp/dist/index.js

# Agent settings
AGENTIC_MAX_ITERATIONS=15
AGENTIC_MAX_TOOL_CALLS=30
AGENTIC_TIMEOUT_SECONDS=120

LOG_LEVEL=INFO
```

### Usage

**Single Query:**
```bash
./scripts/start.sh "search for PEPE on ethereum"
```

**Interactive Mode:**
```bash
./scripts/start.sh --interactive
```

**JSON Output:**
```bash
./scripts/start.sh --output json "top pools on base"
```

### Development

1.  **Activate Virtual Environment:**
    ```bash
    source .venv/bin/activate
    ```
2.  **Run Tests:**
    ```bash
    pytest
    ```
3.  **Run CLI Directly:**
    ```bash
    python -m app "your query"
    ```

## Development Conventions:

- **Python Typing:** The codebase uses type hints for improved readability and maintainability.
- **Asynchronous Programming:** `asyncio` is used for concurrent execution, especially for parallel MCP tool calls.
- **Structured Logging:** Employs a logging callback mechanism for verbose output during agentic planning.
- **Modular Design:** The application is structured into clear modules for CLI, agent logic, MCP management, output handling, and type definitions.
