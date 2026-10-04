import { convexTest } from "convex-test";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { api } from "./_generated/api";
import type { Id } from "./_generated/dataModel";
import { constantTimeEqual } from "./pipeline";
import schema from "./schema";
import { INBOUND_DOMAIN, modules } from "./test.setup";

const SECRET = "pipeline-test-secret-not-real";
const PROXY = `tim-abcdefgh@${INBOUND_DOMAIN}`;
const DAY = 86_400_000;
const START = Date.parse("2026-10-01T00:00:00Z");
const END = START + DAY;

describe("constantTimeEqual", () => {
  test("equal and unequal strings", () => {
    expect(constantTimeEqual("abc", "abc")).toBe(true);
    expect(constantTimeEqual("abc", "abd")).toBe(false);
    expect(constantTimeEqual("abc", "abcd")).toBe(false);
    expect(constantTimeEqual("", "x")).toBe(false);
    expect(constantTimeEqual("", "")).toBe(true);
  });
});

describe("pipeline.listInboundForWindow", () => {
  beforeEach(() => vi.stubEnv("PIPELINE_SHARED_SECRET", SECRET));
  afterEach(() => vi.unstubAllEnvs());

  async function seed(t: ReturnType<typeof convexTest>) {
    await t.run(async (ctx) => {
      const userId = await ctx.db.insert("users", {
        clerkUserId: "user_1",
        email: "tim@example.com",
        proxyAddress: PROXY,
        createdAt: 1,
      });
      const other = await ctx.db.insert("users", {
        clerkUserId: "user_2",
        email: "o@example.com",
        proxyAddress: `o-zzzzzzzz@${INBOUND_DOMAIN}`,
        createdAt: 1,
      });
      const row = (
        id: string,
        receivedAt: number,
        extra: Partial<{
          userId: Id<"users">;
          kind: "newsletter" | "confirmation";
          bodyStatus: "pending" | "stored";
        }> = {},
      ) =>
        ctx.db.insert("receivedEmails", {
          userId,
          resendEmailId: id,
          messageId: `<${id}@x>`,
          from: "Lenny <lenny@substack.com>",
          subject: `Issue ${id}`,
          receivedAt,
          html: `<p>${id}</p>`,
          text: id,
          bodyStatus: "stored",
          kind: "newsletter",
          ...extra,
        });
      await row("before", START - 1);
      await row("at-start", START);
      await row("middle", START + DAY / 2);
      await row("at-end", END); // end is exclusive
      await row("confirmation", START + 10, { kind: "confirmation" });
      await row("pending", START + 20, { bodyStatus: "pending" });
      await row("other-user", START + 30, { userId: other });
    });
  }

  test("returns only this user's stored newsletters in [start, end), oldest first", async () => {
    const t = convexTest(schema, modules);
    await seed(t);
    const rows = await t.query(api.pipeline.listInboundForWindow, {
      secret: SECRET,
      proxyAddress: PROXY.toUpperCase(),
      start: START,
      end: END,
    });
    expect(rows.map((r) => r.resendEmailId)).toEqual(["at-start", "middle"]);
    expect(rows[0]).toEqual({
      resendEmailId: "at-start",
      messageId: "<at-start@x>",
      from: "Lenny <lenny@substack.com>",
      subject: "Issue at-start",
      receivedAt: START,
      html: "<p>at-start</p>",
      text: "at-start",
      htmlTruncated: false,
    });
  });

  test("unknown proxy address returns an empty list", async () => {
    const t = convexTest(schema, modules);
    await seed(t);
    const rows = await t.query(api.pipeline.listInboundForWindow, {
      secret: SECRET,
      proxyAddress: `nobody@${INBOUND_DOMAIN}`,
      start: START,
      end: END,
    });
    expect(rows).toEqual([]);
  });

  test("wrong secret throws", async () => {
    const t = convexTest(schema, modules);
    await seed(t);
    await expect(
      t.query(api.pipeline.listInboundForWindow, {
        secret: "wrong",
        proxyAddress: PROXY,
        start: START,
        end: END,
      }),
    ).rejects.toThrow(/Unauthorized/);
  });

  test("fails closed when PIPELINE_SHARED_SECRET is unset, even for an empty secret", async () => {
    vi.stubEnv("PIPELINE_SHARED_SECRET", "");
    const t = convexTest(schema, modules);
    await expect(
      t.query(api.pipeline.listInboundForWindow, {
        secret: "",
        proxyAddress: PROXY,
        start: START,
        end: END,
      }),
    ).rejects.toThrow(/Unauthorized/);
  });
});
