// The publications a user reads, saved as RSS feed URLs. Users add them by
// pasting a URL or Substack handle, or by searching Substack by name.
import { v } from "convex/values";
import { internal } from "./_generated/api";
import type { Doc } from "./_generated/dataModel";
import type { MutationCtx, QueryCtx } from "./_generated/server";
import { action, internalMutation, internalQuery, mutation, query } from "./_generated/server";
import {
  candidateFeedUrls,
  feedTitle,
  findAlternateFeeds,
  isPublicHttpUrl,
  looksLikeFeed,
} from "./feeds";

export const MAX_SUBSCRIPTIONS = 300;
const FETCH_TIMEOUT_MS = 8_000;
const MAX_BODY_CHARS = 2_000_000;
const SEARCH_LIMIT = 8;
/** Substack's public publication search, as used by its own site search. */
export const SUBSTACK_SEARCH_API = "https://substack.com/api/v1/publication/search";

async function userFor(ctx: QueryCtx | MutationCtx): Promise<Doc<"users"> | null> {
  const identity = await ctx.auth.getUserIdentity();
  if (identity === null) return null;
  return await ctx.db
    .query("users")
    .withIndex("byClerkUserId", (q) => q.eq("clerkUserId", identity.subject))
    .unique();
}

/** The signed-in user's publications, A to Z. */
export const list = query({
  args: {},
  handler: async (ctx) => {
    const user = await userFor(ctx);
    if (user === null) return [];
    const rows = await ctx.db
      .query("subscriptions")
      .withIndex("byUser", (q) => q.eq("userId", user._id))
      .collect();
    return rows
      .map((r) => ({ id: r._id, title: r.title, feedUrl: r.feedUrl }))
      .sort((a, b) => a.title.localeCompare(b.title));
  },
});

export const remove = mutation({
  args: { id: v.id("subscriptions") },
  handler: async (ctx, { id }) => {
    const user = await userFor(ctx);
    const row = await ctx.db.get(id);
    if (user === null || row === null || row.userId !== user._id) return;
    await ctx.db.delete(id);
  },
});

export const userIdForClerk = internalQuery({
  args: { clerkUserId: v.string() },
  handler: async (ctx, { clerkUserId }) => {
    const user = await ctx.db
      .query("users")
      .withIndex("byClerkUserId", (q) => q.eq("clerkUserId", clerkUserId))
      .unique();
    if (user === null) return null;
    const count = (
      await ctx.db
        .query("subscriptions")
        .withIndex("byUser", (q) => q.eq("userId", user._id))
        .collect()
    ).length;
    return { userId: user._id, full: count >= MAX_SUBSCRIPTIONS };
  },
});

/** Insert unless the user already has this feed. Returns "added" | "exists" | "full". */
export const insert = internalMutation({
  args: { userId: v.id("users"), feedUrl: v.string(), title: v.string() },
  handler: async (ctx, { userId, feedUrl, title }) => {
    // The user may have been deleted while the feed was being resolved.
    if ((await ctx.db.get(userId)) === null) return "full" as const;
    const existing = await ctx.db
      .query("subscriptions")
      .withIndex("byUserAndFeed", (q) => q.eq("userId", userId).eq("feedUrl", feedUrl))
      .first();
    if (existing !== null) return "exists" as const;
    const count = (
      await ctx.db
        .query("subscriptions")
        .withIndex("byUser", (q) => q.eq("userId", userId))
        .collect()
    ).length;
    if (count >= MAX_SUBSCRIPTIONS) return "full" as const;
    await ctx.db.insert("subscriptions", { userId, feedUrl, title, addedAt: Date.now() });
    return "added" as const;
  },
});

const MAX_REDIRECTS = 3;

/**
 * GET a URL, following at most MAX_REDIRECTS redirects by hand so every hop is
 * checked before it is requested (a public feed must not be able to redirect us
 * to an internal address).
 */
async function fetchText(url: string): Promise<{ finalUrl: string; body: string } | null> {
  let current = url;
  try {
    for (let hop = 0; hop <= MAX_REDIRECTS; hop++) {
      if (!isPublicHttpUrl(current)) return null;
      const res = await fetch(current, {
        redirect: "manual",
        signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
        headers: { Accept: "application/rss+xml, application/atom+xml, text/html;q=0.8" },
      });
      if (res.status >= 300 && res.status < 400) {
        const location = res.headers.get("location");
        if (!location) return null;
        current = new URL(location, current).toString();
        continue;
      }
      if (!res.ok) return null;
      const body = (await res.text()).slice(0, MAX_BODY_CHARS);
      return { finalUrl: current, body };
    }
    return null;
  } catch {
    return null;
  }
}

/** First candidate that is a real feed (following one HTML page's alternate links). */
export async function resolveFeed(input: string): Promise<{ feedUrl: string; title: string } | null> {
  for (const candidate of candidateFeedUrls(input)) {
    const doc = await fetchText(candidate);
    if (doc === null) continue;
    if (looksLikeFeed(doc.body)) {
      return { feedUrl: doc.finalUrl, title: feedTitle(doc.body) ?? new URL(doc.finalUrl).host };
    }
    for (const alternate of findAlternateFeeds(doc.body, doc.finalUrl)) {
      const feed = await fetchText(alternate);
      if (feed !== null && looksLikeFeed(feed.body)) {
        return { feedUrl: feed.finalUrl, title: feedTitle(feed.body) ?? new URL(feed.finalUrl).host };
      }
    }
  }
  return null;
}

/** Resolve what the user typed to a feed and save it. */
export const add = action({
  args: { input: v.string() },
  handler: async (ctx, { input }): Promise<{ status: "added" | "exists" | "full"; title: string }> => {
    const identity = await ctx.auth.getUserIdentity();
    if (identity === null) throw new Error("Not signed in");
    const account = await ctx.runQuery(internal.subscriptions.userIdForClerk, {
      clerkUserId: identity.subject,
    });
    if (account === null) throw new Error("Your account isn't ready yet");
    // Fail fast before any network work; insert re-checks the limit atomically.
    if (account.full) return { status: "full", title: "" };
    const { userId } = account;
    const feed = await resolveFeed(input);
    if (feed === null) {
      throw new Error("Couldn't find a feed there. Try the publication's home page URL.");
    }
    const status = await ctx.runMutation(internal.subscriptions.insert, { userId, ...feed });
    return { status, title: feed.title };
  },
});

type SearchHit = { name: string; url: string };

/** Pull name + home URL out of Substack's search response; tolerant of shape changes. */
export function parseSearchResults(data: unknown): SearchHit[] {
  const pubs = (data as { publications?: unknown })?.publications;
  if (!Array.isArray(pubs)) return [];
  const hits: SearchHit[] = [];
  for (const p of pubs as Array<Record<string, unknown>>) {
    const name = typeof p.name === "string" ? p.name : null;
    const custom = typeof p.custom_domain === "string" && p.custom_domain ? p.custom_domain : null;
    const sub = typeof p.subdomain === "string" && p.subdomain ? p.subdomain : null;
    const host = custom ?? (sub ? `${sub}.substack.com` : null);
    if (name && host) hits.push({ name, url: `https://${host}` });
  }
  return hits.slice(0, SEARCH_LIMIT);
}

/** Search Substack publications by name. Returns [] when search is unavailable. */
export const search = action({
  args: { query: v.string() },
  handler: async (ctx, { query: q }): Promise<SearchHit[]> => {
    const identity = await ctx.auth.getUserIdentity();
    if (identity === null) throw new Error("Not signed in");
    const term = q.trim();
    if (term.length < 2) return [];
    const url = `${SUBSTACK_SEARCH_API}?query=${encodeURIComponent(term)}&page=0&limit=${SEARCH_LIMIT}`;
    try {
      const res = await fetch(url, { signal: AbortSignal.timeout(FETCH_TIMEOUT_MS) });
      if (!res.ok) return [];
      return parseSearchResults(await res.json());
    } catch {
      return [];
    }
  },
});
