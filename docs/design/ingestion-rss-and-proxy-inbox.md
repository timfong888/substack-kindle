# Design exploration — RSS-first ingestion with a per-customer proxy inbox for paid newsletters

> **Status:** Exploration (2026-10-04). Not a requirement yet. Decisions here will amend
> [`../requirements.md`](../requirements.md) (requirements 1, 6, 9, 13; Gmail sections).
> Items marked **verify** are unconfirmed claims that must be checked before building on them.

## Problem

- Public newsletters work well over RSS (shipped in SAT-330). No OAuth, no inbox access.
- Paid newsletters are the hard case. A public Substack feed contains only a teaser for paid posts.
- The Gmail read-only OAuth path works for one user. For paying customers, `gmail.readonly` is a Google
  *restricted* scope: it requires app verification plus an annual third-party security assessment (CASA).
  That costs real money and calendar time, and asks customers to grant inbox-wide read access.

## Goal

The cleanest customer UX:

1. Public newsletters → open RSS. Nothing for the customer to do beyond naming the publication.
2. Paid newsletters → email. The customer gets a **proxy address** we generate (via Resend). Mail sent to it is
   parsed for the Kindle, **and** still reaches the customer's real inbox.
3. Routing between (1) and (2) is automatic and invisible to the customer.

## Options for the paid path (ranked by simplicity)

### A. Substack private RSS feed — verify first, highest leverage

Several sources report that Substack gives each paid subscriber a personal feed URL with full paid-post content
(from `substack.com/account/reading` → "Get private RSS feed", shaped like
`https://<pub>.substack.com/feed/private/<token>`). **Verify** with a real paid subscription.

If this holds, paid Substacks need **no email infrastructure at all**. The customer pastes one URL per paid
publication; the existing RSS pipeline handles it.

- Cost: zero marginal infra. Reuses `rss_fetch.py`.
- Changes: the SSRF allowlist in `cli.py` (`_validate_feed_url`) currently accepts only `https://*.substack.com/feed`; extend it to the
  private-feed shape. Private-feed URLs embed a credential: store them as secrets, never log them.
  **Prerequisite:** `_validate_feed_url` currently puts the full rejected URL into `InvalidFeedUrlError`, so
  a malformed private-feed URL would leak its token into logs and tracebacks. Redact the path token and query
  string from validation errors (with a test) *before* the allowlist accepts credential-bearing URLs.
- Limits: Substack only. Ghost, beehiiv and others need their own answer (or option B/C). Token revocation and
  rotation are Substack-controlled.

### B. Inbox-side forward rule → proxy address (no pass-through needed)

The customer keeps their subscription email unchanged and adds one forwarding filter in their mail client
(e.g. Gmail: `from:(@substack.com) → forward to tim-7f3k@in.<our-domain>`). The originals never leave their inbox, so
we don't have to deliver anything back to them.

- No OAuth. No change to any newsletter account. No deliverability risk from re-sending.
- Gmail verifies a new forwarding address by sending a confirmation email **to the proxy address**. Our inbound
  handler receives it and shows the code/link to the customer for one-tap confirmation. Apply the requirement-13
  safety rules: only during onboarding, verify the sender is Google.
- Works with any provider that supports forwarding rules (Gmail, Outlook, iCloud, Fastmail).
- UX cost: the customer must create one filter. Provide copy-paste filter text and a "send test" check.

### C. Proxy-in-front (pass-through) — the originally proposed design

The customer changes the email on their newsletter subscription to the proxy address. We receive every issue, store
and parse it, then forward a copy to the customer's real address.

- Resend can do this: inbound webhook → fetch body via API → send a copy out.
- **Blind spots:**
  1. **Account email = login email.** On Substack, one account email covers *all* subscriptions and receives the
     magic-link login emails. Changing it to the proxy routes every subscription and every login through our service.
     If we are down or misroute, the customer can't log in to Substack. This is the biggest risk in C.
  2. **We can't forward "as" the newsletter.** Re-sending with `From: newsletter@substack.com` fails SPF/DMARC.
     Forwards must come `From:` our domain with `Reply-To:` the original sender. The customer sees a different sender.
     This breaks their existing filters, sender avatars, and possibly `List-Unsubscribe`.
  3. **Deliverability is now ours.** If customers mark forwarded newsletters as spam, our sending domain's reputation
     suffers. That domain also carries `whitelist_email` to Kindle. Use a separate subdomain for forwards.
  4. **Double quota.** Each issue costs one inbound plus one outbound email against the Resend quota.
  5. **Exit.** If the customer cancels, they must change their subscription email back. Until they do, we keep
     forwarding (we'd have to keep forwarding for some grace period after cancel).
- Upside: it works even where the customer can't create forwarding rules, and it is the only option that
  doesn't depend on any action in the customer's mail client.

### Recommendation

Do **A → B → C**, in that order. Prove A with one real paid Substack (an hour of work). If A holds, most paid
volume is solved with zero infra. Build B for non-Substack paid newsletters. Keep C as a fallback only: its
failure modes land on the customer's login and inbox, which is the wrong place for an MVP to take risk.

Note that B and C both make RSS optional. Once all of a customer's newsletter mail reaches the proxy, every issue,
free or paid, arrives there. RSS remains useful for (a) zero-setup onboarding of free newsletters and (b) backfill:
feeds carry recent history, while a new proxy address starts with no history.

## Routing: which source is "paid"?

The customer shouldn't have to classify publications. Detect it per source:

- **Feed truncation signal:** for each RSS item, check whether the body is a teaser. On Substack that means a
  paywall marker or a subscribe-prompt block and a body far shorter than usual. **Verify** the exact markers
  against real paid-post items.
- **Email arrival signal:** if an issue arrives at the proxy address for a publication whose RSS item for the same
  post is truncated, the email copy wins.
- Rule: for each post, prefer the **full-text** copy, whichever channel it arrived on. This makes routing a dedup
  decision rather than a configuration decision.

## Identity across channels

Requirement 6 hashes `(sender email, date, subject)`. RSS items have no sender email, and an emailed issue and its
RSS item can differ in timestamp. A post that arrives on both channels would get two IDs and be delivered twice.

Proposal: the canonical key is the **post URL** (Substack emails link to the canonical post; the RSS `<link>`/`<guid>`
is the same URL). Fall back to the requirement-6 hash only when no canonical URL is found. This amends requirement 6.

## Resend design sketch (options B and C)

- **Domain:** a dedicated inbound subdomain, e.g. `in.<our-domain>`, with MX records pointing at Resend. Keep it separate
  from the sending domain used for `whitelist_email`.
- **Per-customer address:** we don't provision mailboxes. A proxy address is just a random, unguessable local part we
  record in the customer table (e.g. `tim-7f3k9q@in.<our-domain>`). **Verify** that Resend accepts all local parts
  on a receiving domain (catch-all). We then reject unknown local parts in our handler.
- **Flow:** Resend `email.received` webhook (metadata only) → our serverless endpoint verifies the webhook
  signature → looks up the customer by recipient address → fetches the body via the Received Emails API → runs the
  existing deterministic parser → stores Markdown (requirement 15) → in C only, sends a pass-through copy.
  The scheduled job reads stored Markdown for its window. The pipeline doesn't change shape.
- **Retention:** Resend keeps received email for 30 days on self-serve plans. Persist what we need at receipt time.
- **Allowlist:** only accept mail from approved senders per customer. Anything else is dropped or held. Otherwise
  the proxy becomes an open relay into the customer's Kindle.
- **Abuse:** treat inbound bodies as untrusted input (existing parser is deterministic and LLM-free, which helps).

## Economics (rough, verify against current Resend pricing)

Search-reported pricing (2026): Free 3k emails/mo (100/day); Pro $20/mo for 50k; Scale from $90/mo for 100k.
Received mail counts against the same quota.

Per customer per month, assuming 15 newsletters at ~15 issues/month each:

| Item | Option B | Option C |
|---|---|---|
| Inbound issues | ~225 | ~225 |
| Pass-through forwards | 0 | ~225 |
| Kindle deliveries + notifications | ~60 | ~60 |
| **Total emails** | **~285** | **~510** |
| Customers per Pro plan (50k) | ~175 | ~98 |
| Email cost per customer | ~$0.11 | ~$0.20 |

Email cost isn't the constraint at any plausible price point. The real costs are Google CASA (if Gmail OAuth is
kept), support load from option C's failure modes, and the webhook endpoint's hosting. Running this for every
customer, not only paying ones, is affordable at these numbers.

## UX — what the customer sees

Onboarding (option B shown; C differs only in step 3):

1. "Add your newsletters." Customer enters publication URLs. Free ones go to RSS immediately.
2. For paid Substacks: "Paste your private feed link" (option A), with a direct link to the Substack page.
3. For other paid newsletters: "Forward them to `tim-7f3k9q@in.<our-domain>`." Show the exact filter text to paste.
   We catch the forwarding-confirmation email and show "Confirm forwarding" as one tap.
   - Option C instead: "Change your subscription email to `tim-7f3k9q@in.<our-domain>`. You'll still get every issue
     in your normal inbox, sent from `newsletters@<our-domain>`." State the login implication plainly.
4. "Send test" checks the full chain: proxy → parse → Kindle.

The customer must always be able to answer "where do my newsletters go?" A per-customer page lists each
publication, its channel (RSS / private feed / email), last issue received, and last delivered to Kindle.

## Sign-up and proxy-address flow

### Sign-in never means Gmail access

**Launch: email magic link only** (via Clerk; see the decisions below). Google sign-in is added later.

When Google is added, request only the basic identity scopes Clerk uses for Google: `openid email profile`.
The service gets a verified email address (`email`, `email_verified`, a stable `sub` id), plus name and avatar
from `profile`, and **nothing from the mailbox**. These are non-sensitive scopes, so none of the restricted-scope
verification or CASA burden that killed the Gmail path applies. Never request any `gmail.*` scope.

### Auth and platform decisions (2026-10-04)

- **Auth: Clerk.** It provides the hosted sign-in UI, sessions and verified email. Launch with **email magic link
  only**: no OAuth client is needed. Add "Continue with Google" later. In development Clerk's shared Google credentials
  work; in production Clerk requires our own Google OAuth client ID and secret (scopes `openid email profile`,
  about 15 minutes in Google Cloud, no CASA).
- **Database and webhooks: Convex.** Clerk `user.created` → Convex HTTP action → user row and proxy address.
  Resend `email.received` → Convex HTTP action → fetch body → stored email row. Convex validates Clerk sessions
  natively.
- **Web app: Next.js on Vercel** (`web/`): sign-in and a dashboard showing the proxy address.
- **Inbound domain: Resend-managed `<anything>@<id>.resend.app`** (catch-all, no DNS) for now; move to our own
  subdomain later. Proxy address format: `<slug>-<8 random chars>@<id>.resend.app`.
- **Pipeline:** the Python job reads stored emails for its window from Convex (Python client) and feeds them to
  `handler.process_messages`. Body parsing stays in Python (no LLM).
- Provisioning steps: `docs/setup-inbox.md` (added with the implementation PR).

### Flow

1. **Sign in**: "Email me a link" at launch; "Continue with Google" is added later. Create the customer row keyed by
   the Clerk user id, storing the verified email as `recipient_email`.
2. **Proxy address is issued immediately** — generate a random, unguessable local part, e.g.
   `tim-7f3k9q@in.<our-domain>`, and store it. No mailbox is provisioned: the inbound subdomain's MX points to
   Resend and every address on it is received (**verify** catch-all). The "inbox" is our own table of received
   messages keyed by proxy address. Unknown local parts are dropped.
3. **Kindle** — enter `kindle_email`; show the shared `whitelist_email` with the Amazon steps; "Send test document".
4. **Add publications** — paste Substack URLs. Free ones go to RSS straight away.
5. **Paid publications** — in order of preference:
   a. paste the private feed URL (if #66 confirms it exists);
   b. add a mail forward rule to the proxy address (copy-paste filter text; we show the forwarding-confirmation code
      when it lands at the proxy);
   c. change the Substack account email to the proxy (pass-through; see risks under option C).
6. **Dashboard** — each publication with its channel, last issue received, and last delivered to Kindle.

### Can the Substack email change be done by API / curl instead of the browser?

Not reliably, and not without the customer doing the browser step anyway:

- Substack has **no public API**. Unofficial endpoints exist (`substack.com/api/v1/*`, community-mapped), but they
  are undocumented, can change without notice, and need the customer's Substack **session cookie**. Holding that
  cookie means holding full account access, which is a large credential and terms-of-service liability.
- Substack's email change sends **confirmation emails to both the old and the new address** and logs the user out
  everywhere. The new-address confirmation would reach our proxy and could be auto-handled. The old-address one
  lands in the customer's real inbox, so **the customer must click it**. Curl can't remove that step.
- The change is **account-wide**: every subscription and the login magic links move to the proxy. If the proxy
  address already has a Substack account, Substack merges the two.

What *can* be done without a browser: the unofficial per-publication free-subscribe endpoint
(`POST https://<pub>.substack.com/api/v1/free`, form field `email`) subscribes any address to a **free**
publication. We don't need this, because free publications come via RSS. It doesn't help paid ones: a paid
subscription belongs to the account that paid for it.

**Conclusion:** don't automate the Substack email change. Prefer the private feed (5a) or a forward rule (5b),
both of which leave the customer's Substack account untouched.

## Open questions

1. Is the Substack private feed real and full-text for paid posts? (Decides how much of B/C we need.)
2. What share of target customers' paid newsletters are not on Substack?
3. Keep the Gmail OAuth path for the owner's personal use, or retire it once A/B work?
4. Output format: the product delivers EPUB today. Is PDF needed for any case? (Kindle reflows EPUB; PDF does not.)
5. Is "for every paying customer" a product tier boundary, or should proxy addresses be issued to all customers?
