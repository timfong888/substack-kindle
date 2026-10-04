import { v } from "convex/values";
import { internal } from "./_generated/api";
import type { Id } from "./_generated/dataModel";
import type { QueryCtx } from "./_generated/server";
import { internalMutation, query } from "./_generated/server";
import { generateProxyAddress } from "./proxyAddress";

const MAX_ADDRESS_ATTEMPTS = 5;
const PURGE_BATCH = 100;
const CONFIRMATION_LIMIT = 5;

async function currentUser(ctx: QueryCtx) {
  const identity = await ctx.auth.getUserIdentity();
  if (identity === null) return null;
  return await ctx.db
    .query("users")
    .withIndex("byClerkUserId", (q) => q.eq("clerkUserId", identity.subject))
    .unique();
}

/** Signed-in user's email + proxy address, or null (signed out / webhook not landed yet). */
export const current = query({
  args: {},
  handler: async (ctx) => {
    const user = await currentUser(ctx);
    if (user === null) return null;
    return { email: user.email, proxyAddress: user.proxyAddress };
  },
});

/** Recent forwarding-confirmation emails for the signed-in user (newest first). */
export const recentConfirmations = query({
  args: {},
  handler: async (ctx) => {
    const user = await currentUser(ctx);
    if (user === null) return [];
    const rows = await ctx.db
      .query("receivedEmails")
      .withIndex("byUserAndReceivedAt", (q) => q.eq("userId", user._id))
      .order("desc")
      .filter((q) => q.eq(q.field("kind"), "confirmation"))
      .take(CONFIRMATION_LIMIT);
    return rows.map((r) => ({
      id: r._id,
      subject: r.subject,
      receivedAt: r.receivedAt,
      code: extractConfirmationCode(r.text ?? ""),
      text: r.text ?? null,
    }));
  },
});

/** Gmail's forwarding confirmation contains "Confirmation code: 123456789". */
export function extractConfirmationCode(text: string): string | null {
  const m = /confirmation code:\s*(\d{6,})/i.exec(text);
  return m ? m[1] : null;
}

/**
 * Create or update a user from a Clerk webhook. The proxy address is issued on
 * first creation only and never changes afterwards (updates touch email only).
 */
export const upsertFromClerk = internalMutation({
  args: { clerkUserId: v.string(), email: v.string() },
  handler: async (ctx, { clerkUserId, email }) => {
    const existing = await ctx.db
      .query("users")
      .withIndex("byClerkUserId", (q) => q.eq("clerkUserId", clerkUserId))
      .unique();
    if (existing !== null) {
      if (existing.email !== email) await ctx.db.patch(existing._id, { email });
      return existing._id;
    }

    const domain = process.env.RESEND_INBOUND_DOMAIN ?? "";
    for (let attempt = 0; attempt < MAX_ADDRESS_ATTEMPTS; attempt++) {
      const proxyAddress = generateProxyAddress(email, domain);
      const taken = await ctx.db
        .query("users")
        .withIndex("byProxyAddress", (q) => q.eq("proxyAddress", proxyAddress))
        .first();
      if (taken !== null) continue;
      return await ctx.db.insert("users", {
        clerkUserId,
        email,
        proxyAddress,
        createdAt: Date.now(),
      });
    }
    throw new Error(`Could not allocate a unique proxy address after ${MAX_ADDRESS_ATTEMPTS} tries`);
  },
});

/** Delete a user (Clerk `user.deleted`) and schedule removal of their stored mail. */
export const deleteFromClerk = internalMutation({
  args: { clerkUserId: v.string() },
  handler: async (ctx, { clerkUserId }) => {
    const user = await ctx.db
      .query("users")
      .withIndex("byClerkUserId", (q) => q.eq("clerkUserId", clerkUserId))
      .unique();
    if (user === null) return;
    await ctx.db.delete(user._id);
    await ctx.scheduler.runAfter(0, internal.users.purgeEmails, { userId: user._id });
  },
});

export const purgeEmails = internalMutation({
  args: { userId: v.id("users") },
  handler: async (ctx, { userId }: { userId: Id<"users"> }) => {
    const batch = await ctx.db
      .query("receivedEmails")
      .withIndex("byUserAndReceivedAt", (q) => q.eq("userId", userId))
      .take(PURGE_BATCH);
    for (const row of batch) await ctx.db.delete(row._id);
    if (batch.length === PURGE_BATCH) {
      await ctx.scheduler.runAfter(0, internal.users.purgeEmails, { userId });
    }
  },
});
