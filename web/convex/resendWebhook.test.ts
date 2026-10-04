import { convexTest } from "convex-test";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { api, internal } from "./_generated/api";
import {
  GOOGLE_FORWARDING_SENDER,
  MAX_BODY_BYTES,
  classifyKind,
  collectRecipients,
  guardBodies,
} from "./inbound";
import schema from "./schema";
import {
  INBOUND_DOMAIN,
  OTHER_SVIX_SECRET,
  TEST_SVIX_SECRET,
  modules,
  signedRequest,
} from "./test.setup";

const PROXY = `tim-abcdefgh@${INBOUND_DOMAIN}`;
const TEST_RESEND_KEY = "test-resend-key-not-real";

function received(overrides: Record<string, unknown> = {}) {
  return {
    type: "email.received",
    created_at: "2026-10-01T12:00:00.000Z",
    data: {
      email_id: "em_1",
      created_at: "2026-10-01T12:00:00.000Z",
      from: "Lenny <lenny@substack.com>",
      to: [PROXY],
      subject: "Weekly issue",
      message_id: "<abc@substack.com>",
      ...overrides,
    },
  };
}

function resendBody(overrides: Record<string, unknown> = {}) {
  return {
    object: "email",
    id: "em_1",
    from: "Lenny <lenny@substack.com>",
    to: [PROXY],
    subject: "Weekly issue",
    message_id: "<abc@substack.com>",
    created_at: "2026-10-01T12:00:01.000Z",
    html: "<p>Hello</p>",
    text: "Hello",
    ...overrides,
  };
}

function setup() {
  const t = convexTest(schema, modules);
  return t;
}

async function seedUser(t: ReturnType<typeof convexTest>, proxyAddress = PROXY) {
  return await t.run(async (ctx) =>
    ctx.db.insert("users", {
      clerkUserId: "user_1",
      email: "tim@example.com",
      proxyAddress,
      createdAt: 1,
    }),
  );
}

async function emails(t: ReturnType<typeof convexTest>) {
  return await t.run(async (ctx) => await ctx.db.query("receivedEmails").collect());
}

describe("inbound pure helpers", () => {
  test("collectRecipients merges to + received_for, unwraps names, lowercases, dedupes", () => {
    expect(
      collectRecipients(["Tim <TIM-X@Example.com>", "a@b.c"], ["tim-x@example.com", "z@y.x"]),
    ).toEqual(["tim-x@example.com", "a@b.c", "z@y.x"]);
    expect(collectRecipients(undefined, "Solo@X.io")).toEqual(["solo@x.io"]);
    expect(collectRecipients(null, [42, ""])).toEqual([]);
  });

  test("classifyKind flags Google's forwarding confirmation sender only", () => {
    expect(classifyKind(`Gmail Team <${GOOGLE_FORWARDING_SENDER}>`)).toBe("confirmation");
    expect(classifyKind("FORWARDING-NOREPLY@GOOGLE.COM")).toBe("confirmation");
    expect(classifyKind("noreply@google.com")).toBe("newsletter");
    expect(classifyKind("lenny@substack.com")).toBe("newsletter");
  });

  test("guardBodies keeps small bodies untouched", () => {
    expect(guardBodies("<p>x</p>", "x")).toEqual({
      html: "<p>x</p>",
      text: "x",
      htmlTruncated: false,
      textTruncated: false,
    });
  });

  test("guardBodies drops oversize html (bytes, not chars) and keeps text that fits", () => {
    const html = "é".repeat(MAX_BODY_BYTES / 2 + 1); // 2 bytes each -> over the limit
    expect(guardBodies(html, "short")).toEqual({
      html: undefined,
      text: "short",
      htmlTruncated: true,
      textTruncated: false,
    });
  });

  test("guardBodies drops text when html + text together exceed the limit", () => {
    const half = "a".repeat(MAX_BODY_BYTES / 2 + 10);
    const r = guardBodies(half, half);
    expect(r.html).toBe(half);
    expect(r.text).toBeUndefined();
    expect(r.textTruncated).toBe(true);
  });
});

describe("POST /resend-inbound", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubEnv("RESEND_WEBHOOK_SECRET", TEST_SVIX_SECRET);
    vi.stubEnv("RESEND_API_KEY", TEST_RESEND_KEY);
    fetchMock = vi.fn(async () => Response.json(resendBody()));
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  test("bad signature returns 400 and stores nothing", async () => {
    const t = setup();
    await seedUser(t);
    const res = await t.fetch("/resend-inbound", signedRequest(received(), OTHER_SVIX_SECRET));
    expect(res.status).toBe(400);
    expect(await emails(t)).toHaveLength(0);
  });

  test("tampered body fails verification", async () => {
    const t = setup();
    await seedUser(t);
    const init = signedRequest(received());
    const res = await t.fetch("/resend-inbound", {
      ...init,
      body: JSON.stringify(received({ to: ["attacker@evil.com"] })),
    });
    expect(res.status).toBe(400);
  });

  test("unset secret fails closed with 500", async () => {
    vi.stubEnv("RESEND_WEBHOOK_SECRET", "");
    const t = setup();
    const res = await t.fetch("/resend-inbound", signedRequest(received()));
    expect(res.status).toBe(500);
  });

  test("unknown recipient: 200, nothing stored, no Resend fetch", async () => {
    const t = setup();
    await seedUser(t);
    const res = await t.fetch(
      "/resend-inbound",
      signedRequest(received({ to: [`nobody-zzzzzzzz@${INBOUND_DOMAIN}`] })),
    );
    expect(res.status).toBe(200);
    await t.finishAllScheduledFunctions(vi.runAllTimers);
    expect(await emails(t)).toHaveLength(0);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  test("non email.received events are acknowledged and ignored", async () => {
    const t = setup();
    await seedUser(t);
    const res = await t.fetch(
      "/resend-inbound",
      signedRequest({ ...received(), type: "email.delivered" }),
    );
    expect(res.status).toBe(200);
    expect(await emails(t)).toHaveLength(0);
  });

  test("known recipient: fetches the body from Resend and stores it", async () => {
    const t = setup();
    const userId = await seedUser(t);
    const res = await t.fetch(
      "/resend-inbound",
      signedRequest(received({ to: [`Tim <${PROXY.toUpperCase()}>`] })),
    );
    expect(res.status).toBe(200);
    await t.finishAllScheduledFunctions(vi.runAllTimers);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("https://api.resend.com/emails/receiving/em_1");
    expect((init.headers as Record<string, string>).Authorization).toBe(
      `Bearer ${TEST_RESEND_KEY}`,
    );

    const rows = await emails(t);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({
      userId,
      resendEmailId: "em_1",
      messageId: "<abc@substack.com>",
      from: "Lenny <lenny@substack.com>",
      subject: "Weekly issue",
      receivedAt: Date.parse("2026-10-01T12:00:01.000Z"),
      html: "<p>Hello</p>",
      text: "Hello",
      bodyStatus: "stored",
      kind: "newsletter",
    });
  });

  test("matches on received_for when `to` is the original list address", async () => {
    const t = setup();
    await seedUser(t);
    await t.fetch(
      "/resend-inbound",
      signedRequest(received({ to: ["list@newsletter.com"], received_for: [PROXY] })),
    );
    await t.finishAllScheduledFunctions(vi.runAllTimers);
    expect(await emails(t)).toHaveLength(1);
  });

  test("duplicate email_id is ignored (one row, one fetch)", async () => {
    const t = setup();
    await seedUser(t);
    await t.fetch("/resend-inbound", signedRequest(received()));
    const again = await t.fetch("/resend-inbound", signedRequest(received()));
    expect(again.status).toBe(200);
    await t.finishAllScheduledFunctions(vi.runAllTimers);
    expect(await emails(t)).toHaveLength(1);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  test("Google forwarding confirmation is classified and surfaced to the user", async () => {
    const confirmationText =
      "Confirmation code: 123456789\n\ntim@gmail.com has requested to automatically forward mail";
    fetchMock.mockImplementation(async () =>
      Response.json(
        resendBody({
          from: `Gmail Team <${GOOGLE_FORWARDING_SENDER}>`,
          subject: "(#123456789) Gmail Forwarding Confirmation",
          html: undefined,
          text: confirmationText,
        }),
      ),
    );
    const t = setup();
    await seedUser(t);
    await t.fetch(
      "/resend-inbound",
      signedRequest(received({ from: `Gmail Team <${GOOGLE_FORWARDING_SENDER}>` })),
    );
    await t.finishAllScheduledFunctions(vi.runAllTimers);
    const [row] = await emails(t);
    expect(row.kind).toBe("confirmation");

    const confirmations = await t
      .withIdentity({ subject: "user_1" })
      .query(api.users.recentConfirmations, {});
    expect(confirmations).toHaveLength(1);
    expect(confirmations[0].code).toBe("123456789");
    expect(await t.withIdentity({ subject: "other" }).query(api.users.recentConfirmations, {}))
      .toEqual([]);
  });

  test("oversize html is skipped with a truncated flag instead of throwing", async () => {
    fetchMock.mockImplementation(async () =>
      Response.json(resendBody({ html: "x".repeat(MAX_BODY_BYTES + 1), text: "plain" })),
    );
    const t = setup();
    await seedUser(t);
    await t.fetch("/resend-inbound", signedRequest(received()));
    await t.finishAllScheduledFunctions(vi.runAllTimers);
    const [row] = await emails(t);
    expect(row.bodyStatus).toBe("stored");
    expect(row.html).toBeUndefined();
    expect(row.htmlTruncated).toBe(true);
    expect(row.text).toBe("plain");
  });
});

describe("inbound.fetchBody", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  async function pendingRow(t: ReturnType<typeof convexTest>) {
    const userId = await seedUser(t);
    return await t.run(async (ctx) =>
      ctx.db.insert("receivedEmails", {
        userId,
        resendEmailId: "em_1",
        from: "a@b.c",
        subject: "s",
        receivedAt: 1,
        bodyStatus: "pending",
        kind: "newsletter",
      }),
    );
  }

  test("body far beyond Convex's ~8 MB argument limit is guarded in the action, then stored", async () => {
    vi.stubEnv("RESEND_API_KEY", TEST_RESEND_KEY);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json(resendBody({ html: "x".repeat(9_000_000), text: "plain" }))),
    );
    const t = convexTest(schema, modules);
    const rowId = await pendingRow(t);
    await t.action(internal.inbound.fetchBody, { rowId, resendEmailId: "em_1" });
    const row = await t.run(async (ctx) => ctx.db.get(rowId));
    expect(row?.bodyStatus).toBe("stored");
    expect(row?.html).toBeUndefined();
    expect(row?.htmlTruncated).toBe(true);
    expect(row?.text).toBe("plain");
  });

  test("storeBody keeps truncation flags computed upstream by the action", async () => {
    const t = convexTest(schema, modules);
    const rowId = await pendingRow(t);
    await t.mutation(internal.inbound.storeBody, {
      rowId,
      text: "plain",
      htmlTruncated: true,
    });
    const row = await t.run(async (ctx) => ctx.db.get(rowId));
    expect(row?.htmlTruncated).toBe(true);
    expect(row?.textTruncated).toBeUndefined();
  });

  test("non-2xx from Resend throws (visible in scheduler logs) and leaves row pending", async () => {
    vi.stubEnv("RESEND_API_KEY", TEST_RESEND_KEY);
    vi.stubGlobal("fetch", vi.fn(async () => new Response("nope", { status: 404 })));
    const t = setup();
    const rowId = await pendingRow(t);
    await expect(
      t.action(internal.inbound.fetchBody, { rowId, resendEmailId: "em_1" }),
    ).rejects.toThrow(/HTTP 404/);
    const row = await t.run(async (ctx) => ctx.db.get(rowId));
    expect(row?.bodyStatus).toBe("pending");
  });

  test("missing RESEND_API_KEY throws before any request", async () => {
    vi.stubEnv("RESEND_API_KEY", "");
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const t = setup();
    const rowId = await pendingRow(t);
    await expect(
      t.action(internal.inbound.fetchBody, { rowId, resendEmailId: "em_1" }),
    ).rejects.toThrow(/RESEND_API_KEY/);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
