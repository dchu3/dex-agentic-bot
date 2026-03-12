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
  buildPaymentConfig,
  buildPaymentRequiredResponse,
  FACILITATOR_URL,
  type PaymentRequirements,
  type PaymentConfig,
} from "./payments.js";
import {
  globalRateLimiter,
  mcpRateLimiter,
  buildCorsMiddleware,
  securityHeaders,
  requestId,
  requestLogger,
  validateAnalyzeArgs,
  globalErrorHandler,
} from "./middleware.js";

const PYTHON_API_URL = process.env.PYTHON_API_URL ?? "http://localhost:8080";
const INTERNAL_API_SECRET = process.env.INTERNAL_API_SECRET ?? "";
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
        const internalHeaders: Record<string, string> = {
          "Content-Type": "application/json",
        };
        if (INTERNAL_API_SECRET) {
          internalHeaders["X-Internal-API-Key"] = INTERNAL_API_SECRET;
        }
        const res = await fetch(`${PYTHON_API_URL}/analyze`, {
          method: "POST",
          headers: internalHeaders,
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
          content: [{ type: "text", text: JSON.stringify(data) }],
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
    // The X-PAYMENT header is base64(JSON.stringify(paymentPayload)).
    // The facilitator expects the decoded JSON object, not the raw base64 string.
    let paymentPayload: Record<string, unknown>;
    try {
      const decoded = Buffer.from(paymentHeader, "base64").toString("utf-8");
      paymentPayload = JSON.parse(decoded) as Record<string, unknown>;
    } catch {
      console.error("Failed to decode X-PAYMENT header as base64 JSON");
      return false;
    }

    const res = await fetch(`${FACILITATOR_URL}/settle`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        x402Version: paymentPayload.x402Version ?? 2,
        paymentPayload,
        paymentRequirements: requirements,
      }),
      signal: controller.signal,
    });

    if (!res.ok) {
      const text = await res.text().catch(() => "");
      console.error(`Facilitator /settle returned ${res.status}: ${text}`);
      return false;
    }

    const body = (await res.json()) as Record<string, unknown>;
    if (body["success"] !== true) {
      console.error(`Facilitator /settle response: ${JSON.stringify(body)}`);
    }
    return body["success"] === true;
  } catch (err) {
    console.error(
      "Settlement error:",
      err instanceof Error ? err.message : String(err),
    );
    return false;
  } finally {
    clearTimeout(timeoutId);
  }
}

async function main(): Promise<void> {
  const paymentConfig = await buildPaymentConfig();
  const analyzePrice = paymentConfig.priceDescription;
  const app = express();

  // Trust proxy (for correct client IP behind Caddy/nginx)
  app.set("trust proxy", 1);

  // Security middleware
  app.use(requestId);
  app.use(requestLogger);
  app.use(securityHeaders);
  app.use(buildCorsMiddleware());
  app.use(globalRateLimiter);

  app.post(
    "/mcp",
    mcpRateLimiter,
    express.json({ limit: "1mb" }),
    async (req: Request, res: Response): Promise<void> => {
      const body = req.body as Record<string, unknown>;

      // Reject JSON-RPC batch requests — the MCP SDK processes arrays natively,
      // which would bypass per-method payment enforcement below.
      if (Array.isArray(body)) {
        res.status(400).json({ error: "Batch requests are not supported" });
        return;
      }

      const method = body?.["method"] as string | undefined;

      // Only enforce x402 payment for the paid analyze_token tool.
      // For other tool names, let the MCP server handle the request and
      // return a protocol-consistent JSON-RPC error response.
      if (method === "tools/call") {
        const params = body?.["params"] as Record<string, unknown> | undefined;
        const toolName = params?.["name"];

        if (toolName === "analyze_token") {
          // Validate tool arguments before processing payment
          const toolArgs = params?.["arguments"] as Record<string, unknown> | undefined;
          const validationError = validateAnalyzeArgs(toolArgs);
          if (validationError) {
            res.status(validationError.status).json({ error: validationError.error });
            return;
          }

          // x402 v2 uses PAYMENT-SIGNATURE; v1 used X-PAYMENT.
          // Accept both for backwards compatibility.
          const rawPaymentHeader =
            req.headers["payment-signature"] ?? req.headers["x-payment"];
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
            const { body: respBody, headerValue } = buildPaymentRequiredResponse(
              paymentConfig,
              "PAYMENT-SIGNATURE header is required",
            );
            res
              .status(402)
              .set("PAYMENT-REQUIRED", headerValue)
              .json(respBody);
            return;
          }

          const settled = await settlePayment(paymentHeader, paymentConfig.accepts[0]);

          // Audit log: payment result
          console.log(JSON.stringify({
            event: "x402_payment",
            timestamp: new Date().toISOString(),
            tool: "analyze_token",
            ip: req.ip,
            request_id: req.headers["x-request-id"],
            success: settled,
          }));

          if (!settled) {
            const { body: respBody, headerValue } = buildPaymentRequiredResponse(
              paymentConfig,
              "Payment settlement failed — invalid or already-used payment",
            );
            res
              .status(402)
              .set("PAYMENT-REQUIRED", headerValue)
              .json(respBody);
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
    res.json({ status: "ok" });
  });

  // Global error handler (must be registered last)
  app.use(globalErrorHandler);

  app.listen(SERVER_PORT, () => {
    console.log(`DEX Analysis MCP server listening on port ${SERVER_PORT}`);
    console.log(
      `Tool calls require USDC payment via x402 (facilitator: ${FACILITATOR_URL})`
    );
    console.log(`Python API: ${PYTHON_API_URL}`);
  });
}

main().catch((err) => {
  console.error("Fatal:", err instanceof Error ? err.message : String(err));
  process.exit(1);
});
