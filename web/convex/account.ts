// Onboarding: the Kindle step (sender address, Kindle address, test send,
// "it arrived") and the "send my first digest" request. Public functions act
// only on the signed-in user.
import { v } from "convex/values";
import { internal } from "./_generated/api";
import type { Doc } from "./_generated/dataModel";
import type { MutationCtx, QueryCtx } from "./_generated/server";
import { action, internalMutation, internalQuery, mutation, query } from "./_generated/server";
import { RESEND_SEND_API } from "./inbound";
import {
  AMAZON_STORES,
  amazonSettingsUrl,
  generateSenderToken,
  normalizeKindleEmail,
  senderAddress,
} from "./kindle";

const MAX_TOKEN_ATTEMPTS = 5;
/** Minimum gap between test sends, so the button can't be used to spam a Kindle. */
export const TEST_SEND_INTERVAL_MS = 60_000;

async function signedInUser(ctx: QueryCtx | MutationCtx): Promise<Doc<"users"> | null> {
  const identity = await ctx.auth.getUserIdentity();
  if (identity === null) return null;
  return await ctx.db
    .query("users")
    .withIndex("byClerkUserId", (q) => q.eq("clerkUserId", identity.subject))
    .unique();
}

async function requireUser(ctx: QueryCtx | MutationCtx): Promise<Doc<"users">> {
  const user = await signedInUser(ctx);
  if (user === null) throw new Error("Not signed in");
  return user;
}

function senderFor(user: Doc<"users">): string | null {
  const domain = process.env.KINDLE_SENDER_DOMAIN ?? "";
  return user.senderToken && domain.trim() ? senderAddress(user.senderToken, domain) : null;
}

/** Everything the onboarding screen shows, or null when signed out / not provisioned yet. */
export const state = query({
  args: {},
  handler: async (ctx) => {
    const user = await signedInUser(ctx);
    if (user === null) return null;
    const subscriptionCount = (
      await ctx.db
        .query("subscriptions")
        .withIndex("byUser", (q) => q.eq("userId", user._id))
        .collect()
    ).length;
    const store = user.amazonStore ?? "com";
    return {
      email: user.email,
      senderAddress: senderFor(user),
      kindleEmail: user.kindleEmail ?? null,
      amazonStore: store,
      amazonStores: AMAZON_STORES,
      amazonUrl: amazonSettingsUrl(store),
      testSentAt: user.testSentAt ?? null,
      kindleConfirmedAt: user.kindleConfirmedAt ?? null,
      digestRequestedAt: user.digestRequestedAt ?? null,
      lastDigestAt: user.lastDigestAt ?? null,
      subscriptionCount,
    };
  },
});

/** Issue the user's sender token if they don't have one yet. Idempotent. */
export const ensureSender = mutation({
  args: {},
  handler: async (ctx) => {
    const user = await requireUser(ctx);
    if (user.senderToken) return;
    for (let attempt = 0; attempt < MAX_TOKEN_ATTEMPTS; attempt++) {
      const token = generateSenderToken();
      const taken = await ctx.db
        .query("users")
        .withIndex("bySenderToken", (q) => q.eq("senderToken", token))
        .first();
      if (taken !== null) continue;
      await ctx.db.patch(user._id, { senderToken: token });
      return;
    }
    throw new Error("Could not allocate a unique sender token");
  },
});

/** Save the user's Send-to-Kindle address and Amazon store. A new address needs a new test. */
export const setKindleEmail = mutation({
  args: { kindleEmail: v.string(), amazonStore: v.optional(v.string()) },
  handler: async (ctx, { kindleEmail, amazonStore }) => {
    const user = await requireUser(ctx);
    const email = normalizeKindleEmail(kindleEmail);
    if (email === null) throw new Error("Enter your Send-to-Kindle address (it ends in @kindle.com)");
    const store = amazonStore && AMAZON_STORES.includes(amazonStore) ? amazonStore : "com";
    const changed = email !== user.kindleEmail;
    await ctx.db.patch(user._id, {
      kindleEmail: email,
      amazonStore: store,
      ...(changed ? { kindleConfirmedAt: undefined, testSentAt: undefined } : {}),
    });
  },
});

/** The user saw the test document on their Kindle. */
export const confirmKindle = mutation({
  args: {},
  handler: async (ctx) => {
    const user = await requireUser(ctx);
    if (!user.testSentAt) throw new Error("Send a test first");
    await ctx.db.patch(user._id, { kindleConfirmedAt: Date.now() });
  },
});

/** Ask the digest runner for a digest now. Needs a confirmed Kindle and at least one subscription. */
export const requestDigest = mutation({
  args: {},
  handler: async (ctx) => {
    const user = await requireUser(ctx);
    if (!user.kindleConfirmedAt) throw new Error("Confirm your Kindle first");
    const any = await ctx.db
      .query("subscriptions")
      .withIndex("byUser", (q) => q.eq("userId", user._id))
      .first();
    if (any === null) throw new Error("Add at least one publication first");
    await ctx.db.patch(user._id, { digestRequestedAt: Date.now() });
  },
});

export const testSendTarget = internalQuery({
  args: { clerkUserId: v.string() },
  handler: async (ctx, { clerkUserId }) => {
    const user = await ctx.db
      .query("users")
      .withIndex("byClerkUserId", (q) => q.eq("clerkUserId", clerkUserId))
      .unique();
    if (user === null) return null;
    return {
      userId: user._id,
      from: senderFor(user),
      to: user.kindleEmail ?? null,
      testSentAt: user.testSentAt ?? null,
    };
  },
});

export const markTestSent = internalMutation({
  args: { userId: v.id("users"), at: v.number() },
  handler: async (ctx, { userId, at }) => {
    await ctx.db.patch(userId, { testSentAt: at });
  },
});

/** A one-page HTML document; Send to Kindle converts it into a book in the library. */
export function testDocument(sender: string): string {
  return [
    "<!doctype html><html><head><meta charset='utf-8'><title>Substack Digest: test</title></head>",
    "<body><h1>It works</h1>",
    "<p>This test came from your personal sender address:</p>",
    `<p><code>${sender}</code></p>`,
    "<p>Go back to the app and tap <strong>It arrived</strong>.</p>",
    "</body></html>",
  ].join("");
}

/** Send a one-page test document from the user's sender address to their Kindle. */
export const sendTest = action({
  args: {},
  handler: async (ctx) => {
    const identity = await ctx.auth.getUserIdentity();
    if (identity === null) throw new Error("Not signed in");
    const target = await ctx.runQuery(internal.account.testSendTarget, {
      clerkUserId: identity.subject,
    });
    if (target === null || target.from === null) throw new Error("Your sender address isn't ready");
    if (target.to === null) throw new Error("Save your Kindle address first");
    const now = Date.now();
    if (target.testSentAt !== null && now - target.testSentAt < TEST_SEND_INTERVAL_MS) {
      throw new Error("A test was just sent; wait a minute before sending another");
    }
    const apiKey = process.env.RESEND_API_KEY;
    if (!apiKey) throw new Error("Sending is not configured");
    const res = await fetch(RESEND_SEND_API, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${apiKey}`,
        "Content-Type": "application/json",
        "Idempotency-Key": `kindle-test-${target.userId}-${Math.floor(now / TEST_SEND_INTERVAL_MS)}`,
      },
      body: JSON.stringify({
        from: target.from,
        to: [target.to],
        subject: "Substack Digest test",
        text: "Your Substack Digest test document is attached.",
        attachments: [
          {
            filename: "Substack Digest test.html",
            content: btoa(testDocument(target.from)),
          },
        ],
      }),
    });
    if (!res.ok) {
      // Status only: the response body can echo addresses, and logs are not private.
      console.error(`kindle test send failed: HTTP ${res.status}`);
      throw new Error("The test could not be sent. Try again in a minute.");
    }
    await ctx.runMutation(internal.account.markTestSent, { userId: target.userId, at: now });
  },
});
