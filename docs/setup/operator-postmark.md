# Operator setup: Postmark sending domain

One-time setup performed by the service operator. This establishes the shared,
verified sender identity (`WHITELIST_EMAIL`) that delivers every EPUB and
notification. Done once for the whole service, not per user.

## Prerequisites

- A Postmark account (https://postmarkapp.com) with a Server created.
- Access to the DNS records for `fong888.com` (the sending domain).

## 1. Add the sending domain in Postmark

1. In the Postmark dashboard, go to **Sender Signatures → Domains → Add Domain**.
2. Enter `fong888.com` and click **Verify Domain**.
3. Postmark generates two DNS records you must publish:
   - a **DKIM** record (a `TXT` record, host like `<selector>._domainkey.fong888.com`),
   - a **Return-Path** record (a `CNAME`, host like `pm-bounces.fong888.com`
     pointing at `pm.mtasv.net`).

## 2. Publish the DNS records

1. In your DNS provider for `fong888.com`, add the DKIM `TXT` record and the
   Return-Path `CNAME` record exactly as shown in Postmark.
2. DNS propagation can take from minutes up to a few hours.

## 3. Verify

1. Back in Postmark, on the domain page click **Verify** for both DKIM and
   Return-Path. Both must show **Verified** (green) before sending.
2. Until DKIM is verified, Postmark will reject sends from `@fong888.com`.

## 4. Configure the local environment

Add the sender identity and server token to
`~/development/substack-kindle/.env` (gitignored — never commit real values):

```dotenv
POSTMARK_SERVER_TOKEN=<your Postmark Server API token>
WHITELIST_EMAIL=kindle_whitelist@fong888.com
```

- `POSTMARK_SERVER_TOKEN` is found under **Servers → <your server> → API Tokens**.
- `WHITELIST_EMAIL` is the shared `@fong888.com` address that every send is
  `From:`. It must be on the verified domain above. This same address is what
  each user adds to their Amazon approved-sender list (see
  [user-onboarding.md](./user-onboarding.md)).

## 5. Smoke test

With the env set, send a tiny known EPUB to a Kindle address:

```bash
substack-kindle test-send --to <name>@kindle.com --store ~/.substack-kindle
```

A `200` / `ErrorCode 0` response and the test book landing on the Kindle
confirms the domain is verified and sending works.
