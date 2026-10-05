import { httpRouter } from "convex/server";
import { Webhook } from "svix";
import { internal } from "./_generated/api";
import { httpAction } from "./_generated/server";
import { collectRecipients } from "./inbound";

const http = httpRouter();

/**
 * Verify a Svix-signed webhook (Clerk and Resend both use Svix). Returns the
 * parsed payload, or a Response to send back: 400 on a bad signature, 500 when
 * the secret is not configured.
 */
async function verifySvix(
  request: Request,
  secret: string | undefined,
): Promise<{ payload: unknown } | { response: Response }> {
  if (!secret) return { response: new Response("Webhook secret not configured", { status: 500 }) };
  const body = await request.text(); // raw body: signature covers exact bytes
  const headers = {
    "svix-id": request.headers.get("svix-id") ?? "",
    "svix-timestamp": request.headers.get("svix-timestamp") ?? "",
    "svix-signature": request.headers.get("svix-signature") ?? "",
  };
  try {
    new Webhook(secret).verify(body, headers);
  } catch {
    return { response: new Response("Invalid signature", { status: 400 }) };
  }
  try {
    return { payload: JSON.parse(body) };
  } catch {
    return { response: new Response("Invalid JSON", { status: 400 }) };
  }
}

type ClerkEmail = { id: string; email_address: string; verification?: { status?: string } | null };
type ClerkUserData = {
  id: string;
  primary_email_address_id?: string | null;
  email_addresses?: ClerkEmail[];
};

/** Primary email address, only when Clerk reports it verified. */
export function primaryVerifiedEmail(data: ClerkUserData): string | null {
  const primary = data.email_addresses?.find((e) => e.id === data.primary_email_address_id);
  if (!primary || primary.verification?.status !== "verified") return null;
  return primary.email_address.trim().toLowerCase();
}

http.route({
  path: "/clerk-users-webhook",
  method: "POST",
  handler: httpAction(async (ctx, request) => {
    const verified = await verifySvix(request, process.env.CLERK_WEBHOOK_SECRET);
    if ("response" in verified) return verified.response;
    const event = verified.payload as { type?: string; data?: ClerkUserData };

    switch (event.type) {
      case "user.created":
      case "user.updated": {
        if (!event.data?.id) return new Response("Missing user id", { status: 400 });
        const email = primaryVerifiedEmail(event.data);
        if (email === null) break; // wait for a later update once verified
        await ctx.runMutation(internal.users.upsertFromClerk, {
          clerkUserId: event.data.id,
          email,
        });
        break;
      }
      case "user.deleted": {
        if (event.data?.id) {
          await ctx.runMutation(internal.users.deleteFromClerk, { clerkUserId: event.data.id });
        }
        break;
      }
      default:
        break; // ignore other events
    }
    return new Response(null, { status: 200 });
  }),
});

type ResendReceivedData = {
  email_id?: string;
  created_at?: string;
  from?: string;
  to?: unknown;
  received_for?: unknown;
  subject?: string;
  message_id?: string;
};

http.route({
  path: "/resend-inbound",
  method: "POST",
  handler: httpAction(async (ctx, request) => {
    const verified = await verifySvix(request, process.env.RESEND_WEBHOOK_SECRET);
    if ("response" in verified) return verified.response;
    const event = verified.payload as { type?: string; data?: ResendReceivedData };

    if (event.type !== "email.received") return new Response(null, { status: 200 });
    const data = event.data ?? {};
    if (!data.email_id) return new Response("Missing email_id", { status: 400 });

    // Unknown recipients and duplicates both get a plain 200: never reveal
    // which proxy addresses exist, and stop Resend from retrying.
    await ctx.runMutation(internal.inbound.claim, {
      resendEmailId: data.email_id,
      recipients: collectRecipients(data.to, data.received_for),
      from: data.from ?? "",
      subject: data.subject ?? "",
      messageId: data.message_id,
      createdAt: data.created_at,
    });
    return new Response(null, { status: 200 });
  }),
});

export default http;
