import { convexTest } from "convex-test";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { api } from "./_generated/api";
import type { Id } from "./_generated/dataModel";
import { DIGEST_INTERVAL_MS, hmacHex } from "./digest";
import schema from "./schema";
import { modules } from "./test.setup";

const SECRET = "pipeline-test-secret-not-real";
const DOMAIN = "mail.test.example";

beforeEach(() => {
  vi.stubEnv("PIPELINE_SHARED_SECRET", SECRET);
  vi.stubEnv("KINDLE_SENDER_DOMAIN", DOMAIN);
});
afterEach(() => vi.unstubAllEnvs());

async function signedTargets() {
  const issuedAt = Date.now();
  return { issuedAt, signature: await hmacHex(SECRET, `digest-targets:${issuedAt}`) };
}

async function setup(overrides: Record<string, unknown> = {}) {
  const t = convexTest(schema, modules);
  const userId = await t.run(async (ctx) => {
    const id = await ctx.db.insert("users", {
      clerkUserId: "user_digest",
      email: "reader@example.com",
      proxyAddress: "reader-abcdefgh@inbox.test",
      createdAt: 0,
      senderToken: "abcdefghijklmnopqrstuvwxyz",
      kindleEmail: "reader@kindle.com",
      kindleConfirmedAt: 1,
      ...overrides,
    });
    await ctx.db.insert("subscriptions", {
      userId: id,
      feedUrl: "https://a.substack.com/feed",
      title: "A",
      addedAt: 0,
    });
    return id;
  });
  return { t, userId };
}

describe("hmacHex", () => {
  test("matches the shared cross-language vector (see tests/test_digest_run.py)", async () => {
    expect(await hmacHex(SECRET, "digest-targets:1794900000000")).toBe(
      "06551f9c89e38106dcbf0eb1af9cb5ff8b1b5d7b75db5f842fedf98e1f998595",
    );
  });
});

describe("targets", () => {
  test("rejects a bad or stale signature", async () => {
    const { t } = await setup();
    await expect(
      t.query(api.digest.targets, { issuedAt: Date.now(), signature: "bad" }),
    ).rejects.toThrow(/Unauthorized/);
    const stale = Date.now() - 10 * 60_000;
    await expect(
      t.query(api.digest.targets, {
        issuedAt: stale,
        signature: await hmacHex(SECRET, `digest-targets:${stale}`),
      }),
    ).rejects.toThrow(/Unauthorized/);
  });

  test("returns a confirmed user with feeds, their sender and Kindle", async () => {
    const { t, userId } = await setup();
    const rows = await t.query(api.digest.targets, await signedTargets());
    expect(rows).toEqual([
      {
        userId,
        kindleEmail: "reader@kindle.com",
        sender: `kindle+abcdefghijklmnopqrstuvwxyz@${DOMAIN}`,
        feedUrls: ["https://a.substack.com/feed"],
        deliveredKeys: [],
        lastDigestAt: null,
      },
    ]);
  });

  test("skips unconfirmed users and users served recently, unless they asked", async () => {
    const unconfirmed = await setup({ kindleConfirmedAt: undefined });
    expect(await unconfirmed.t.query(api.digest.targets, await signedTargets())).toEqual([]);

    const recent = await setup({ lastDigestAt: Date.now() - DIGEST_INTERVAL_MS / 2 });
    expect(await recent.t.query(api.digest.targets, await signedTargets())).toEqual([]);

    const asked = await setup({ lastDigestAt: Date.now() - 1000, digestRequestedAt: Date.now() });
    expect(await asked.t.query(api.digest.targets, await signedTargets())).toHaveLength(1);
  });
});

describe("recordDigest", () => {
  async function record(t: Awaited<ReturnType<typeof setup>>["t"], userId: Id<"users">, digestKey: string) {
    const issuedAt = Date.now();
    await t.mutation(api.digest.recordDigest, {
      userId,
      digestKey,
      posts: [
        { key: "https://a.substack.com/p/one", url: "https://a.substack.com/p/one", title: "One", publication: "A", hash: "h1" },
      ],
      issuedAt,
      signature: await hmacHex(SECRET, `record-digest:${userId}:${digestKey}:${issuedAt}`),
    });
  }

  test("stores post → revision → delivery, clears the request, and dedups next time", async () => {
    const { t, userId } = await setup({ digestRequestedAt: Date.now() });
    await record(t, userId, "digest-1");
    await record(t, userId, "digest-1"); // retried after a crash: no duplicates

    const counts = await t.run(async (ctx) => ({
      posts: (await ctx.db.query("posts").collect()).length,
      revisions: (await ctx.db.query("contentRevisions").collect()).length,
      deliveries: (await ctx.db.query("deliveries").collect()).length,
      user: await ctx.db.get(userId),
    }));
    expect(counts).toMatchObject({ posts: 1, revisions: 1, deliveries: 1 });
    expect(counts.user?.digestRequestedAt).toBeUndefined();
    expect(counts.user?.lastDigestAt).toBeGreaterThan(0);

    await t.run(async (ctx) => ctx.db.patch(userId, { digestRequestedAt: Date.now() }));
    const [row] = await t.query(api.digest.targets, await signedTargets());
    expect(row.deliveredKeys).toEqual(["https://a.substack.com/p/one"]);
  });

  test("rejects a signature made for another user", async () => {
    const { t, userId } = await setup();
    const issuedAt = Date.now();
    await expect(
      t.mutation(api.digest.recordDigest, {
        userId,
        digestKey: "d",
        posts: [],
        issuedAt,
        signature: await hmacHex(SECRET, `record-digest:someone-else:d:${issuedAt}`),
      }),
    ).rejects.toThrow(/Unauthorized/);
  });
});

describe("markNoDigest", () => {
  test("stamps lastDigestAt and clears the request", async () => {
    const { t, userId } = await setup({ digestRequestedAt: Date.now() });
    const issuedAt = Date.now();
    await t.mutation(api.digest.markNoDigest, {
      userId,
      issuedAt,
      signature: await hmacHex(SECRET, `no-digest:${userId}:${issuedAt}`),
    });
    const user = await t.run((ctx) => ctx.db.get(userId));
    expect(user?.digestRequestedAt).toBeUndefined();
    expect(user?.lastDigestAt).toBeGreaterThan(0);
  });
});
