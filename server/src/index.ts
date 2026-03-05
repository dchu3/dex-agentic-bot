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

/** Create a fresh McpServer for each stateless request. */
function makeMcpServer(): McpServer {
  const server = new McpServer({ name: "dex-analysis", version: "1.0.0" });

  server.tool(
    "analyze_token",
    "Full AI-powered token safety and market analysis using Gemini. " +
      "Returns safety verdict, market metrics, and a detailed AI report. " +
      "Costs USDC per call (x402 protocol).",
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
      const timeoutId = setTimeout(() => controller.abort(), 10000);
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
        const report =
          String(data["telegram_message"] ?? data["ai_analysis"] ?? "").trim() ||
          "No report generated.";
        return { content: [{ type: "text", text: report }] };
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
  try {
    const res = await fetch(`${FACILITATOR_URL}/settle`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        paymentPayload: paymentHeader,
        paymentRequirements: requirements,
      }),
    });

    if (!res.ok) return false;

    const body = (await res.json()) as Record<string, unknown>;
    return body["success"] === true;
  } catch {
    return false;
  }
}

const paymentRequirements = buildPaymentRequirements();
const app = express();
app.use(express.json());

app.post("/mcp", async (req: Request, res: Response): Promise<void> => {
  const method = (req.body as Record<string, unknown>)?.["method"] as
    | string
    | undefined;

  // Enforce payment only for tool invocations
  if (method === "tools/call") {
    const paymentHeader = req.headers["x-payment"] as string | undefined;

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

  // Dispatch through a fresh stateless MCP server instance
  const server = makeMcpServer();
  const transport = new StreamableHTTPServerTransport({
    sessionIdGenerator: undefined, // Stateless: no session state
  });

  try {
    await server.connect(transport);
    await transport.handleRequest(req, res, req.body as Record<string, unknown>);
  } finally {
    // Best-effort cleanup; ignore close errors
    await server.close().catch(() => undefined);
  }
});

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
