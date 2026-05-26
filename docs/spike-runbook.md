# End-to-end spike runbook

The live sequence for the end-to-end spike (Task 9 of the implementation plan):
read yesterday's newsletters from `timfong888@gmail.com`, build one EPUB, deliver
it to `timfong888@kindle.com` via Postmark, and email a confirmation to
`timfong888@gmail.com`.

Run this once the prerequisites below are met. This is the real run, not a CI
test.

## 1. Prerequisites

- `~/development/substack-kindle/.env` has `POSTMARK_SERVER_TOKEN` and
  `WHITELIST_EMAIL` set.
- `fong888.com` is verified in Postmark (DKIM + Return-Path).
  See [setup/operator-postmark.md](./setup/operator-postmark.md).
- The owner has added `kindle_whitelist@fong888.com` to the Amazon Approved
  Personal Document E-mail List. See
  [setup/user-onboarding.md](./setup/user-onboarding.md).

## 2. Seed approved senders

Write the owner's `CustomerConfig` (recipient `timfong888@gmail.com`, kindle
`timfong888@kindle.com`) into the store, then seed the approved-sender list:

```bash
substack-kindle seed-senders \
  --file "<vault>/Newsletter Senders for Kindle Skill.md" \
  --customer me \
  --store ~/.substack-kindle
```

## 3. Pre-flight test send

```bash
substack-kindle test-send --to timfong888@kindle.com --store ~/.substack-kindle
```

Confirm the test EPUB lands on the Kindle before continuing.

## 4. Fetch yesterday's mail (agent, via Gmail MCP)

The agent queries the Gmail MCP for messages from the approved senders within
yesterday's window:

```
from:(<approved senders>) after:YYYY/MM/DD before:YYYY/MM/DD
```

For each matching thread, fetch the FULL_CONTENT HTML body and write
`messages.json` in the Task-4 schema:

```json
{"messages": [
  {"message_id": "...", "sender": "bytebytego@substack.com",
   "date_sent": "2026-05-24T15:30:38+00:00", "subject": "...",
   "html_body": "<html>..."}
]}
```

A skeleton target file can be created with
`substack-kindle fetch-template --out messages.json`.

## 5. Run

```bash
substack-kindle run \
  --window yesterday \
  --messages messages.json \
  --customer me \
  --store ~/.substack-kindle
```

## 6. Verify

- An EPUB with a navigable table of contents arrives on `timfong888@kindle.com`.
- A "delivered to your Kindle" confirmation arrives in `timfong888@gmail.com`.
- The printed `JobRunResult` is `succeeded` / `delivered`.
