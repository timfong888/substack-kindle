# Requirements — Newsletter-to-Kindle Service

> **Status:** Living document. This is the source of truth for product requirements.
> Design explorations that may change these requirements live in [`docs/design/`](design/).
>
> **Revision 2026-10-04**
> - Code review: Greptile removed. **Claude Code review skills** are the coding agent's review loop. Sourcery and
>   CodeRabbit run as server-side PR reviewers; CodeRabbit is kept while under evaluation (see S2/S3).
> - Transactional email: Postmark replaced by **Resend** (see "Transactional Email Provider — Resend").
> - Ingestion: RSS is the primary path for public newsletters (shipped in SAT-330); paid newsletters
>   are under design exploration — see [`design/ingestion-rss-and-proxy-inbox.md`](design/ingestion-rss-and-proxy-inbox.md).
>   Requirements 1, 6, 9, 13 are **under review** as a result.
> - **Gmail path removed** (decision 2026-10-04). No Gmail OAuth, no label gesture. Paid newsletters will arrive
>   by private feed or inbound email (Resend), not by reading the customer's mailbox. Testing uses the production path.
> - **Output:** EPUB delivered to Kindle is the primary product. PDF is secondary (not in scope now).
>   Reading-surface design (Kindle e-ink, Kindle iOS/Mac apps, Apple Books) — see
>   [`design/reading-experience.md`](design/reading-experience.md).

## Goal

Build a functional personal tool quickly (MVP for own use first), but design it so that hosting it and charging external customers later is straightforward — no rewrite required.

**Primary user:** a Substack reader who wants a better reading experience than the inbox or the Substack app — distraction-free, offline, on Kindle (e-ink and the Kindle apps on iOS / laptop). **Primary output:** one EPUB per job, sent to Kindle. PDF is secondary.

---

## The Three Emails (terminology)

|Name|Role|Scope|
|---|---|---|
|recipient_email|Customer's personal inbox — newsletters arrive here. The service does **not** read it (Gmail path removed); see the design exploration for private feeds / proxy address|Per-customer|
|kindle_email|The `@kindle.com` address — tool SENDS the EPUB here|Per-customer|
|whitelist_email|Verified transactional sending address — the FROM address that sends EPUBs; must be on each customer's Amazon approved-sender list|Shared (one for all customers)|

A single shared `whitelist_email` means every customer whitelists the same string in Amazon, and infrastructure has one sender identity to manage.

A fourth address is proposed by the design exploration: **proxy_email** — a per-customer address on our inbound domain (Resend). Not yet a requirement.

---

## Functional Requirements

1. *(Under review — see design exploration.)* Read the full text of newsletters. Public newsletters: from their RSS feed. Paid newsletters: private feed or a per-customer Resend inbound address (proposed). The service does not read the customer's mailbox.
2. Format newsletters in a readable font and output as EPUB.
3. Track each newsletter by a unique reference ID.
4. Track each newsletter's sending address.
5. Track the EPUB name and number (issue/sequence).
6. *(Under review — see "Identity across channels" in the design exploration for a proposed post-URL canonical key.)* Assign each newsletter a unique ID, produced by a single clear, reusable hash function over a fixed combination of:
    - sender email address
    - date sent
    - subject line
    The same function is called everywhere a newsletter ID is needed (parsing, dedup, storage), so the ID is deterministic and reproducible from the source values. The source values for each ID are also stored in a mapping record, so any ID can be traced back to its sender/date/subject. (The hash is a dedup/lookup key, not a security primitive — it is not expected to be reversible on its own; the stored mapping is the recovery path.)
    *Note:* the RSS path keys on the item `<guid>`, which has no sender email. If RSS and email ingestion coexist, the same post must map to one ID across both channels — see design exploration, "Identity across channels".
7. The process should run automatically on a fixed schedule (e.g. daily/weekly).
8. The LLM does NOT process the actual body text of newsletters — that would make cost scale with newsletter length and volume. Body conversion (HTML → Markdown → EPUB) is done deterministically with libraries, not the model. Any LLM use is confined to small, bounded metadata tasks. As a result, token / processing cost per run is roughly constant and does not scale with the size of the newsletters.
9. *(Under review.)* Make it easy for another person to onboard: add their publications (RSS feeds; for paid newsletters, a private feed URL or proxy address) and their Kindle address (multi-user-ready).
10. Use the simplest available billing service to charge users for the service (productization phase — not part of the MVP).
11. Be able to define a start and end period for newsletters so that historical issues can be backfilled.
12. Notify the user by email when a new update is available/viewable on their Kindle.
13. *(Under review — Gmail path removed.)* The Amazon "approved sender" email goes to the customer's Amazon account email, which the service no longer reads. Default: the customer confirms it themselves, and the "send test document" step verifies the chain. Auto-following the link is only possible if that email reaches a proxy address; the safety constraint below then applies.
14. When the full (multi-issue) EPUB is received, it contains a table of contents listing the included newsletters.
15. When an email is parsed, it is stored as Markdown so it can be easily retrieved at any time.
16. Support an on-demand backfill process that finds older newsletters within an explicit start and end date.
17. When a backfill is requested, newsletters already delivered to the Kindle are excluded (deduplication against prior deliveries).
18. In the EPUB table of contents, clicking any headline navigates to that newsletter.

### Job model — scheduled runs and backfill share one pattern

Backfill is the primitive. Every run — scheduled or on-demand — is a job defined by an explicit `start_date` and `end_date` over which newsletters are collected. A scheduled run is simply a job whose date window is derived automatically (e.g. "since the last successful run" → now); an on-demand backfill is a job whose window is supplied by the customer.

- Both paths run through the same collect → dedup → build EPUB → send pipeline. There is no separate backfill code path.
- Every job records its `start_date`, `end_date`, trigger type (scheduled / on-demand), and outcome, so runs are auditable and the next scheduled window can be computed from the last successful job.
- Deduplication (requirement 17) applies identically to both: a newsletter already in the processed-state store is skipped regardless of how the job was triggered.
- **One EPUB per job.** A job bundles all the newsletters it collects (after dedup) into a single EPUB, with the table of contents (requirements 14, 18) listing every included newsletter. A job that collects one newsletter produces a one-entry EPUB; a backfill spanning weeks produces one EPUB with many entries.
- There must be a way to initiate an on-demand job by supplying explicit `start_date` and `end_date`.
- The TOC links navigate to the specific newsletter within the delivered EPUB on the Kindle (requirement 18).

### Requirement 13 — safety constraint on the approval click

*Applies only if an approval email ever reaches a proxy address. The service does not read customer inboxes, so by default the customer confirms the email themselves.*

Following a link contained in an inbox email is a state-changing action driven by email content. To keep this safe:

- The agent follows the Amazon approval link ONLY when it is expecting one — i.e. during onboarding, immediately after the customer has added whitelist_email.
- The agent must verify the message genuinely originates from Amazon before acting.
- Preferred: surface the pending approval for one-tap customer confirmation rather than fully silent click-through.
- Outside the onboarding window, approval-type emails are not auto-actioned.

The same constraint applies to any other confirmation email the service receives on a customer's behalf (e.g. a Gmail forwarding-confirmation email arriving at a proxy address).

---

## OAuth Scope — Read-Only

*(**Removed 2026-10-04** with the Gmail path. Kept for history; the service holds no mailbox OAuth.)*

- The tool uses Gmail read-only OAuth scope. It never modifies the customer's mailbox (no label changes, no archiving, no deletion).
- Because the tool cannot remove labels, processed-state must NOT be tracked via Gmail labels (see below).
- Productization note: `gmail.readonly` is a Google *restricted* scope. Public use by external customers requires Google OAuth verification plus an annual third-party security assessment (CASA). This is a significant cost and timeline item and is a main reason to evaluate the proxy-inbox design.

---

## Newsletter Identification — Gmail Label Gesture

*(**Removed 2026-10-04** with the Gmail path. Approved sources are the feed URLs in the feeds registry; on the inbound-email path, approved sender addresses per proxy address.)*

The tool must distinguish newsletters from the rest of a personal inbox.

- The customer applies a designated Gmail label (e.g. `+Newsletter`) to a newsletter message. This is a one-time human gesture.
- The tool reads labelled messages and takes the true `From` header directly (no body parsing, no client-format fragility).
- The sole purpose of the label is to identify and register an APPROVED NEWSLETTER SENDER ADDRESS. The label is added to approved_sources.
- The tool does NOT remove the label (read-only scope) and does NOT use the label to mean "processed."
- Once a sender is on approved_sources, future issues from that sender are collected automatically with no further action.

---

## Processed-State Tracking

- Whether a given message has already been parsed/delivered is tracked by the service itself, in its own datastore, keyed by message ID (and/or the requirement-6 hash).
- This state is independent of Gmail labels and independent of the Kindle.
- Deduplication for backfill (requirement 17) reads from this same store.

---

## Configuration (per customer)

Stored as data from day one — one row in the MVP, scalable to many customers.

|Field|Description|
|---|---|
|recipient_email|Customer's personal inbox|
|~~gmail_oauth_token~~|Removed with the Gmail path|
|kindle_email|Customer's Kindle address, e.g. `xxxx@kindle.com`|
|~~newsletter_label~~|Removed with the Gmail path|
|approved_sources|Approved newsletter sources (RSS feed URLs; sender addresses on the email path)|
|whitelisting_status|confirmed / unconfirmed (Amazon approved-sender check)|
|proxy_email|*(Proposed)* per-customer inbound address on our Resend domain|

`whitelist_email` (the sending address) is a single shared system value, not per-customer config.

### First-run / onboarding behavior

1. Customer connects their newsletter sources (RSS feeds for public newsletters; paid-newsletter path per design exploration).
2. Customer provides their kindle_email.
3. Tool displays the shared whitelist_email and instructs the customer to add it to their Amazon "Approved Personal Document E-mail List."
4. Discovery path for customers who have not set this up: Amazon.com -> Account -> Manage Your Content and Devices -> Preferences -> Personal Document Settings On that page the customer will find:
    - each device's Send-to-Kindle email address (editable)
    - the Approved Personal Document E-mail List (where whitelist_email goes)
5. The customer confirms the Amazon approved-sender email themselves (requirement 13); the service does not read their inbox. Step 6 verifies the chain end-to-end.
6. Tool offers a "send test document" action to verify the full chain end-to-end before any scheduled run is enabled.
7. Customer registers approved sources (feed URLs; private feed URLs or proxy-address senders for paid newsletters).

---

## Transactional Email Provider — Resend

**Decision (2026-10-04):** `whitelist_email` sends through **Resend**, replacing Postmark. Reason: Resend also supports **inbound** email (receiving to addresses on our domain, delivered via webhook + API), which the proxy-inbox design depends on. One vendor for send and receive.

- **Provider:** Resend. Verified sending domain (SPF + DKIM) for `whitelist_email`.
- **Credentials:** `RESEND_API_KEY` and sender address supplied at runtime via env/secrets manager only, never committed (see S1a).
- **EPUB send path:** Resend's `POST /emails` accepts an `attachments` array (base64 `content` + `filename`, or a `path` URL). Use it directly from the injected transport, same as the Postmark transport today.
- **Size limit:** Resend's documented per-email limit is 40 MB including attachments *after* base64 encoding (**verify against current Resend docs before relying on it**). Base64 inflates size by ~4/3, so the usable raw EPUB ceiling is ~30 MB. `size_budget.py` already takes a post-base64 cap and derives the raw limit (`_max_raw_bytes`), so the migration replaces `POSTMARK_MAX_MESSAGE_BYTES` (10 MB) with a 40 MB post-encoding constant; do **not** treat 40 MB as a raw-file budget. Amazon's 50 MB cap is not the binding constraint.
- **Inbound:** received mail counts against the same monthly quota as sent mail, and self-serve plans retain email data for 30 days. The service must persist what it needs (Markdown per requirement 15) rather than rely on Resend retention.
- **Inbound webhooks:** the `email.received` webhook carries metadata only. Body, headers and attachments are fetched separately through the Received Emails / Attachments API.
- **MCP:** Resend publishes an MCP server. Use it only if it fits; the EPUB send stays on the REST API through the injected transport (same pattern as before, keeps "all I/O injected").

**Migration impact (not yet done):**
- `postmark.py`, `postmark_transport.py`, `size_budget.py`, `cli.py`, `handler.py`, related tests, `.env.example`, `SECURITY.md`, README and CLAUDE.md reference Postmark.
- If `whitelist_email` changes address or domain, every existing customer must re-add it to their Amazon approved list. Amazon drops unapproved mail silently, with no bounce. Keep the same `whitelist_email` string if possible.

### Superseded: Postmark

Postmark was the original provider (10 MB total message cap; MCP `sendEmail` had no attachment support, so the EPUB used the `/email` REST API directly). Its code is still on `main` until the Resend migration lands.

## Kindle / Email Constraints (reference)

- Amazon Send-to-Kindle accepts a total of 50 MB or less per email, up to 25 attachments per email. Files over 50 MB can be sent as a ZIP, which Amazon's conversion service unpacks and converts automatically.
- The real bottleneck is the sending provider, not Amazon. Budget against the provider's cap (Resend — see above).
- For text-only newsletter EPUBs this is a non-issue (well under 1 MB). It only matters if a backfill batches many issues into one EPUB, or newsletters carry heavy images. If a compiled EPUB risks exceeding the limit, split it or ZIP it.
- Failure mode: if whitelist_email is not on the customer's Amazon approved list, Amazon silently drops the email with no bounce. The "send test document" step exists to catch this.

---

## Engineering Practice — Test-Driven Development

- Development follows TDD. Tests are written before implementation.
- Every user story has associated tests. A story is not complete until its tests exist and pass.
- Acceptance tests (see PLAN role) are the executable definition of "done" for each requirement.
- Each engineering sub-issue in Linear (see LINEARIZE role) includes its test coverage as part of the work, not as a follow-up.

### Development Loop (how the coding agent works the backlog)

Two nested loops keep the agent productive and self-correcting. TDD is what makes them safe: the agent's definition of "done" is external (tests pass + review clean), never self-asserted.

**Outer loop — autonomous iteration (Ralph Wiggum pattern).** The agent works the Linear backlog continuously rather than stopping after one task. Each pass: pick the next unstarted story → write failing tests → implement until green → open a PR → run the inner loop → on merge, move to the next story.

- Mechanism: the `/loop` skill (re-runs a prompt/command, interval or self-paced) or an equivalent shell `while` loop driving the agent. This is the "keep iterating until the queue is empty" layer.
- Guardrails: the agent only advances when the merge gate is satisfied; it does not self-merge past a failing gate. A story it cannot complete is left in a clearly-flagged state for human review rather than force-pushed.

**Inner loop — per-PR review resolution (Claude Code review skills).** Within a single PR, the agent drives the review to clean before merge: run Claude Code review skills → fix → open PR → server-side reviewers (Sourcery; CodeRabbit while under evaluation) comment → agent verifies and resolves every finding → repeat until there are no unresolved comments. Then the merge gate lets it land.

- Mechanism: Claude Code's review skills (e.g. `/code-review`, `/security-review`, `/simplify`) for the coding agent; Sourcery (`sourcery-ai` GitHub App) and CodeRabbit (`.coderabbit.yaml`, under evaluation) as server-side reviewers.
- References: *TBD — owner to add links to the Sourcery setup and the specific Claude skills used for PR review.*
- Complements, does not replace, CI (S4, tests). Both are part of the merge gate.

**Merge gate (see S1/S2/S4):** a change reaches main only after the Claude review loop is clean, server-side review comments are resolved, CI tests pass, and the PR is approved per branch protection.

---

## Deployment Architecture

### MVP and scaling decision

- The MVP will be built on Claude Managed Agents (Anthropic-hosted agent infrastructure: hosted sandbox, state management, credential handling, error recovery). This removes the need to build an agent harness.
- This does NOT force a later rewrite. Managed Agents and the self-hosted Claude Agent SDK share the same SDK and workflow model; logic translates between them. The same build scales from personal MVP to paying customers.
- Caveats to track:
    - Managed Agents is in public beta. Acceptable for a personal MVP. Before charging customers, confirm GA status or that beta terms are acceptable for a paid service.
    - Vendor lock-in: Managed Agents runs only on Anthropic infrastructure (not Bedrock / Vertex). Accepted for this product.
    - Pricing includes a per-session-hour component on top of tokens. This workload is short scheduled bursts (parse -> build EPUB -> send), so keep sessions short and per-run; do not hold sessions open. Supports requirement 8.
- Inbound email (proxy-inbox design) is event-driven: Resend calls a webhook per received email. That needs an always-reachable HTTP endpoint (serverless function), separate from the scheduled agent run. The webhook stores the email; the scheduled job still does collect → EPUB → send.

### What Managed Agents does NOT solve

- Managed Agents removes infrastructure work, not architecture work. The multi-tenant users table, per-customer config, processed-state store, OAuth token storage, and dedup logic are all still to be designed and owned.
- The productization-readiness decisions already made — per-customer config from day one, single shared whitelist_email — are what prevent a rewrite, independent of the compute layer.

---

## Project Setup / Bootstrap (one-time, performed by owner)

These are prerequisites, not product features. Repo creation and app installs involve account access and permission grants — performed by the owner, not the agent. Each item is "done" when its acceptance check passes.

S1. GitHub repository — CREATED - Repository: https://github.com/timfong888/substack-kindle - The repository is PUBLIC. This makes the secrets policy below non-negotiable. - Includes: README, .gitignore, license, and the secrets-handling policy. - Branch protection on main: no direct pushes; changes via pull request. Acceptance: repo cloneable; a test PR can be opened.

S1a. Secrets policy (CRITICAL — public repo) - No tokens, passwords, API keys, OAuth client secrets, or credentials of any kind are ever committed to the repository. - Specifically excluded from the repo: Gmail OAuth client secret and tokens, any code-review service API key, transactional-email credentials (`RESEND_API_KEY`; formerly `POSTMARK_SERVER_TOKEN`), Resend webhook signing secret, Substack private-feed URLs (they embed an auth token), and any per-customer config values. - All secrets are supplied at runtime via environment variables or a secrets manager. Claude Managed Agents provides credential management — use it; do not hardcode. - .gitignore must cover .env files and any local credential files. - Enable GitHub secret scanning and push protection on the repo (free for public repos) so an accidental commit of a secret is blocked at push. - Add a pre-commit secret-scan hook (e.g. gitleaks / trufflehog) as a local gate before push. - Non-secret review config MAY be committed. Acceptance: secret scanning enabled; a test commit containing a dummy key is blocked by push protection and/or the pre-commit hook.

S2. Sourcery — PR reviewer - Sourcery is installed on the repository and reviews PRs automatically. - Configured as a required status check that blocks merge until the review is complete. Acceptance: opening a PR triggers a Sourcery review; merge is blocked until the review completes. *(Replaces Greptile.)* CodeRabbit (`.coderabbit.yaml`) also stays installed while the owner evaluates it; its findings are handled like Sourcery's.

S3. Claude Code review skills (agent-side inner loop) - The development agent runs Claude Code review skills on its own PRs and resolves findings before requesting human approval. - References: *TBD — owner to add.* - Complements S2 — it does not replace it.

S4. CI test runner - A CI pipeline runs the full test suite on every PR. - Required status check: a PR with a failing test is blocked from merge. - This is what makes the TDD requirement enforceable rather than aspirational. Acceptance: a PR with a deliberately failing test cannot be merged.

S5. Linear workspace - A Linear team/project exists to receive the user stories and engineering sub-issues produced by the LINEARIZE role. Acceptance: issues can be created and linked to GitHub PRs.

Merge gate summary: a change reaches main only after (a) Claude review loop clean and server-side review (Sourcery; CodeRabbit under evaluation) resolved (S2/S3), (b) CI tests pass (S4), (c) PR approved per branch protection (S1). AI review and CI are complementary — AI review is sampling-based and catches different issues than the test suite; neither substitutes for the other.

---

## Process / Workflow Roles

ROLE Prompt Engineer PM.

ARCH Review the PRD and ask questions to clarify architecture and design choices.

PLAN Break requirements into clear user stories and simple, clear acceptance tests that engineers can use to validate feature completeness.

REVIEW Review the plan and break it into structured Epics, to make design and user features easier to understand.

LINEARIZE Create user stories in Linear. For each user story, create sub-issues for engineering tasks (e.g. Test Suite).

---

## Open / To Verify

- ~~Backfill scheduling: requirement 16 is on-demand; confirm whether scheduled runs (requirement 7) and backfill share the same pipeline.~~ RESOLVED — see "Job model" above: both are date-windowed jobs through one shared pipeline.
- ~~Confirm the transactional email provider and its attachment cap.~~ RESOLVED (2026-10-04) — Resend. Verify the current per-email size limit before re-pointing `size_budget.py`.
- Paid-newsletter ingestion path — see [`design/ingestion-rss-and-proxy-inbox.md`](design/ingestion-rss-and-proxy-inbox.md).
- Code review references (Sourcery setup, Claude skills) — owner to add.
- ~~Remove or keep CodeRabbit?~~ Keep while under evaluation (2026-10-04).
