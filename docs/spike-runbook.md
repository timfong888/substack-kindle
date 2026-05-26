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

## Appendix: manual Gmail → Kindle EPUB test (one-off, NOT the product path)

While the Postmark account is pending approval (new accounts can only send to
addresses on the verified sender's own domain), you can sanity-check that a
built EPUB renders correctly on the device by emailing it **manually** from your
own Gmail to your Kindle:

1. Build the EPUB to a file (without sending), e.g. via the pipeline modules, and
   save it (the spike saved one to `~/.substack-kindle/newsletters-YYYY-MM-DD.epub`).
2. In the Gmail web UI, compose to `timfong888@kindle.com`, attach the `.epub`,
   and send. This works because `timfong888@gmail.com` is the Amazon account
   email and is auto-approved as a Send-to-Kindle sender.

**This is a test convenience only.** Notes:

- **Delivery is NOT done through the Gmail connector.** The Anthropic-managed
  Gmail connector (`claude_ai_Gmail`) used for *reading* mail cannot send this:
  its `create_draft` tool states attachments are "not supported yet" and it
  exposes no send tool at all. So the manual step is done by a human in the Gmail
  UI, not by the agent.
- **Production delivery is always via Postmark** (`postmark.send_epub`, the
  `/email` REST API with the EPUB as a base64 attachment) from the shared
  `whitelist_email`. The Gmail connector's role in the product is read-only
  newsletter intake only — never delivery.
- Once Postmark approves the account, drop this manual step entirely and use the
  normal `run` flow above.
