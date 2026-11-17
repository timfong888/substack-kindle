# Substack to Kindle — MVP engineering spec

> **Status:** Draft for review (2026-11-17), from #79. It describes the RSS path being built in #77 and is meant to vet
> that build, not to replace it. Acceptance criteria: [`substack-kindle-mvp.md`](substack-kindle-mvp.md).
> **Supersedes for MVP:** the intake-mailbox ingestion in `docs/design/intake-mailbox.md`. Tim chose RSS only on
> 2026-11-17: the MVP delivers free posts in full and previews of paid posts. Keeping the intake mailbox for full paid
> posts is open decision Q1.

## 1. Architecture

```
Browser ── Clerk auth ──> Next.js on Vercel (web/app) ──> Convex (web/convex): users, subscriptions, posts, deliveries
                                                             ▲  HMAC-signed public functions
GitHub Actions "Digest runner" (every 15 min) ── Python digest_run ──┘
        │  reads public RSS feeds (SSRF-checked)        │ builds EPUB (job_epub)
        └──────────────> Resend POST /emails ──> user's @kindle.com address, From kindle+<token>@mail.fong888.com
```

| Component | Runs on | Owns |
|---|---|---|
| Web app (`web/app/page.tsx`) | Vercel | Onboarding UI: subscriptions, Kindle step, "Send my first digest" |
| Convex functions (`web/convex/*.ts`) | Convex Production | All state; user-facing mutations and actions; runner API |
| Digest runner (`src/substack_kindle/digest_run.py`) | GitHub Actions, `.github/workflows/digest.yml` | Feed reads, EPUB build, send, recording deliveries |
| Resend | SaaS | Delivery only (send-only) |
| DNS for `KINDLE_SENDER_DOMAIN` (`mail.fong888.com`) | Cloudflare | SPF, DKIM, DMARC `p=reject` for the sending subdomain |

## 2. Data model (Convex, `web/convex/schema.ts`)

Times are epoch milliseconds, UTC. Convex ids are opaque strings.

**users**

| Field | Type | Required | Constraint | Set by |
|---|---|---|---|---|
| `clerkUserId` | string | yes | unique (index `byClerkUserId`) | Clerk webhook |
| `email` | string | yes | Clerk primary email | Clerk webhook |
| `proxyAddress` | string | yes | unique, never changed (legacy of intake path) | creation |
| `senderToken` | string | no | 26 chars, `[A-Z2-7]`, unique (index `bySenderToken`); retry on collision, then fail | `account.ensureSender` |
| `kindleEmail` | string | no | lowercase, matches `^[^\s@]+@(free\.)?kindle\.com$` | `account.setKindleEmail` |
| `amazonStore` | string | no | one of `AMAZON_STORES` (`com`, `co.uk`, `ca`, `de`, `fr`, `es`, `it`, `co.jp`, `in`, `com.au`) | `setKindleEmail` |
| `testSentAt` | number | no | last test send | `account.markTestSent` |
| `kindleConfirmedAt` | number | no | cleared when `kindleEmail` changes | `account.confirmKindle` |
| `digestRequestedAt` | number | no | cleared by `recordDigest` / `markNoDigest` | `account.requestDigest` |
| `lastDigestAt` | number | no | last digest or empty run | `recordDigest` / `markNoDigest` |

**subscriptions**: `userId` (id → users, many-to-one), `feedUrl` (https, public host), `title`, `addedAt`. At most
300 per user (`MAX_SUBSCRIPTIONS`). Unique on `(userId, feedUrl)`, enforced in `insert` via index `byUserAndFeed`.

**posts**: `key` (feed item guid; on Substack the canonical post URL; unique via `byKey`), `url?`, `title`,
`publication`, `firstSeenAt`. **contentRevisions**: `postId` (→ posts), `hash` (sha256 hex of cleaned Markdown),
`createdAt`; unique on `(postId, hash)`. **deliveries**: `userId`, `revisionId`, `postKey`, `digestKey`, `status`
∈ {`sent`, `failed`}, `sentAt`. Bodies are not stored.

Indexes and the queries they serve:

| Index | Query |
|---|---|
| `users.bySenderToken` | uniqueness check when issuing a token |
| `subscriptions.byUserAndFeed` | duplicate check on add |
| `subscriptions.byUser` | list subscriptions; runner target build |
| `deliveries.byUserAndSentAt` | `deliveredKeys` for the last 30 days (`DEDUP_LOOKBACK_MS`) per target |
| `deliveries.byUserAndPost` | idempotent `recordDigest` (skip a post already recorded with the same `digestKey`) |
| `posts.byKey`, `contentRevisions.byPostAndHash` | upsert in `recordDigest` |

`digest:targets` scans `users` in full. That is acceptable below roughly 1,000 users; see risk R5.

### Kindle-step state

| State | Condition | Valid next | Invalid |
|---|---|---|---|
| `no_sender` | `senderToken` unset | `sender_ready` (`ensureSender`) | anything else |
| `sender_ready` | token set, no `kindleEmail` | `kindle_saved` | `sendTest`, `confirmKindle`, `requestDigest` |
| `kindle_saved` | `kindleEmail` set, no `testSentAt` | `test_sent` (`sendTest`) | `confirmKindle`, `requestDigest` |
| `test_sent` | `testSentAt` set, `kindleConfirmedAt` unset | `confirmed`; `kindle_saved` on address change | `requestDigest` |
| `confirmed` | `kindleConfirmedAt` set | `test_sent` on address change; digests | — |

A digest target needs `confirmed` and at least one subscription.

## 3. Interfaces

User-facing (Clerk identity required; all throw `Error("Not signed in")` without it):

| Function | Kind | Signature | Errors |
|---|---|---|---|
| `account.state` | query | `() → AccountState` | — |
| `account.ensureSender` | mutation | `() → senderAddress` | "Could not allocate a unique sender token" |
| `account.setKindleEmail` | mutation | `(kindleEmail, amazonStore?) → void` | "Enter your Send-to-Kindle address (it ends in @kindle.com)" |
| `account.sendTest` | action, calls Resend synchronously | `() → void` | "Save your Kindle address first"; "A test was just sent; wait a minute before sending another" (60 s, `TEST_SEND_INTERVAL_MS`); "The test could not be sent. Try again in a minute." |
| `account.confirmKindle` | mutation | `() → void` | "Send a test first" |
| `account.requestDigest` | mutation | `() → void` | "Confirm your Kindle first"; "Add at least one publication first" |
| `subscriptions.add` | action, fetches the page (8 s timeout) | `(input) → {feedUrl, title}` | "Couldn't find a feed there. Try the publication's home page URL." |
| `subscriptions.search` | action, calls Substack's public search API | `(query) → SearchHit[]` | network errors surface as a generic message |
| `subscriptions.list` / `remove` | query / mutation | `() → Subscription[]` / `(id) → void` | — |

Runner API (public Convex functions, HMAC-SHA256 over `PIPELINE_SHARED_SECRET`, `issuedAt` within ±5 min
(`MAX_SKEW_MS`), constant-time compare; failure → `Error("Unauthorized")`):

| Function | Signature | Message signed |
|---|---|---|
| `digest:targets` | `(issuedAt, signature) → Target[]` where `Target = {userId, sender, kindleEmail, feeds[], deliveredKeys[], lastDigestAt}` | per `digest.ts` |
| `digest:recordDigest` | `(userId, digestKey, posts[], issuedAt, signature) → void`; idempotent on `digestKey` | `record-digest:<userId>:<digestKey>:<issuedAt>` |
| `digest:markNoDigest` | `(userId, issuedAt, signature) → void` | `no-digest:<userId>:<issuedAt>` |

Runner (Python, all I/O injected):

```python
run_digests(*, convex: Convex, secret: str, http_get: Callable[[str], bytes | str],
            send: SendFn, now: datetime) -> DigestRunSummary   # sent / empty / failed counts
main(env: Mapping[str, str] | None = None) -> int              # exit 0 unless required env is missing
```

Per target, synchronously and in order: check distinct local parts → fetch each feed with `safe_http_get` (https,
public addresses only, ≤3 redirects, each hop re-checked, 20 s timeout) → keep posts from the last 7 days not in
`deliveredKeys`, newest first, at most 25 → if none, `markNoDigest` → else build EPUB → `send` via Resend with
`idempotency_key = digest-<userId>-<sha256(guids)[:16]>` → `recordDigest`.

Transaction boundaries: each Convex mutation is one transaction. Send and record are not atomic. If the send
succeeds and `recordDigest` fails, the next pass rebuilds the same set of guids, so Resend's idempotency key drops the
duplicate, provided the retry happens inside Resend's idempotency window (24 hours).

External dependencies and failure behaviour:

| Dependency | Timeout | Retry | Fallback |
|---|---|---|---|
| Feed host | 20 s | none in-run; next 15-min pass | skip that feed, digest the others |
| Resend | client default | none in-run; next pass, same idempotency key | user counted `failed`, nothing recorded |
| Convex | 30 s | none in-run | a `targets` failure aborts the run; a per-user failure skips that user |
| Substack search API | 8 s | none | user enters a URL instead |

## 4. Business rules

| Rule | Value | Enforced in |
|---|---|---|
| Daily slot | 13:00 UTC; due when `lastDigestAt` < latest slot | `digest:targets` |
| First digest | served on the next pass, outside the slot | `digestRequestedAt` |
| Lookback | 7 days (`LOOKBACK_DAYS`) | runner |
| Posts per digest | ≤ 25 (`MAX_POSTS`) | runner |
| Cover | text-only 1600×2560 JPEG rendered with Pillow; if rendering fails, the digest sends without a cover | runner (per #77) |
| Dedupe | post guid, 30-day delivery lookback | `targets` + runner |
| Test sends | ≤ 1 per 60 s per user | `sendTest` |
| Subscriptions | ≤ 300 per user | `subscriptions.add` / `insert` |
| Runner concurrency | one run at a time (`concurrency: digest-runner`, no cancel) | workflow |

## 5. Non-functional requirements

| NFR | Target |
|---|---|
| First-digest latency | p95 ≤ 20 min from `requestDigest` to Resend accepting the email (15-min schedule + 5-min run). GitHub cron can lag; see R3. |
| Runner duration | ≤ 10 min per pass (workflow timeout) at 50 users × 20 feeds |
| Daily reliability | ≥ 99% of due users sent or `empty` per day, over 30 days (assumption: Tim to choose 99 / 99.5) |
| Privacy | Actions logs hold counts and Convex ids only; no addresses, titles or bodies (public repo) |
| Email auth | SPF, DKIM and DMARC `p=reject` on `KINDLE_SENDER_DOMAIN` before the first real send |
| Cost | GitHub Actions free tier; Resend ≈ 30 emails per user per month |

## 6. Risks

| # | Risk | Category | Likelihood × impact | Mitigation |
|---|---|---|---|---|
| R1 | Paid posts arrive as previews (accepted for MVP), and the public profile found only 45% of what Tim reads (83/183 in #76), so manual entry must cover the rest | Product | High × Medium | Name search and URL entry; Q1 keeps the intake mailbox as the route to full paid posts |
| R2 | Amazon treats an unrecognised sender as unapproved and silently drops the digest | External | Medium × High | Test send plus "It arrived" confirmation; never mark confirmed automatically |
| R3 | GitHub `schedule` runs late or skips under load, breaking the first-digest promise | Technical | Medium × Medium | `workflow_dispatch` on request is a later option; measure lag for 1 week |
| R4 | A user-supplied feed URL is used for SSRF | Security | Low × High | `check_public_https_url` on every hop; DNS rebinding between check and connect is not covered (accepted for MVP) |
| R5 | `digest:targets` full scan exceeds Convex read limits as users grow | Technical | Low (Tim-first) × Medium | Add an index on a `nextDueAt` field past ~1,000 users |
| R6 | Send succeeds, record fails, retry after 24 h sends a duplicate | Technical | Low × Low | Accept; Kindle shows two copies |
| R7 | Kindle drops EPUBs over its size limit | External | Low × Medium | 25-post cap only: `digest_run` in #77 does not call `size_budget.py`. Add the check (Q6) |

## 7. Open questions

| # | Question | Blocks | Owner | Decide by |
|---|---|---|---|---|
| Q1 | Keep the intake mailbox (#75) for full paid posts? (MVP ships previews) | Post-MVP coverage; R1 | Tim (PM) | after 2 weeks of Tim using previews |
| Q2 | Does the cover show as the library thumbnail on each device? (#78 device test) | Cover thumbnail criterion | Tim | after the device test |
| Q3 | Price and trial | Pay stage | Tim | before a second user |
| Q4 | Aha latency target | AC-9, NFR | Tim | before a second user |
| Q5 | Re-send edited posts | AC-12 | Tim | before a second user |
| Q6 | Wire `size_budget` into `digest_run` (it isn't today) | R7 | Eng | before the first real digest |

## 8. Spec review (spec-review: technical accuracy and implementation readiness)

Run once on the first draft; the fixes are already applied above.

**Technical accuracy: 78/100 → fixed.** Stories 7/10, data model 8, requirements 7, architecture 8, business logic 9,
NFRs 7, testing 8.
- High: "Interfaces" first gave the idempotency key as per user and slot; #77 derives it from the user id and a hash of
  the post guids. Fixed in §3 and AC-13.
- High: there was no transaction boundary for send → record. Added, with the Resend 24-hour window and R6.
- Medium: the indexes had no queries listed. Added the index → query table, and the `targets` full scan as R5.
- Medium: the NFRs had no numbers. Added p95 first-digest latency, runner duration and reliability.

**Implementation readiness: NEEDS CLARIFICATION.** Blockers:
- High (was Critical): paid content. Tim decided on previews for MVP on 2026-11-17. Q1 (full paid posts via the intake mailbox) stays open, post-MVP.
- High: the Kindle-step transitions were implied. Added the state table with invalid transitions.
- High: `digest_run` does not enforce the EPUB size budget (Q6). Logged for Eng.
- Medium: the price stage has no criteria until Q3 is answered, and the cover thumbnail criterion waits on the Q2 device test. Logged, by design.

Verdicts: accuracy GOOD (after fixes) · readiness READY TO IMPLEMENT for the RSS path, with Q6 to fix before the first real digest.
