import { defineSchema, defineTable } from "convex/server";
import { v } from "convex/values";

export const emailKind = v.union(v.literal("newsletter"), v.literal("confirmation"));

export default defineSchema({
  users: defineTable({
    clerkUserId: v.string(),
    email: v.string(),
    // Issued once on first creation and never changed afterwards.
    proxyAddress: v.string(),
    createdAt: v.number(),
  })
    .index("byClerkUserId", ["clerkUserId"])
    .index("byProxyAddress", ["proxyAddress"]),

  receivedEmails: defineTable({
    userId: v.id("users"),
    resendEmailId: v.string(),
    messageId: v.optional(v.string()),
    from: v.string(),
    subject: v.string(),
    // Epoch milliseconds (Resend `created_at`).
    receivedAt: v.number(),
    // Bodies are absent until the fetch action stores them ("pending"), and are
    // dropped (with the matching *Truncated flag) when they would push the
    // document past Convex's ~1 MiB limit.
    html: v.optional(v.string()),
    text: v.optional(v.string()),
    htmlTruncated: v.optional(v.boolean()),
    textTruncated: v.optional(v.boolean()),
    bodyStatus: v.union(v.literal("pending"), v.literal("stored")),
    kind: emailKind,
  })
    .index("byResendEmailId", ["resendEmailId"])
    .index("byUserAndReceivedAt", ["userId", "receivedAt"]),
});
