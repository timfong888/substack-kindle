# Substack to Kindle — MVP acceptance criteria and test plan

> **Status:** Draft for review (2026-11-17), from the SDLC MVP pass in #79.
> **Source:** the [PRD](https://docs.google.com/document/d/1F4qPDcvFxCcXtYPgc2ClKo57AFJbjyIQfp5dCQ6EhGE/edit) and its
> "Decisions (Tim, 2026-11-17)" paragraph, plus the build in #77. Engineering spec:
> [`substack-kindle-mvp-spec.md`](substack-kindle-mvp-spec.md).
> **Scope:** the RSS path (account → subscriptions → Kindle step → daily digest), built for Tim first. Tim chose RSS
> only on 2026-11-17: free posts arrive in full and paid posts as previews. Full paid posts are open decision Q1.

The persona is a heavy Substack reader who writes, starting with Tim. Each criterion is deterministic: fixed clock, faked
network, and no live Amazon, Resend or Substack calls.

## Journey stage: sign up and add publications

**AC-1: Empty state.** A signed-in user with no subscriptions sees a prompt to add a publication. "Send my first
digest" is refused with "Add at least one publication first".

**AC-2: Add by URL.** Entering a Substack home page (`https://<name>.substack.com`) or a custom-domain home page that
advertises a feed stores one subscription with the feed URL and the feed's title, and shows it in the list.

**AC-3: Refuse unsafe or non-feed input.** Input that is not `https`, that resolves to a non-public address
(`localhost`, `10.0.0.0/8`, `169.254.169.254`), or whose page has no feed is refused with "Couldn't find a feed
there. Try the publication's home page URL." Nothing is stored.

**AC-4: No duplicate subscriptions.** Adding a feed the user already has returns `status: "exists"` and leaves
exactly one subscription for that `(user, feedUrl)`.

**AC-5: Subscription cap.** For a user with 300 subscriptions, adding another returns `status: "full"` and stores
nothing.

## Journey stage: Kindle step

**AC-6: Kindle address validation.** Only addresses ending in `@kindle.com` or `@free.kindle.com` (any letter case,
surrounding spaces trimmed) are saved, lowercased. Anything else is refused with "Enter your Send-to-Kindle address
(it ends in @kindle.com)".

**AC-7: Stable per-user sender.** The user is shown `kindle+<token>@<KINDLE_SENDER_DOMAIN>`, where `<token>` is 26
characters from the base32 alphabet. Repeated visits show the same address, and two users never share a token.

**AC-8: Test send and confirmation.** "Send test" sends one message from the user's sender to their Kindle address. A
second test within 60 seconds is refused. "It arrived" is refused until a test has been sent. Changing the Kindle
address clears the confirmation.

## Journey stage: first value (aha)

**AC-9: First digest on request.** After confirmation, "Send my first digest" causes the next runner pass to send
exactly one EPUB to the user's Kindle, from the user's sender. The EPUB holds that user's feed posts from the last 7
days, at most 25. The runner runs every 15 minutes, so with a working schedule the digest arrives within 15 minutes plus
send time.

**AC-10: Nothing new, nothing sent.** If none of the user's feeds has an undelivered post in the window, no email is
sent and the run counts the user as `empty`.

## Journey stage: habit (daily digest)

**AC-11: Daily schedule.** A confirmed user whose last digest predates the latest daily slot (13:00 UTC) gets one
digest in the first runner pass after that slot, and no second one that day.

**AC-12: Never the same post twice.** A post (keyed by feed item guid) delivered to a user is not delivered to them
again, even if the feed lists it again or its content changes.

**AC-13: Failure isolation and retry.** If sending to one user fails, the other users' digests still go out. The
failed user gets no delivery records, so the next pass retries them. A retry of the same digest cannot send twice:
the Resend idempotency key is derived from the user id and the set of post guids. This guarantee holds only while
the post set is unchanged. If the send succeeded, recording failed and a new post has appeared since, the earlier posts
are sent again: an accepted MVP limitation (spec R6, Q7).

**AC-14: Digest structure.** The EPUB's first page shows the digest date. The table of contents lists every post
title, grouped by publication. Each chapter has exactly one H1, the generated article title
(`<h1 class="article-title">`). The newsletter's own H1 headings become H2.

**AC-18: Cover, and no cover is not a failure.** Each digest EPUB declares a 1600×2560 JPEG cover image showing the
digest date. If rendering the cover fails, the digest is still sent, without a cover.

## Cross-cutting: security and privacy

**AC-15: Runner authentication.** `digest:targets`, `digest:recordDigest` and `digest:markNoDigest` refuse a call
whose HMAC signature is wrong or whose `issuedAt` is more than 5 minutes off the server clock.

**AC-16: Public-log hygiene.** Runner output contains counts and Convex user ids only: no email addresses, post
titles or bodies.

**AC-17: Sender and Kindle addresses differ.** The runner refuses a target whose sender local part equals the Kindle
local part (`ensure_distinct_local_parts`).

## Questions (no criteria written until answered)

| # | Question | Blocks | Owner | Proposed wording if accepted |
|---|---|---|---|---|
| Q1 | Keep the intake mailbox (#75) for full paid posts? The MVP ships previews. | Post-MVP coverage | Tim | "A paid post the user's own inbox received appears in full in their digest." |
| Q2 | Cover thumbnail: the device test in #78 is not done | Cover thumbnail criterion | Tim | "The cover appears as the library thumbnail on Paperwhite, iOS Kindle and Kindle for Web." |
| Q3 | Price and trial (one monthly Stripe price) | Pay stage | Tim | "After a [7/14/30]-day trial, digests stop until the user subscribes at [$2.99/$3.99/$4.99] a month." |
| Q4 | Aha target: first digest within 15 min, 60 min or the same day? | AC-9 time bound | Tim | AC-9 as written assumes 15 min (the runner interval). |
| Q5 | Should an edited post (new content hash) be re-sent? | AC-12 | Tim | AC-12 as written says no, which matches #77. |

## Test plan

Levels: **U** = unit (pure function), **I** = integration (several modules with fakes: `convex-test` for Convex,
injected HTTP and send functions for Python), **E** = end-to-end (UI or a live account, run manually).
**Indep.** = the acceptance test should be written by a different model family from the coder (the acceptance-tests
skill dispatches it on `/codex`). Those tests live in `tests/acceptance/` (pytest) or `web/convex/acceptance/*.test.ts`
(vitest; the existing `convex/**/*.test.ts` glob already collects them), so the existing CI jobs run them.

| AC | Test | Level | Where | CI job | Indep. |
|---|---|---|---|---|---|
| AC-1 | `requestDigest` with zero subscriptions throws | I | `web/convex/acceptance/onboarding.test.ts` | `web (typecheck + vitest)` | Yes |
| AC-2 | `subscriptions.add` with faked fetch of a Substack home page returns `{status: "added", title}`; `subscriptions.list` shows the feed URL and title | I | same | `web` | Yes |
| AC-3 | `add` with http, private-IP and no-feed inputs throws and stores nothing | I | same | `web` | Yes |
| AC-4 | `add` twice → second returns `exists`; `list` has one row for the feed | I | same | `web` | Yes |
| AC-5 | 300 rows seeded, `add` returns `full`; `list` still has 300 | I | same | `web` | Yes |
| AC-6 | `normalizeKindleEmail` table (cases, spaces, wrong domain) and `setKindleEmail` error | U + I | `web/convex/acceptance/kindle.test.ts` | `web` | Yes |
| AC-7 | `ensureSender` twice, then `account.state.senderAddress` → same address both times; token matches `^[A-Z2-7]{26}$`; two users differ | I | same | `web` | Yes |
| AC-8 | `sendTest` with fake Resend; second call within 60 s refused; `confirmKindle` before test refused; `setKindleEmail` clears `kindleConfirmedAt` | I | same | `web` | Yes |
| AC-9 | `run_digests` with fake Convex, fake feeds (30 posts, 10 older than 7 days), fake send: one send, ≤25 posts, all in window, `from` = user sender | I | `tests/acceptance/test_digest_mvp.py` | `lint + test` | Yes |
| AC-10 | feeds with only delivered posts → no send, summary `empty=1` | I | same | `lint + test` | Yes |
| AC-11 | `digest:targets` at 12:59 and 13:16 UTC with `lastDigestAt` the day before → target only at 13:16, and not again after `recordDigest` | I | `web/convex/acceptance/digest.test.ts` | `web` | Yes |
| AC-12 | post key in `deliveredKeys` is skipped even with changed body | I | `tests/acceptance/test_digest_mvp.py` | `lint + test` | Yes |
| AC-13 | two targets, send raises for the first: second is sent, `recordDigest` never called for the first; the retry sends the same idempotency key (same user, same guids). Limitation scenario: send succeeds, `recordDigest` raises, a new post appears, and the next pass sends a different key that includes the earlier posts. Assert this as documented behaviour until Q7 is decided | I | same | `lint + test` | Yes |
| AC-14 | build EPUB from 3 fixture posts across 2 publications; parse with `ebooklib`: date on first page, nav groups, each chapter has exactly one `<h1>`, with class `article-title`; a fixture post's own `# Heading` appears as `<h2>` | U | `tests/acceptance/test_digest_epub.py` | `lint + test` | Yes |
| AC-18 | build with a fake cover renderer: EPUB has a `cover-image` manifest item, 1600×2560 JPEG; with a renderer that raises, the send still happens and the EPUB has no cover | I | `tests/acceptance/test_digest_epub.py` | `lint + test` | Yes |
| AC-15 | bad signature and `issuedAt` ± 6 min refused on all three functions | I | `web/convex/acceptance/digest.test.ts` | `web` | Yes |
| AC-16 | run with fixtures holding known addresses and titles; captured stdout and stderr contain none of them | I | `tests/acceptance/test_digest_mvp.py` | `lint + test` | Yes |
| AC-17 | target with equal local parts → user counted `failed`, no send | I | same | `lint + test` | Yes |
| Smoke | Tim's own account: sign up, add 3 publications, Kindle step, first digest arrives on Paperwhite | E | manual checklist, then `live-e2e.yml` (`workflow_dispatch`) | manual, not a merge gate | No (owner runs it) |

Unit tests for internals (feed parsing, `check_public_https_url`, token generation) stay with the coder, as in #77.
The acceptance tests above treat the code as a black box and must not be edited by the coder. They pin the public
surface: the Convex function names and the `run_digests` entry point listed in the spec.
