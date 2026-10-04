import { v } from "convex/values";
import { query } from "./_generated/server";

/*
 * Read path for the Python digest pipeline.
 *
 * Why a public query guarded by a shared secret: the Python `convex` client
 * calls public functions over HTTP with an optional *user* JWT. It has no
 * supported way to call internal functions, and we don't want the pipeline to
 * hold a deploy/admin key (which can push code and read every table). So this
 * query is public but refuses to run unless the caller presents
 * PIPELINE_SHARED_SECRET (set via `npx convex env set`). It fails closed when
 * the env var is unset. Rotate the secret by setting a new value in Convex and
 * in the pipeline's environment.
 */

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

function assertPipelineSecret(secret: string) {
  const expected = process.env.PIPELINE_SHARED_SECRET;
  if (!expected || !constantTimeEqual(secret, expected)) {
    throw new Error("Unauthorized");
  }
}

/**
 * Newsletter emails (bodies stored) received by `proxyAddress` in the window
 * [start, end), as epoch milliseconds, oldest first.
 */
export const listInboundForWindow = query({
  args: {
    secret: v.string(),
    proxyAddress: v.string(),
    start: v.number(),
    end: v.number(),
  },
  handler: async (ctx, { secret, proxyAddress, start, end }) => {
    assertPipelineSecret(secret);
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
