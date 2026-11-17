// Interface for the Python digest runner. Public functions guarded by an HMAC
// over PIPELINE_SHARED_SECRET (same scheme and rationale as pipeline.ts): the
// runner never holds a Convex admin key, and the raw secret never travels.
import { v } from "convex/values";
import type { Id } from "./_generated/dataModel";
import { mutation, query } from "./_generated/server";
import { senderAddress } from "./kindle";
import { constantTimeEqual, MAX_SKEW_MS } from "./pipeline";

/** A user gets at most one scheduled digest per this interval (requests skip it). */
export const DIGEST_INTERVAL_MS = 20 * 3_600_000;
/** Post keys already delivered within this lookback are sent to the runner for dedup. */
export const DEDUP_LOOKBACK_MS = 30 * 86_400_000;

/** hex(HMAC-SHA256(secret, message)). */
export async function hmacHex(secret: string, message: string): Promise<string> {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw",
    enc.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const mac = await crypto.subtle.sign("HMAC", key, enc.encode(message));
  return Array.from(new Uint8Array(mac), (b) => b.toString(16).padStart(2, "0")).join("");
}

async function assertSigned(message: string, issuedAt: number, signature: string) {
  const secret = process.env.PIPELINE_SHARED_SECRET;
  const fresh = Number.isSafeInteger(issuedAt) && Math.abs(Date.now() - issuedAt) <= MAX_SKEW_MS;
  if (!secret || !fresh) throw new Error("Unauthorized");
  const expected = await hmacHex(secret, message);
  if (!constantTimeEqual(signature, expected)) throw new Error("Unauthorized");
}

/**
 * Users due a digest: Kindle confirmed, at least one feed, and either a pending
 * "send my first digest" request or no digest in DIGEST_INTERVAL_MS.
 * Signature message: `digest-targets:<issuedAt>`.
 */
export const targets = query({
  args: { issuedAt: v.number(), signature: v.string() },
  handler: async (ctx, { issuedAt, signature }) => {
    await assertSigned(`digest-targets:${issuedAt}`, issuedAt, signature);
    const domain = process.env.KINDLE_SENDER_DOMAIN ?? "";
    if (!domain.trim()) return [];
    const now = Date.now();
    const out = [];
    for (const user of await ctx.db.query("users").collect()) {
      if (!user.kindleConfirmedAt || !user.kindleEmail || !user.senderToken) continue;
      const requested = user.digestRequestedAt !== undefined;
      const due = user.lastDigestAt === undefined || now - user.lastDigestAt >= DIGEST_INTERVAL_MS;
      if (!requested && !due) continue;
      const feeds = await ctx.db
        .query("subscriptions")
        .withIndex("byUser", (q) => q.eq("userId", user._id))
        .collect();
      if (feeds.length === 0) continue;
      const recent = await ctx.db
        .query("deliveries")
        .withIndex("byUserAndSentAt", (q) =>
          q.eq("userId", user._id).gte("sentAt", now - DEDUP_LOOKBACK_MS),
        )
        .collect();
      out.push({
        userId: user._id,
        kindleEmail: user.kindleEmail,
        sender: senderAddress(user.senderToken, domain),
        feedUrls: feeds.map((f) => f.feedUrl),
        deliveredKeys: [...new Set(recent.filter((d) => d.status === "sent").map((d) => d.postKey))],
        lastDigestAt: user.lastDigestAt ?? null,
      });
    }
    return out;
  },
});

/**
 * Record a sent digest: upsert each post and revision, add a delivery per post,
 * stamp lastDigestAt and clear the pending request. Idempotent on digestKey.
 * Signature message: `record-digest:<userId>:<digestKey>:<issuedAt>`.
 */
export const recordDigest = mutation({
  args: {
    userId: v.id("users"),
    digestKey: v.string(),
    posts: v.array(
      v.object({
        key: v.string(),
        url: v.optional(v.string()),
        title: v.string(),
        publication: v.string(),
        hash: v.string(),
      }),
    ),
    issuedAt: v.number(),
    signature: v.string(),
  },
  handler: async (ctx, { userId, digestKey, posts, issuedAt, signature }) => {
    await assertSigned(`record-digest:${userId}:${digestKey}:${issuedAt}`, issuedAt, signature);
    const user = await ctx.db.get(userId);
    if (user === null) return;
    const now = Date.now();
    for (const p of posts) {
      const post = await ctx.db
        .query("posts")
        .withIndex("byKey", (q) => q.eq("key", p.key))
        .first();
      const postId: Id<"posts"> =
        post?._id ??
        (await ctx.db.insert("posts", {
          key: p.key,
          url: p.url,
          title: p.title,
          publication: p.publication,
          firstSeenAt: now,
        }));
      const revision = await ctx.db
        .query("contentRevisions")
        .withIndex("byPostAndHash", (q) => q.eq("postId", postId).eq("hash", p.hash))
        .first();
      const revisionId =
        revision?._id ?? (await ctx.db.insert("contentRevisions", { postId, hash: p.hash, createdAt: now }));
      const already = await ctx.db
        .query("deliveries")
        .withIndex("byUserAndPost", (q) => q.eq("userId", userId).eq("postKey", p.key))
        .collect();
      if (already.some((d) => d.digestKey === digestKey)) continue;
      await ctx.db.insert("deliveries", {
        userId,
        revisionId,
        postKey: p.key,
        digestKey,
        status: "sent",
        sentAt: now,
      });
    }
    await ctx.db.patch(userId, { lastDigestAt: now, digestRequestedAt: undefined });
  },
});

/**
 * Nothing new to send: stamp lastDigestAt so the user isn't retried every run,
 * and clear a pending request. Signature message: `no-digest:<userId>:<issuedAt>`.
 */
export const markNoDigest = mutation({
  args: { userId: v.id("users"), issuedAt: v.number(), signature: v.string() },
  handler: async (ctx, { userId, issuedAt, signature }) => {
    await assertSigned(`no-digest:${userId}:${issuedAt}`, issuedAt, signature);
    const user = await ctx.db.get(userId);
    if (user === null) return;
    await ctx.db.patch(userId, { lastDigestAt: Date.now(), digestRequestedAt: undefined });
  },
});
