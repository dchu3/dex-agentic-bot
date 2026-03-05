/**
 * x402 payment requirements for the DEX analysis tool.
 *
 * The x402 protocol (https://x402.org) enables machine-to-machine HTTP payments.
 * Clients must include a signed USDC TransferChecked transaction in the X-PAYMENT
 * header; the facilitator verifies and settles it on Solana before the server
 * proceeds with the request.
 *
 * Payment flow:
 *   1. Client calls tool → server returns 402 + payment requirements
 *   2. Client builds partial USDC tx → retries with X-PAYMENT header
 *   3. Server calls facilitator /settle → USDC lands in SERVER_WALLET_ADDRESS
 *   4. Server runs analysis and returns the full Gemini report
 */

export const FACILITATOR_URL =
  process.env.X402_FACILITATOR_URL ?? "https://x402.org/facilitator";

/** USDC mint on Solana mainnet (6 decimals). */
const USDC_MAINNET = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v";
/** USDC mint on Solana devnet. */
const USDC_DEVNET = "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU";

/** Wire-format payment requirements object (x402 spec §4.1). */
export interface PaymentRequirements {
  scheme: string;
  network: string;
  maxAmountRequired: string;
  resource: string;
  description: string;
  mimeType: string;
  payTo: string;
  maxTimeoutSeconds: number;
  asset: string;
  outputSchema: null;
  extra: null;
}

/** Build payment requirements from environment variables. */
export function buildPaymentRequirements(): PaymentRequirements[] {
  const walletAddress = process.env.SERVER_WALLET_ADDRESS;
  if (!walletAddress) {
    throw new Error("SERVER_WALLET_ADDRESS must be set in .env");
  }

  const priceUsd = parseFloat(process.env.SERVER_PRICE_ANALYZE ?? "0.50");
  // USDC has 6 decimal places: $0.50 → 500_000 raw units
  const amountRaw = Math.round(priceUsd * 1_000_000).toString();

  const network = process.env.SERVER_SOLANA_NETWORK ?? "solana";
  const isDevnet = network.includes("devnet");
  const asset = isDevnet ? USDC_DEVNET : USDC_MAINNET;

  return [
    {
      scheme: "exact",
      network,
      maxAmountRequired: amountRaw,
      resource: "/mcp",
      description: `DEX AI token analysis — $${priceUsd.toFixed(2)} USDC`,
      mimeType: "application/json",
      payTo: walletAddress,
      maxTimeoutSeconds: 300,
      asset,
      outputSchema: null,
      extra: null,
    },
  ];
}
