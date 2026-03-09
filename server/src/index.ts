/**
 * DEX Analysis MCP Server — payment gateway for the Gemini token analysis service.
 *
 * Exposes a single MCP tool `analyze_token` behind an x402 USDC paywall on Solana.
 * Each tool call requires a valid USDC payment; the analysis itself is delegated
 * to the Python FastAPI service (app/api_server.py) running alongside.
 *
 * Usage (as MCP client, e.g. Claude Desktop or AI agent):
 *   transport: StreamableHTTP
 *   url: http://your-host:4022/mcp
 *
 * The server operates in stateless mode: each HTTP request is a complete
 * MCP exchange, which allows x402 payment verification per call.
 */

import "dotenv/config";
import express from "express";
import type { Request, Response } from "express";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { z } from "zod";
import {
  buildPaymentRequirements,
  FACILITATOR_URL,
  type PaymentRequirements,
} from "./payments.js";

const PYTHON_API_URL = process.env.PYTHON_API_URL ?? "http://localhost:8080";
const SERVER_PORT = parseInt(process.env.SERVER_PORT ?? "4022", 10);
const ANALYZE_TIMEOUT_MS = parseTimeoutMs(
  process.env.SERVER_ANALYZE_TIMEOUT_MS,
  30000,
  "SERVER_ANALYZE_TIMEOUT_MS"
);
const SETTLE_TIMEOUT_MS = parseTimeoutMs(
  process.env.SERVER_SETTLE_TIMEOUT_MS,
  10000,
  "SERVER_SETTLE_TIMEOUT_MS"
);

function parseTimeoutMs(
  rawValue: string | undefined,
  defaultValue: number,
  envName: string
): number {
  if (rawValue === undefined || rawValue.trim() === "") {
    return defaultValue;
  }
  const parsed = Number.parseInt(rawValue, 10);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    throw new Error(`${envName} must be a positive integer`);
  }
  return parsed;
}

/** Create a fresh McpServer for each stateless request. */
function makeMcpServer(priceDescription: string): McpServer {
  const server = new McpServer({ name: "dex-analysis", version: "1.0.0" });

  server.tool(
    "analyze_token",
    "Full AI-powered token safety and market analysis using Gemini. " +
      "Returns a structured JSON report with price_data, liquidity, safety, " +
      "holder_snapshot, ai_analysis (key_strengths, key_risks, whale_signal, " +
      "narrative_momentum), verdict (action, confidence, one_sentence), and " +
      `human_readable summary. ${priceDescription} (x402 protocol).`,
    {
      address: z.string().describe("Token contract address"),
      chain: z
        .string()
        .optional()
        .describe(
          "Blockchain network, e.g. solana, ethereum, base (auto-detected if omitted)"
        ),
    },
    async ({ address, chain }) => {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), ANALYZE_TIMEOUT_MS);
      try {
        const res = await fetch(`${PYTHON_API_URL}/analyze`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ address, chain: chain ?? null }),
          signal: controller.signal,
        });
        let data: Record<string, unknown> = {};
        try {
          data = (await res.json()) as Record<string, unknown>;
        } catch {
          data = {};
        }
        if (!res.ok) {
          return {
            content: [
              {
                type: "text",
                text: `Analysis error: ${String(data["detail"] ?? "unknown error")}`,
              },
            ],
            isError: true,
          };
        }
        return {
          content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
        };
      } catch (error: unknown) {
        const message =
          error instanceof Error && error.name === "AbortError"
            ? "Analysis request timed out. Please try again later."
            : "Analysis service request failed. Please try again later.";
        return {
          content: [
            {
              type: "text",
              text: message,
            },
          ],
          isError: true,
        };
      } finally {
        clearTimeout(timeoutId);
      }
    }
  );

  return server;
}

/** Call the x402 facilitator to settle a payment and confirm it succeeded. */
async function settlePayment(
  paymentHeader: string,
  requirements: PaymentRequirements
): Promise<boolean> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), SETTLE_TIMEOUT_MS);
  try {
    const res = await fetch(`${FACILITATOR_URL}/settle`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        paymentPayload: paymentHeader,
        paymentRequirements: requirements,
      }),
      signal: controller.signal,
    });

    if (!res.ok) return false;

    const body = (await res.json()) as Record<string, unknown>;
    return body["success"] === true;
  } catch {
    return false;
  } finally {
    clearTimeout(timeoutId);
  }
}

const paymentRequirements = buildPaymentRequirements();
const analyzePrice = paymentRequirements[0]?.description ?? "USDC per call";
const app = express();

app.post(
  "/mcp",
  express.json({ limit: "1mb" }),
  async (req: Request, res: Response): Promise<void> => {
    const body = req.body as Record<string, unknown>;
    const method = body?.["method"] as string | undefined;

    // Only enforce x402 payment for the paid analyze_token tool.
    // For other tool names, let the MCP server handle the request and
    // return a protocol-consistent JSON-RPC error response.
    if (method === "tools/call") {
      const params = body?.["params"] as Record<string, unknown> | undefined;
      const toolName = params?.["name"];

      if (toolName === "analyze_token") {
        const rawPaymentHeader = req.headers["x-payment"];
        let paymentHeader: string | undefined;
        if (Array.isArray(rawPaymentHeader)) {
          if (rawPaymentHeader.length !== 1) {
            res.status(400).json({
              error: "Invalid x-payment header — multiple values are not allowed",
            });
            return;
          }
          paymentHeader = rawPaymentHeader[0];
        } else {
          paymentHeader = rawPaymentHeader;
        }

        if (!paymentHeader) {
          res.status(402).json({
            x402Version: 1,
            error: "Payment required",
            accepts: paymentRequirements,
          });
          return;
        }

        const settled = await settlePayment(paymentHeader, paymentRequirements[0]);
        if (!settled) {
          res.status(402).json({
            x402Version: 1,
            error: "Payment settlement failed — invalid or already-used payment",
            accepts: paymentRequirements,
          });
          return;
        }
      }
    }

    // Dispatch through a fresh stateless MCP server instance
    const server = makeMcpServer(analyzePrice);
    const transport = new StreamableHTTPServerTransport({
      sessionIdGenerator: undefined, // Stateless: no session state
    });

    try {
      await server.connect(transport);
      await transport.handleRequest(req, res, body);
    } finally {
      // Best-effort cleanup; ignore close errors
      await server.close().catch(() => undefined);
    }
  }
);

app.get("/health", (_req: Request, res: Response) => {
  res.json({ status: "ok", facilitator: FACILITATOR_URL });
});

app.listen(SERVER_PORT, () => {
  console.log(`DEX Analysis MCP server listening on port ${SERVER_PORT}`);
  console.log(
    `Tool calls require USDC payment via x402 (facilitator: ${FACILITATOR_URL})`
  );
  console.log(`Python API: ${PYTHON_API_URL}`);
});
