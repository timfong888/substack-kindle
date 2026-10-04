import { v } from "convex/values";
import { query } from "./_generated/server";

/*
 * Read path for the Python digest pipeline.
 *
 * Why a public query guarded by a shared secret: the Python `convex` client
 * calls public functions over HTTP with an optional *user* JWT. It has no
 * supported way to call internal functions, and we don't want the pipeline to
 * hold a deploy/admin key (which can push code and read every table). So this
 * query is public but refuses to run unless the caller proves it holds
 * PIPELINE_SHARED_SECRET (set via `npx convex env set`).
 *
 * The raw secret never travels as an argument (function arguments can show up
 * in Convex's logs and dashboard). Instead the caller sends
 *   signature = hex(HMAC-SHA256(secret, `${proxyAddress}:${start}:${end}:${issuedAt}`))
 * with integer epoch-millisecond values. A signature is bound to one address
 * and one window, and is only accepted within MAX_SKEW_MS of `issuedAt`, so a
 * leaked log line can't be reused for another inbox or replayed later.
 * Fails closed when the env var is unset. Rotate by setting a new value in
 * Convex and in the pipeline's environment.
 */

/** How far `issuedAt` may be from the server clock, either direction. */
export const MAX_SKEW_MS = 5 * 60_000;

/** hex(HMAC-SHA256(secret, "address:start:end:issuedAt")); integers only. */
export async function pipelineSignature(
  secret: string,
  proxyAddress: string,
  start: number,
  end: number,
  issuedAt: number,
): Promise<string> {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw",
    enc.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const mac = await crypto.subtle.sign(
    "HMAC",
    key,
    enc.encode(`${proxyAddress}:${start}:${end}:${issuedAt}`),
  );
  return Array.from(new Uint8Array(mac), (b) => b.toString(16).padStart(2, "0")).join("");
}

/** Constant-time string comparison (over UTF-8 bytes); length mismatch still scans. */
export function constantTimeEqual(a: string, b: string): boolean {
  const enc = new TextEncoder();
  const x = enc.encode(a);
  const y = enc.encode(b);
  const len = Math.max(x.length, y.length);
  let diff = x.length ^ y.length;
  for (let i = 0; i < len; i++) diff |= (x[i] ?? 0) ^ (y[i] ?? 0);
  return diff === 0;
}

async function assertPipelineSignature(args: {
  proxyAddress: string;
  start: number;
  end: number;
  issuedAt: number;
  signature: string;
}) {
  const secret = process.env.PIPELINE_SHARED_SECRET;
  const { proxyAddress, start, end, issuedAt, signature } = args;
  const canonical = [start, end, issuedAt].every(Number.isSafeInteger);
  const fresh = Math.abs(Date.now() - issuedAt) <= MAX_SKEW_MS;
  if (!secret || !canonical || !fresh) throw new Error("Unauthorized");
  const expected = await pipelineSignature(secret, proxyAddress, start, end, issuedAt);
  if (!constantTimeEqual(signature, expected)) throw new Error("Unauthorized");
}

/**
 * Newsletter emails (bodies stored) received by `proxyAddress` in the window
 * [start, end), as epoch milliseconds, oldest first.
 */
export const listInboundForWindow = query({
  args: {
    proxyAddress: v.string(),
    start: v.number(),
    end: v.number(),
    issuedAt: v.number(),
    signature: v.string(),
  },
  handler: async (ctx, args) => {
    await assertPipelineSignature(args);
    const { proxyAddress, start, end } = args;
    const user = await ctx.db
      .query("users")
      .withIndex("byProxyAddress", (q) => q.eq("proxyAddress", proxyAddress.trim().toLowerCase()))
      .unique();
    if (user === null) return [];
    const rows = await ctx.db
      .query("receivedEmails")
      .withIndex("byUserAndReceivedAt", (q) =>
        q.eq("userId", user._id).gte("receivedAt", start).lt("receivedAt", end),
      )
      .collect();
    return rows
      .filter((r) => r.kind === "newsletter" && r.bodyStatus === "stored")
      .map((r) => ({
        resendEmailId: r.resendEmailId,
        messageId: r.messageId ?? null,
        from: r.from,
        subject: r.subject,
        receivedAt: r.receivedAt,
        html: r.html ?? null,
        text: r.text ?? null,
        htmlTruncated: r.htmlTruncated ?? false,
      }));
  },
});
