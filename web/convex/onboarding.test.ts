import { convexTest } from "convex-test";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { api, internal } from "./_generated/api";
import { TEST_SEND_INTERVAL_MS } from "./account";
import schema from "./schema";
import { parseSearchResults } from "./subscriptions";
import { modules } from "./test.setup";

const CLERK_ID = "user_onboarding_test";
const SENDER_DOMAIN = "mail.test.example";

async function setup() {
  const t = convexTest(schema, modules);
  await t.run(async (ctx) => {
    await ctx.db.insert("users", {
      clerkUserId: CLERK_ID,
      email: "reader@example.com",
      proxyAddress: "reader-abcdefgh@inbox.test",
      createdAt: 0,
    });
  });
  return { t, as: t.withIdentity({ subject: CLERK_ID }) };
}

function rssResponse(title: string, url: string): Response {
  const res = new Response(`<?xml version="1.0"?><rss><channel><title>${title}</title></channel></rss>`, {
    status: 200,
  });
  Object.defineProperty(res, "url", { value: url });
  return res;
}

beforeEach(() => {
  vi.stubEnv("KINDLE_SENDER_DOMAIN", SENDER_DOMAIN);
  vi.stubEnv("RESEND_API_KEY", "re_test_not_real");
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("Kindle step", () => {
  test("ensureSender issues one stable per-user sender address", async () => {
    const { as } = await setup();
    expect((await as.query(api.account.state, {}))?.senderAddress).toBeNull();
    await as.mutation(api.account.ensureSender, {});
    const first = (await as.query(api.account.state, {}))?.senderAddress;
    expect(first).toMatch(new RegExp(`^kindle\\+[a-z2-7]{26}@${SENDER_DOMAIN.replace(/\./g, "\\.")}$`));
    await as.mutation(api.account.ensureSender, {});
    expect((await as.query(api.account.state, {}))?.senderAddress).toBe(first);
  });

  test("setKindleEmail validates and normalizes; a new address resets the test", async () => {
    const { t, as } = await setup();
    await expect(
      as.mutation(api.account.setKindleEmail, { kindleEmail: "me@gmail.com" }),
    ).rejects.toThrow(/kindle\.com/);
    await as.mutation(api.account.setKindleEmail, { kindleEmail: " Me@Kindle.com ", amazonStore: "co.uk" });
    let s = await as.query(api.account.state, {});
    expect(s?.kindleEmail).toBe("me@kindle.com");
    expect(s?.amazonUrl).toContain("amazon.co.uk");

    await t.run(async (ctx) => {
      const user = await ctx.db.query("users").first();
      await ctx.db.patch(user!._id, { testSentAt: 1, kindleConfirmedAt: 2 });
    });
    await as.mutation(api.account.setKindleEmail, { kindleEmail: "me@kindle.com" });
    s = await as.query(api.account.state, {});
    expect(s?.kindleConfirmedAt).toBe(2); // same address: kept
    await as.mutation(api.account.setKindleEmail, { kindleEmail: "other@kindle.com" });
    s = await as.query(api.account.state, {});
    expect(s?.kindleConfirmedAt).toBeNull();
    expect(s?.testSentAt).toBeNull();
  });

  test("sendTest posts from the sender address to the Kindle, then rate-limits", async () => {
    const { as } = await setup();
    await as.mutation(api.account.ensureSender, {});
    await as.mutation(api.account.setKindleEmail, { kindleEmail: "me@kindle.com" });
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ id: "em_1" }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await as.action(api.account.sendTest, {});
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    const body = JSON.parse(String(init.body));
    const state = await as.query(api.account.state, {});
    expect(body.from).toBe(state?.senderAddress);
    expect(body.to).toEqual(["me@kindle.com"]);
    expect(body.attachments[0].filename).toMatch(/\.html$/);
    expect(state?.testSentAt).not.toBeNull();

    await expect(as.action(api.account.sendTest, {})).rejects.toThrow(/wait/);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(TEST_SEND_INTERVAL_MS).toBeGreaterThanOrEqual(60_000);
  });

  test("sendTest fails cleanly without a Kindle address or when Resend refuses", async () => {
    const { as } = await setup();
    await as.mutation(api.account.ensureSender, {});
    await expect(as.action(api.account.sendTest, {})).rejects.toThrow(/Kindle address/);
    await as.mutation(api.account.setKindleEmail, { kindleEmail: "me@kindle.com" });
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 422 })));
    vi.spyOn(console, "error").mockImplementation(() => {});
    await expect(as.action(api.account.sendTest, {})).rejects.toThrow(/could not be sent/);
    expect((await as.query(api.account.state, {}))?.testSentAt).toBeNull();
  });

  test("confirmKindle needs a test; requestDigest needs a confirmed Kindle and a subscription", async () => {
    const { t, as } = await setup();
    await expect(as.mutation(api.account.confirmKindle, {})).rejects.toThrow(/test/);
    await expect(as.mutation(api.account.requestDigest, {})).rejects.toThrow(/Kindle/);
    const userId = await t.run(async (ctx) => {
      const user = await ctx.db.query("users").first();
      await ctx.db.patch(user!._id, { testSentAt: Date.now() });
      return user!._id;
    });
    await as.mutation(api.account.confirmKindle, {});
    await expect(as.mutation(api.account.requestDigest, {})).rejects.toThrow(/publication/);
    await t.run(async (ctx) => {
      await ctx.db.insert("subscriptions", { userId, feedUrl: "https://a.substack.com/feed", title: "A", addedAt: 0 });
    });
    await as.mutation(api.account.requestDigest, {});
    expect((await as.query(api.account.state, {}))?.digestRequestedAt).not.toBeNull();
  });

  test("signed-out callers get null state and cannot mutate", async () => {
    const { t } = await setup();
    expect(await t.query(api.account.state, {})).toBeNull();
    await expect(t.mutation(api.account.ensureSender, {})).rejects.toThrow(/signed in/);
  });
});

describe("subscriptions", () => {
  test("add resolves a Substack handle to its feed, stores it once, and lists it", async () => {
    const { as } = await setup();
    const fetchMock = vi.fn(async (url: string) =>
      rssResponse("ByteByteGo Newsletter", url),
    );
    vi.stubGlobal("fetch", fetchMock);

    const first = await as.action(api.subscriptions.add, { input: "bytebytego" });
    expect(first).toEqual({ status: "added", title: "ByteByteGo Newsletter" });
    expect(fetchMock.mock.calls[0][0]).toBe("https://bytebytego.substack.com/feed");
    expect(await as.action(api.subscriptions.add, { input: "bytebytego" })).toMatchObject({
      status: "exists",
    });
    expect(await as.query(api.subscriptions.list, {})).toEqual([
      expect.objectContaining({ title: "ByteByteGo Newsletter", feedUrl: "https://bytebytego.substack.com/feed" }),
    ]);
  });

  test("add follows an HTML page's alternate feed link", async () => {
    const { as } = await setup();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url === "https://example.org/feed") return new Response("nope", { status: 404 });
        if (url === "https://example.org/") {
          const res = new Response('<html><link rel="alternate" type="application/rss+xml" href="/rss.xml">');
          Object.defineProperty(res, "url", { value: url });
          return res;
        }
        return rssResponse("Example Blog", url);
      }),
    );
    const result = await as.action(api.subscriptions.add, { input: "example.org" });
    expect(result.title).toBe("Example Blog");
    expect((await as.query(api.subscriptions.list, {}))[0].feedUrl).toBe("https://example.org/rss.xml");
  });

  test("a redirect to a private address is refused before it is requested", async () => {
    const { as } = await setup();
    const fetchMock = vi.fn(async (url: string) => {
      if (url === "https://evil.substack.com/feed") {
        return new Response(null, {
          status: 302,
          headers: { location: "http://169.254.169.254/latest/meta-data" },
        });
      }
      return rssResponse("Should not be fetched", url);
    });
    vi.stubGlobal("fetch", fetchMock);
    await expect(as.action(api.subscriptions.add, { input: "evil" })).rejects.toThrow(/feed/);
    expect(fetchMock.mock.calls.map((c) => c[0])).toEqual(["https://evil.substack.com/feed"]);
  });

  test("a redirect to a public feed is followed", async () => {
    const { as } = await setup();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        url === "https://moved.substack.com/feed"
          ? new Response(null, { status: 301, headers: { location: "https://www.moved.example/feed" } })
          : rssResponse("Moved", url),
      ),
    );
    await as.action(api.subscriptions.add, { input: "moved" });
    expect((await as.query(api.subscriptions.list, {}))[0].feedUrl).toBe("https://www.moved.example/feed");
  });

  test("add reports when no feed can be found", async () => {
    const { as } = await setup();
    vi.stubGlobal("fetch", vi.fn(async () => new Response("<html></html>", { status: 200 })));
    await expect(as.action(api.subscriptions.add, { input: "example.org" })).rejects.toThrow(/feed/);
  });

  test("remove only deletes the caller's own rows", async () => {
    const { t, as } = await setup();
    const otherRow = await t.run(async (ctx) => {
      const other = await ctx.db.insert("users", {
        clerkUserId: "someone_else",
        email: "x@example.com",
        proxyAddress: "x-abcdefgh@inbox.test",
        createdAt: 0,
      });
      return await ctx.db.insert("subscriptions", { userId: other, feedUrl: "https://b/feed", title: "B", addedAt: 0 });
    });
    await as.mutation(api.subscriptions.remove, { id: otherRow });
    expect(await t.run((ctx) => ctx.db.get(otherRow))).not.toBeNull();
  });

  test("parseSearchResults maps Substack search hits to name + home URL", () => {
    expect(
      parseSearchResults({
        publications: [
          { name: "Lenny's Newsletter", subdomain: "lenny", custom_domain: "www.lennysnewsletter.com" },
          { name: "ByteByteGo", subdomain: "bytebytego", custom_domain: null },
          { name: "", subdomain: "x" },
        ],
      }),
    ).toEqual([
      { name: "Lenny's Newsletter", url: "https://www.lennysnewsletter.com" },
      { name: "ByteByteGo", url: "https://bytebytego.substack.com" },
    ]);
    expect(parseSearchResults({ unexpected: true })).toEqual([]);
  });
});

describe("subscription limit", () => {
  test("a full list is reported without fetching anything", async () => {
    const { t, as } = await setup();
    await t.run(async (ctx) => {
      const user = await ctx.db.query("users").first();
      const { MAX_SUBSCRIPTIONS } = await import("./subscriptions");
      for (let i = 0; i < MAX_SUBSCRIPTIONS; i++) {
        await ctx.db.insert("subscriptions", { userId: user!._id, feedUrl: `https://p${i}.example/feed`, title: `P${i}`, addedAt: 0 });
      }
    });
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    expect(await as.action(api.subscriptions.add, { input: "bytebytego" })).toMatchObject({ status: "full" });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("account deletion", () => {
  test("purges subscriptions and deliveries, and late inserts are refused", async () => {
    vi.useFakeTimers();
    const { t } = await setup();
    const userId = await t.run(async (ctx) => {
      const user = await ctx.db.query("users").first();
      const post = await ctx.db.insert("posts", { key: "k", title: "T", publication: "P", firstSeenAt: 0 });
      const revisionId = await ctx.db.insert("contentRevisions", { postId: post, hash: "h", createdAt: 0 });
      await ctx.db.insert("subscriptions", { userId: user!._id, feedUrl: "https://a.example/feed?token=x", title: "A", addedAt: 0 });
      await ctx.db.insert("deliveries", { userId: user!._id, revisionId, postKey: "k", digestKey: "d", status: "sent", sentAt: 0 });
      return user!._id;
    });
    await t.mutation(internal.users.deleteFromClerk, { clerkUserId: CLERK_ID });
    await t.finishAllScheduledFunctions(vi.runAllTimers);
    const left = await t.run(async (ctx) => ({
      subs: (await ctx.db.query("subscriptions").collect()).length,
      deliveries: (await ctx.db.query("deliveries").collect()).length,
    }));
    expect(left).toEqual({ subs: 0, deliveries: 0 });
    expect(
      await t.mutation(internal.subscriptions.insert, { userId, feedUrl: "https://b.example/feed", title: "B" }),
    ).toBe("full");
    expect(await t.run(async (ctx) => (await ctx.db.query("subscriptions").collect()).length)).toBe(0);
  });
});
