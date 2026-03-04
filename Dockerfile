# ---- Stage 1: Build MCP servers ----
FROM node:22-slim AS mcp-builder

RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

ARG DEXSCREENER_MCP_REF=316f6bee7b4f28a675b927eb74f4ef1ed48bdc40
ARG HONEYPOT_MCP_REF=28a95d3aed45ba285a295d778ce5ed370c679d50
ARG RUGCHECK_MCP_REF=723c89636157c4095f2eb4074d33ffbf4de3e3cc
ARG SOLANA_RPC_MCP_REF=c22d7fb5878d99d1432ed4e624f3ad3cee15e965
ARG BLOCKSCOUT_MCP_REF=371ae9f76a1db8639727eb3ccea917f158d006c4
ARG TRADER_MCP_REF=924213e355150c00d2ce34d37ef3babb67aeb223
ARG DEXPAPRIKA_MCP_VERSION=1.0.5

# Clone and build each MCP server from public GitHub repos
RUN git clone https://github.com/dchu3/dex-screener-mcp.git \
    && cd dex-screener-mcp && git checkout "$DEXSCREENER_MCP_REF" && npm ci && npm run build

RUN git clone https://github.com/dchu3/dex-honeypot-mcp.git \
    && cd dex-honeypot-mcp && git checkout "$HONEYPOT_MCP_REF" && npm ci && npm run build

RUN git clone https://github.com/dchu3/dex-rugcheck-mcp.git \
    && cd dex-rugcheck-mcp && git checkout "$RUGCHECK_MCP_REF" && npm ci && npm run build

RUN git clone https://github.com/dchu3/solana-rpc-mcp.git \
    && cd solana-rpc-mcp && git checkout "$SOLANA_RPC_MCP_REF" && npm ci && npm run build

RUN git clone https://github.com/dchu3/dex-blockscout-mcp.git \
    && cd dex-blockscout-mcp && git checkout "$BLOCKSCOUT_MCP_REF" && npm ci && npm run build

RUN git clone https://github.com/dchu3/dex-trader-mcp.git \
    && cd dex-trader-mcp && git checkout "$TRADER_MCP_REF" && npm ci && npm run build

# Install dexpaprika-mcp globally
RUN npm install -g "dexpaprika-mcp@${DEXPAPRIKA_MCP_VERSION}"


# ---- Stage 2: Python runtime ----
FROM python:3.11-slim

# Install Node.js 22 runtime (required to spawn MCP server subprocesses)
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Copy built MCP servers
COPY --from=mcp-builder /build/dex-screener-mcp /opt/mcp/dex-screener-mcp
COPY --from=mcp-builder /build/dex-honeypot-mcp /opt/mcp/dex-honeypot-mcp
COPY --from=mcp-builder /build/dex-rugcheck-mcp /opt/mcp/dex-rugcheck-mcp
COPY --from=mcp-builder /build/solana-rpc-mcp /opt/mcp/solana-rpc-mcp
COPY --from=mcp-builder /build/dex-blockscout-mcp /opt/mcp/dex-blockscout-mcp
COPY --from=mcp-builder /build/dex-trader-mcp /opt/mcp/dex-trader-mcp

# Copy globally installed dexpaprika-mcp
COPY --from=mcp-builder /usr/local/lib/node_modules/dexpaprika-mcp /usr/local/lib/node_modules/dexpaprika-mcp
RUN ln -s /usr/local/lib/node_modules/dexpaprika-mcp/dist/bin.js /usr/local/bin/dexpaprika-mcp \
    && chmod +x /usr/local/bin/dexpaprika-mcp

# Pre-configure MCP server commands (users don't need to set these)
ENV MCP_DEXSCREENER_CMD="node /opt/mcp/dex-screener-mcp/dist/index.js"
ENV MCP_DEXPAPRIKA_CMD="dexpaprika-mcp"
ENV MCP_HONEYPOT_CMD="node /opt/mcp/dex-honeypot-mcp/dist/index.js"
ENV MCP_RUGCHECK_CMD="node /opt/mcp/dex-rugcheck-mcp/dist/index.js"
ENV MCP_SOLANA_RPC_CMD="node /opt/mcp/solana-rpc-mcp/dist/index.js"
ENV MCP_BLOCKSCOUT_CMD="node /opt/mcp/dex-blockscout-mcp/dist/index.js"
ENV MCP_TRADER_CMD="node /opt/mcp/dex-trader-mcp/dist/index.js"

# Create data directory for SQLite databases
RUN mkdir -p /root/.dex-bot

# Set up Python application
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

ENTRYPOINT ["python", "-m", "app"]
CMD ["--interactive"]
