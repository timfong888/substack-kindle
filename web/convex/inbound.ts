import { v } from "convex/values";
import { internal } from "./_generated/api";
import type { Id } from "./_generated/dataModel";
import type { ActionCtx } from "./_generated/server";
import { internalAction, internalMutation, internalQuery } from "./_generated/server";

export const GOOGLE_FORWARDING_SENDER = "forwarding-noreply@google.com";
export const RESEND_RECEIVING_API = "https://api.resend.com/emails/receiving";
export const RESEND_SEND_API = "https://api.resend.com/emails";
/** Forward tries per email (first try + retries); then give up and log for alerting. */
export const MAX_FORWARD_ATTEMPTS = 5;
/** Delay before retry n (1-based): 30s, 2m, 8m, 32m. */
const FORWARD_RETRY_BASE_MS = 30_000;
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

/**
 * From header for a forwarded copy: the original sender's name on our own verified
 * sending address. Sending as the original sender would fail its SPF/DMARC.
 */
export function forwardFromHeader(originalFrom: string, forwardAddress: string): string {
  const m = /^([\s\S]*)<[^>]*>\s*$/.exec(originalFrom);
  const name = (m && m[1].trim() ? m[1] : originalFrom)
    .replace(/["<>\\\r\n]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return `"${name} via proxy" <${forwardAddress}>`;
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
    // Guard here, before the bodies cross into the mutation: Convex caps
    // function arguments (~8 MiB), so an oversize body (e.g. base64 images)
    // would make runMutation throw and leave the row pending forever.
    const bodies = guardBodies(email.html ?? undefined, email.text ?? undefined);
    await ctx.runMutation(internal.inbound.storeBody, {
      rowId,
      from: email.from ?? undefined,
      subject: email.subject ?? undefined,
      messageId: email.message_id ?? undefined,
      createdAt: email.created_at ?? undefined,
      html: bodies.html,
      text: bodies.text,
      htmlTruncated: bodies.htmlTruncated || undefined,
      textTruncated: bodies.textTruncated || undefined,
    });

    await forwardOrScheduleRetry(ctx, { rowId, resendEmailId, apiKey, email, attempt: 1 });
  },
});

type ReceivedEmail = {
  from?: string;
  subject?: string;
  html?: string | null;
  text?: string | null;
};

/**
 * Pass-through: copy the email to the user's own inbox, so login codes and
 * receipts still reach them after their Substack email points at the proxy.
 * Off until RESEND_FORWARD_FROM (an address on a Resend-verified domain) is set.
 * A failed send is retried with backoff; the stored body and the Idempotency-Key
 * make retries safe. After MAX_FORWARD_ATTEMPTS it logs an error for alerting.
 */
async function forwardOrScheduleRetry(
  ctx: ActionCtx,
  args: {
    rowId: Id<"receivedEmails">;
    resendEmailId: string;
    apiKey: string;
    email: ReceivedEmail | null;
    attempt: number;
  },
) {
  const { rowId, resendEmailId, apiKey, email, attempt } = args;
  const forwardAddress = process.env.RESEND_FORWARD_FROM;
  if (!forwardAddress) return;
  const target = await ctx.runQuery(internal.inbound.forwardTarget, { rowId });
  if (target === null) return;

  let failure: string;
  if (email === null) {
    failure = "could not re-fetch the received email";
  } else {
    const from = email.from ?? target.from;
    try {
      const sent = await fetch(RESEND_SEND_API, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${apiKey}`,
          "Content-Type": "application/json",
          // Resend drops a repeat with the same key (24h), so a retry can't double-send.
          "Idempotency-Key": `forward-${resendEmailId}`,
        },
        body: JSON.stringify({
          from: forwardFromHeader(from, forwardAddress),
          to: [target.to],
          reply_to: from,
          subject: email.subject ?? target.subject,
          html: email.html ?? undefined,
          text: email.text ?? (email.html ? undefined : ""),
        }),
      });
      if (sent.ok) {
        await ctx.runMutation(internal.inbound.markForwarded, { rowId });
        return;
      }
      failure = `HTTP ${sent.status}`;
    } catch (err) {
      failure = err instanceof Error ? err.message : String(err);
    }
  }

  if (attempt < MAX_FORWARD_ATTEMPTS) {
    await ctx.scheduler.runAfter(
      FORWARD_RETRY_BASE_MS * 4 ** (attempt - 1),
      internal.inbound.retryForward,
      { rowId, resendEmailId, attempt: attempt + 1 },
    );
    return;
  }
  console.error(
    `Resend forward gave up after ${attempt} attempts for ${resendEmailId}: ${failure}`,
  );
}

/** Retry a failed forward: re-fetch the full email (stored bodies may be truncated) and send. */
export const retryForward = internalAction({
  args: { rowId: v.id("receivedEmails"), resendEmailId: v.string(), attempt: v.number() },
  handler: async (ctx, { rowId, resendEmailId, attempt }) => {
    const apiKey = process.env.RESEND_API_KEY;
    if (!apiKey) throw new Error("RESEND_API_KEY is not set");
    let email: ReceivedEmail | null = null;
    try {
      const res = await fetch(`${RESEND_RECEIVING_API}/${encodeURIComponent(resendEmailId)}`, {
        headers: { Authorization: `Bearer ${apiKey}` },
      });
      if (res.ok) email = (await res.json()) as ReceivedEmail;
    } catch {
      // Counted as a failed attempt below.
    }
    await forwardOrScheduleRetry(ctx, { rowId, resendEmailId, apiKey, email, attempt });
  },
});

/**
 * Where to forward a stored email, or null when it must not be forwarded:
 * already forwarded, a Gmail forwarding confirmation (meant for our dashboard),
 * a deleted user, or a user email on our own inbound domain (a mail loop).
 */
export const forwardTarget = internalQuery({
  args: { rowId: v.id("receivedEmails") },
  handler: async (ctx, { rowId }) => {
    const row = await ctx.db.get(rowId);
    if (row === null || row.forwardedAt !== undefined || row.kind !== "newsletter") return null;
    const user = await ctx.db.get(row.userId);
    if (user === null) return null;
    const inboundDomain = (process.env.RESEND_INBOUND_DOMAIN ?? "").toLowerCase();
    if (inboundDomain && user.email.toLowerCase().endsWith(`@${inboundDomain}`)) return null;
    return { to: user.email, from: row.from, subject: row.subject };
  },
});

export const markForwarded = internalMutation({
  args: { rowId: v.id("receivedEmails") },
  handler: async (ctx, { rowId }) => {
    if ((await ctx.db.get(rowId)) !== null) await ctx.db.patch(rowId, { forwardedAt: Date.now() });
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
    htmlTruncated: v.optional(v.boolean()),
    textTruncated: v.optional(v.boolean()),
  },
  handler: async (ctx, args) => {
    const row = await ctx.db.get(args.rowId);
    if (row === null) return; // user deleted meanwhile
    const from = args.from ?? row.from;
    // Re-guard as a safety net; keep flags the action already computed.
    const bodies = guardBodies(args.html, args.text);
    await ctx.db.patch(args.rowId, {
      from,
      subject: args.subject ?? row.subject,
      messageId: args.messageId ?? row.messageId,
      receivedAt: parseTimestamp(args.createdAt, row.receivedAt),
      html: bodies.html,
      text: bodies.text,
      htmlTruncated: bodies.htmlTruncated || args.htmlTruncated || undefined,
      textTruncated: bodies.textTruncated || args.textTruncated || undefined,
      bodyStatus: "stored",
      kind: classifyKind(from),
    });
  },
});
