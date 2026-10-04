import { v } from "convex/values";
import { internal } from "./_generated/api";
import { internalAction, internalMutation } from "./_generated/server";

export const GOOGLE_FORWARDING_SENDER = "forwarding-noreply@google.com";
export const RESEND_RECEIVING_API = "https://api.resend.com/emails/receiving";
/** Convex documents max out at ~1 MiB; keep bodies well under it. */
export const MAX_BODY_BYTES = 900_000;

// ---------------------------------------------------------------- pure helpers

/** Bare lowercase address from `Name <addr@x>` or `addr@x`. */
export function bareAddress(raw: string): string {
  const m = /<([^>]+)>/.exec(raw);
  return (m ? m[1] : raw).trim().toLowerCase();
}

/** Unique, lowercased recipient addresses from `to` + `received_for`. */
export function collectRecipients(to: unknown, receivedFor: unknown): string[] {
  const out = new Set<string>();
  for (const list of [to, receivedFor]) {
    const items = Array.isArray(list) ? list : typeof list === "string" ? [list] : [];
    for (const item of items) {
      if (typeof item === "string" && item.trim()) out.add(bareAddress(item));
    }
  }
  return [...out];
}

export function classifyKind(from: string): "newsletter" | "confirmation" {
  return bareAddress(from) === GOOGLE_FORWARDING_SENDER ? "confirmation" : "newsletter";
}

function byteLength(s: string): number {
  return new TextEncoder().encode(s).length;
}

/**
 * Drop bodies that would push the document past the Convex size limit rather
 * than failing the write. html is checked alone first; text is then kept only
 * if html + text together still fit.
 */
export function guardBodies(html: string | undefined, text: string | undefined) {
  let htmlOut = html;
  let textOut = text;
  let htmlTruncated = false;
  let textTruncated = false;
  const htmlBytes = html ? byteLength(html) : 0;
  if (htmlBytes > MAX_BODY_BYTES) {
    htmlOut = undefined;
    htmlTruncated = true;
  }
  const keptHtmlBytes = htmlOut ? htmlBytes : 0;
  if (text && keptHtmlBytes + byteLength(text) > MAX_BODY_BYTES) {
    textOut = undefined;
    textTruncated = true;
  }
  return { html: htmlOut, text: textOut, htmlTruncated, textTruncated };
}

function parseTimestamp(value: string | undefined, fallback: number): number {
  const ms = value ? Date.parse(value) : NaN;
  return Number.isFinite(ms) ? ms : fallback;
}

// ---------------------------------------------------------------- functions

/**
 * Atomically: resolve the recipient to a user, dedupe on the Resend email id,
 * insert a pending row and schedule the body fetch. Doing the dedupe and the
 * insert in one mutation makes concurrent duplicate webhook deliveries safe
 * (Convex OCC retries the loser, which then sees the row).
 */
export const claim = internalMutation({
  args: {
    resendEmailId: v.string(),
    recipients: v.array(v.string()),
    from: v.string(),
    subject: v.string(),
    messageId: v.optional(v.string()),
    createdAt: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    let user = null;
    for (const address of args.recipients) {
      user = await ctx.db
        .query("users")
        .withIndex("byProxyAddress", (q) => q.eq("proxyAddress", address))
        .unique();
      if (user !== null) break;
    }
    if (user === null) return "unknown-recipient" as const;

    const dup = await ctx.db
      .query("receivedEmails")
      .withIndex("byResendEmailId", (q) => q.eq("resendEmailId", args.resendEmailId))
      .first();
    if (dup !== null) return "duplicate" as const;

    const rowId = await ctx.db.insert("receivedEmails", {
      userId: user._id,
      resendEmailId: args.resendEmailId,
      messageId: args.messageId,
      from: args.from,
      subject: args.subject,
      receivedAt: parseTimestamp(args.createdAt, Date.now()),
      bodyStatus: "pending",
      kind: classifyKind(args.from),
    });
    await ctx.scheduler.runAfter(0, internal.inbound.fetchBody, {
      rowId,
      resendEmailId: args.resendEmailId,
    });
    return "accepted" as const;
  },
});

/** GET the full received email from Resend and store it. Non-2xx throws. */
export const fetchBody = internalAction({
  args: { rowId: v.id("receivedEmails"), resendEmailId: v.string() },
  handler: async (ctx, { rowId, resendEmailId }) => {
    const apiKey = process.env.RESEND_API_KEY;
    if (!apiKey) throw new Error("RESEND_API_KEY is not set");
    const res = await fetch(`${RESEND_RECEIVING_API}/${encodeURIComponent(resendEmailId)}`, {
      headers: { Authorization: `Bearer ${apiKey}` },
    });
    if (!res.ok) {
      throw new Error(`Resend GET receiving/${resendEmailId} failed: HTTP ${res.status}`);
    }
    const email = (await res.json()) as {
      from?: string;
      subject?: string;
      message_id?: string;
      created_at?: string;
      html?: string | null;
      text?: string | null;
    };
    await ctx.runMutation(internal.inbound.storeBody, {
      rowId,
      from: email.from ?? undefined,
      subject: email.subject ?? undefined,
      messageId: email.message_id ?? undefined,
      createdAt: email.created_at ?? undefined,
      html: email.html ?? undefined,
      text: email.text ?? undefined,
    });
  },
});

export const storeBody = internalMutation({
  args: {
    rowId: v.id("receivedEmails"),
    from: v.optional(v.string()),
    subject: v.optional(v.string()),
    messageId: v.optional(v.string()),
    createdAt: v.optional(v.string()),
    html: v.optional(v.string()),
    text: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    const row = await ctx.db.get(args.rowId);
    if (row === null) return; // user deleted meanwhile
    const from = args.from ?? row.from;
    const bodies = guardBodies(args.html, args.text);
    await ctx.db.patch(args.rowId, {
      from,
      subject: args.subject ?? row.subject,
      messageId: args.messageId ?? row.messageId,
      receivedAt: parseTimestamp(args.createdAt, row.receivedAt),
      html: bodies.html,
      text: bodies.text,
      htmlTruncated: bodies.htmlTruncated || undefined,
      textTruncated: bodies.textTruncated || undefined,
      bodyStatus: "stored",
      kind: classifyKind(from),
    });
  },
});
