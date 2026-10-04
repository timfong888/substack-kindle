import { convexTest } from "convex-test";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { api } from "./_generated/api";
import schema from "./schema";
import {
  INBOUND_DOMAIN,
  OTHER_SVIX_SECRET,
  TEST_SVIX_SECRET,
  modules,
  signedRequest,
} from "./test.setup";

function clerkUser(id: string, email: string, status = "verified") {
  return {
    id,
    primary_email_address_id: "idn_1",
    email_addresses: [
      { id: "idn_0", email_address: "secondary@example.com", verification: { status: "verified" } },
      { id: "idn_1", email_address: email, verification: { status } },
    ],
  };
}

async function allUsers(t: ReturnType<typeof convexTest>) {
  return await t.run(async (ctx) => await ctx.db.query("users").collect());
}

describe("POST /clerk-users-webhook", () => {
  beforeEach(() => {
    vi.stubEnv("CLERK_WEBHOOK_SECRET", TEST_SVIX_SECRET);
    vi.stubEnv("RESEND_INBOUND_DOMAIN", INBOUND_DOMAIN);
  });
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  test("user.created creates a user with a proxy address from the primary email", async () => {
    const t = convexTest(schema, modules);
    const res = await t.fetch(
      "/clerk-users-webhook",
      signedRequest({ type: "user.created", data: clerkUser("user_1", "Tim.Fong@Example.com") }),
    );
    expect(res.status).toBe(200);
    const users = await allUsers(t);
    expect(users).toHaveLength(1);
    expect(users[0].clerkUserId).toBe("user_1");
    expect(users[0].email).toBe("tim.fong@example.com");
    expect(users[0].proxyAddress).toMatch(/^timfong-[a-z2-7]{8}@testinbox\.resend\.app$/);
    expect(users[0].createdAt).toBeTypeOf("number");
  });

  test("redelivered user.created is idempotent", async () => {
    const t = convexTest(schema, modules);
    const payload = { type: "user.created", data: clerkUser("user_1", "tim@example.com") };
    await t.fetch("/clerk-users-webhook", signedRequest(payload));
    const first = await allUsers(t);
    await t.fetch("/clerk-users-webhook", signedRequest(payload));
    const second = await allUsers(t);
    expect(second).toHaveLength(1);
    expect(second[0].proxyAddress).toBe(first[0].proxyAddress);
  });

  test("user.updated changes the email but never the proxy address", async () => {
    const t = convexTest(schema, modules);
    await t.fetch(
      "/clerk-users-webhook",
      signedRequest({ type: "user.created", data: clerkUser("user_1", "tim@example.com") }),
    );
    const [before] = await allUsers(t);
    await t.fetch(
      "/clerk-users-webhook",
      signedRequest({ type: "user.updated", data: clerkUser("user_1", "newname@example.com") }),
    );
    const after = await allUsers(t);
    expect(after).toHaveLength(1);
    expect(after[0].email).toBe("newname@example.com");
    expect(after[0].proxyAddress).toBe(before.proxyAddress);
  });

  test("retries proxy-address generation on collision", async () => {
    const t = convexTest(schema, modules);
    await t.run(async (ctx) => {
      await ctx.db.insert("users", {
        clerkUserId: "user_0",
        email: "tim@elsewhere.com",
        proxyAddress: `tim-aaaaaaaa@${INBOUND_DOMAIN}`,
        createdAt: 1,
      });
    });
    // First draw collides ("aaaaaaaa"), second is free ("bbbbbbbb").
    const fill = (byte: number) => (view: ArrayBufferView<ArrayBuffer>) => {
      new Uint8Array(view.buffer, view.byteOffset, view.byteLength).fill(byte);
      return view;
    };
    const spy = vi
      .spyOn(crypto, "getRandomValues")
      .mockImplementationOnce(fill(0) as typeof crypto.getRandomValues)
      .mockImplementationOnce(fill(1) as typeof crypto.getRandomValues);
    try {
      await t.fetch(
        "/clerk-users-webhook",
        signedRequest({ type: "user.created", data: clerkUser("user_1", "tim@example.com") }),
      );
    } finally {
      spy.mockRestore();
    }
    const created = (await allUsers(t)).find((u) => u.clerkUserId === "user_1");
    expect(created?.proxyAddress).toBe(`tim-bbbbbbbb@${INBOUND_DOMAIN}`);
  });

  test("user.updated for an unseen user creates it (missed create event)", async () => {
    const t = convexTest(schema, modules);
    await t.fetch(
      "/clerk-users-webhook",
      signedRequest({ type: "user.updated", data: clerkUser("user_9", "late@example.com") }),
    );
    expect(await allUsers(t)).toHaveLength(1);
  });

  test("unverified primary email is ignored until verified", async () => {
    const t = convexTest(schema, modules);
    const res = await t.fetch(
      "/clerk-users-webhook",
      signedRequest({
        type: "user.created",
        data: clerkUser("user_1", "tim@example.com", "unverified"),
      }),
    );
    expect(res.status).toBe(200);
    expect(await allUsers(t)).toHaveLength(0);
  });

  test("user.deleted removes the user and their stored mail", async () => {
    vi.useFakeTimers();
    try {
      const t = convexTest(schema, modules);
      await t.fetch(
        "/clerk-users-webhook",
        signedRequest({ type: "user.created", data: clerkUser("user_1", "tim@example.com") }),
      );
      const [user] = await allUsers(t);
      await t.run(async (ctx) => {
        await ctx.db.insert("receivedEmails", {
          userId: user._id,
          resendEmailId: "em_1",
          from: "a@b.c",
          subject: "s",
          receivedAt: 1,
          bodyStatus: "stored",
          kind: "newsletter",
        });
      });
      const res = await t.fetch(
        "/clerk-users-webhook",
        signedRequest({ type: "user.deleted", data: { id: "user_1", deleted: true } }),
      );
      expect(res.status).toBe(200);
      await t.finishAllScheduledFunctions(vi.runAllTimers);
      expect(await allUsers(t)).toHaveLength(0);
      const emails = await t.run(async (ctx) => await ctx.db.query("receivedEmails").collect());
      expect(emails).toHaveLength(0);
    } finally {
      vi.useRealTimers();
    }
  });

  test("user.deleted for an unknown user is a no-op 200", async () => {
    const t = convexTest(schema, modules);
    const res = await t.fetch(
      "/clerk-users-webhook",
      signedRequest({ type: "user.deleted", data: { id: "user_x", deleted: true } }),
    );
    expect(res.status).toBe(200);
  });

  test("bad signature returns 400 and writes nothing", async () => {
    const t = convexTest(schema, modules);
    const res = await t.fetch(
      "/clerk-users-webhook",
      signedRequest(
        { type: "user.created", data: clerkUser("user_1", "tim@example.com") },
        OTHER_SVIX_SECRET,
      ),
    );
    expect(res.status).toBe(400);
    expect(await allUsers(t)).toHaveLength(0);
  });

  test("missing svix headers return 400", async () => {
    const t = convexTest(schema, modules);
    const res = await t.fetch("/clerk-users-webhook", {
      method: "POST",
      body: JSON.stringify({ type: "user.created", data: clerkUser("u", "a@b.c") }),
    });
    expect(res.status).toBe(400);
  });

  test("unset webhook secret fails closed with 500", async () => {
    vi.stubEnv("CLERK_WEBHOOK_SECRET", "");
    const t = convexTest(schema, modules);
    const res = await t.fetch(
      "/clerk-users-webhook",
      signedRequest({ type: "user.created", data: clerkUser("user_1", "tim@example.com") }),
    );
    expect(res.status).toBe(500);
    expect(await allUsers(t)).toHaveLength(0);
  });
});

describe("users.current", () => {
  beforeEach(() => vi.stubEnv("RESEND_INBOUND_DOMAIN", INBOUND_DOMAIN));
  afterEach(() => vi.unstubAllEnvs());

  test("returns null when signed out", async () => {
    const t = convexTest(schema, modules);
    expect(await t.query(api.users.current, {})).toBeNull();
  });

  test("returns null when the webhook has not created the row yet", async () => {
    const t = convexTest(schema, modules);
    expect(await t.withIdentity({ subject: "user_1" }).query(api.users.current, {})).toBeNull();
  });

  test("returns email and proxy address for the signed-in user only", async () => {
    const t = convexTest(schema, modules);
    await t.run(async (ctx) => {
      await ctx.db.insert("users", {
        clerkUserId: "user_1",
        email: "tim@example.com",
        proxyAddress: "tim-aaaaaaaa@testinbox.resend.app",
        createdAt: 1,
      });
      await ctx.db.insert("users", {
        clerkUserId: "user_2",
        email: "other@example.com",
        proxyAddress: "other-bbbbbbbb@testinbox.resend.app",
        createdAt: 1,
      });
    });
    expect(await t.withIdentity({ subject: "user_1" }).query(api.users.current, {})).toEqual({
      email: "tim@example.com",
      proxyAddress: "tim-aaaaaaaa@testinbox.resend.app",
    });
  });
});
