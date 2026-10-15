import { v } from "convex/values";
import { internal } from "./_generated/api";
import type { Id } from "./_generated/dataModel";
import type { QueryCtx } from "./_generated/server";
import { internalMutation, query } from "./_generated/server";
import { generateProxyAddress } from "./proxyAddress";

const MAX_ADDRESS_ATTEMPTS = 5;
const PURGE_BATCH = 100;
const CONFIRMATION_LIMIT = 5;
const RECENT_EMAIL_LIMIT = 5;

async function currentUser(ctx: QueryCtx) {
  const identity = await ctx.auth.getUserIdentity();
  if (identity === null) return null;
  return await ctx.db
    .query("users")
    .withIndex("byClerkUserId", (q) => q.eq("clerkUserId", identity.subject))
    .unique();
}

/**
 * Signed-in user's email + proxy address, or null (signed out / webhook not landed yet).
 * `forwarding` says whether pass-through to the user's own inbox is switched on.
 */
export const current = query({
  args: {},
  handler: async (ctx) => {
    const user = await currentUser(ctx);
    if (user === null) return null;
    return {
      email: user.email,
      proxyAddress: user.proxyAddress,
      forwarding: Boolean(process.env.RESEND_FORWARD_FROM),
    };
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
      .withIndex("byUserKindAndReceivedAt", (q) =>
        q.eq("userId", user._id).eq("kind", "confirmation"),
      )
      .order("desc")
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

/**
 * Newest emails of any kind for the signed-in user. Without pass-through forwarding this is
 * the only way to read mail sent to the proxy, e.g. Substack's email-change confirmation.
 */
export const recentEmails = query({
  args: {},
  handler: async (ctx) => {
    const user = await currentUser(ctx);
    if (user === null) return [];
    const rows = await ctx.db
      .query("receivedEmails")
      .withIndex("byUserAndReceivedAt", (q) => q.eq("userId", user._id))
      .order("desc")
      .take(RECENT_EMAIL_LIMIT);
    return rows.map((r) => ({
      id: r._id,
      from: r.from,
      subject: r.subject,
      receivedAt: r.receivedAt,
      pending: r.bodyStatus === "pending",
      // Many emails (e.g. Substack's confirmations) may have only an HTML part.
      text: r.text ?? (r.html ? htmlToReadableText(r.html) : null),
    }));
  },
});

const ENTITIES: Record<string, string> = {
  "&nbsp;": " ",
  "&lt;": "<",
  "&gt;": ">",
  "&quot;": '"',
  "&#39;": "'",
  "&amp;": "&",
};

function decodeEntities(s: string): string {
  return s.replace(/&(nbsp|lt|gt|quot|#39|amp);/g, (m) => ENTITIES[m]);
}

/**
 * Plain text from an HTML email for the dashboard reader: drops style/script,
 * keeps https link targets as "text (url)" so confirmation links stay clickable,
 * turns block elements into line breaks. Display only; never rendered as HTML.
 */
export function htmlToReadableText(html: string): string {
  const text = html
    .replace(/<(style|script)[\s\S]*?<\/\1>/gi, "")
    .replace(
      /<a\b[^>]*?href\s*=\s*"(https:[^"]*)"[^>]*>([\s\S]*?)<\/a>/gi,
      (_m, href: string, label: string) => `${label} (${decodeEntities(href)})`,
    )
    .replace(/<(br|\/p|\/div|\/tr|\/li|\/h[1-6])\b[^>]*>/gi, "\n")
    .replace(/<[^>]+>/g, "");
  return decodeEntities(text)
    .split("\n")
    .map((line) => line.replace(/\s+/g, " ").trim())
    .filter(Boolean)
    .join("\n");
}

/** Gmail's forwarding confirmation contains "Confirmation code: 123456789". */
export function extractConfirmationCode(text: string): string | null {
  const m = /confirmation code:\s*(\d{6,})/i.exec(text);
  return m ? m[1] : null;
}

/**
 * Create or update a user from a Clerk webhook. The proxy address is issued on
 * first creation only and never changes afterwards (updates touch email only).
 * Returns null for a user already deleted in Clerk.
 */
export const upsertFromClerk = internalMutation({
  args: { clerkUserId: v.string(), email: v.string() },
  handler: async (ctx, { clerkUserId, email }) => {
    const tombstone = await ctx.db
      .query("deletedClerkUsers")
      .withIndex("byClerkUserId", (q) => q.eq("clerkUserId", clerkUserId))
      .first();
    if (tombstone !== null) return null; // deleted in Clerk; ignore late events

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
    const tombstone = await ctx.db
      .query("deletedClerkUsers")
      .withIndex("byClerkUserId", (q) => q.eq("clerkUserId", clerkUserId))
      .first();
    if (tombstone === null) {
      // Recorded even when the user row doesn't exist yet (delete arrived first).
      await ctx.db.insert("deletedClerkUsers", { clerkUserId, deletedAt: Date.now() });
    }
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
