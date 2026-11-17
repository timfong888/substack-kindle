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
    // Kindle step. The sender token is issued once (rotation is a later ticket);
    // the address the user approves on Amazon is kindle+<token>@KINDLE_SENDER_DOMAIN.
    senderToken: v.optional(v.string()),
    kindleEmail: v.optional(v.string()),
    amazonStore: v.optional(v.string()),
    testSentAt: v.optional(v.number()),
    // Set when the user taps "It arrived"; cleared when the Kindle address changes.
    kindleConfirmedAt: v.optional(v.number()),
    // "Send my first digest": the digest runner picks this up and clears it.
    digestRequestedAt: v.optional(v.number()),
    lastDigestAt: v.optional(v.number()),
  })
    .index("byClerkUserId", ["clerkUserId"])
    .index("byProxyAddress", ["proxyAddress"])
    .index("bySenderToken", ["senderToken"]),

  // Publications a user reads, as RSS feeds (free posts in full, paid posts as previews).
  subscriptions: defineTable({
    userId: v.id("users"),
    feedUrl: v.string(),
    title: v.string(),
    addedAt: v.number(),
  })
    .index("byUser", ["userId"])
    .index("byUserAndFeed", ["userId", "feedUrl"]),

  // Content model: a post (one URL/guid) has immutable revisions (a hash of the
  // body as delivered); a delivery is one revision sent to one user in one digest.
  // Bodies are not stored: the runner re-reads the public feed.
  posts: defineTable({
    key: v.string(), // the feed item's guid (Substack: the canonical post URL)
    url: v.optional(v.string()),
    title: v.string(),
    publication: v.string(),
    firstSeenAt: v.number(),
  }).index("byKey", ["key"]),

  contentRevisions: defineTable({
    postId: v.id("posts"),
    hash: v.string(), // sha256 of the cleaned Markdown
    createdAt: v.number(),
  }).index("byPostAndHash", ["postId", "hash"]),

  deliveries: defineTable({
    userId: v.id("users"),
    revisionId: v.id("contentRevisions"),
    postKey: v.string(),
    digestKey: v.string(), // idempotency key of the digest email
    status: v.union(v.literal("sent"), v.literal("failed")),
    sentAt: v.number(),
  })
    .index("byUserAndPost", ["userId", "postKey"])
    .index("byUserAndSentAt", ["userId", "sentAt"]),

  // Clerk user ids are never reused. A tombstone stops a late or out-of-order
  // user.created / user.updated webhook from recreating a deleted user.
  deletedClerkUsers: defineTable({
    clerkUserId: v.string(),
    deletedAt: v.number(),
  }).index("byClerkUserId", ["clerkUserId"]),

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
    // Set once the pass-through copy to the user's own inbox was sent.
    forwardedAt: v.optional(v.number()),
  })
    .index("byResendEmailId", ["resendEmailId"])
    .index("byUserAndReceivedAt", ["userId", "receivedAt"])
    // Lets the dashboard read only confirmations, not every stored newsletter.
    .index("byUserKindAndReceivedAt", ["userId", "kind", "receivedAt"]),
});
