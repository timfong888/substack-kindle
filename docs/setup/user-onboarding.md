# User onboarding

Per-user setup so the service can deliver newsletters to a user's Kindle. This
is required for every user because Amazon only accepts documents emailed from
addresses the user has explicitly approved.

> **Important:** Amazon exposes **no API** for the Approved Personal Document
> E-mail List. Adding the sender is a manual step the user performs in their
> Amazon account — by design, there is no way to automate it.

## 1. Approve the service's sender address (manual, Amazon)

The user adds the shared service sender (`WHITELIST_EMAIL`, e.g.
`kindle_whitelist@fong888.com`) to their Amazon approved list:

1. Go to **Amazon → Account → Manage Your Content and Devices**
   (https://www.amazon.com/mycp).
2. Open the **Preferences** tab.
3. Expand **Personal Document Settings**.
4. Under **Approved Personal Document E-mail List**, click **Add a new
   approved e-mail address**.
5. Enter the service sender address exactly: `kindle_whitelist@fong888.com`.
6. Click **Add**. Documents emailed from any address not on this list are
   silently dropped by Amazon.

## 2. Find the user's Send-to-Kindle address

1. In the same **Personal Document Settings** page, under **Send-to-Kindle
   E-Mail Settings**, copy the user's device address (ends in `@kindle.com`,
   e.g. `username@kindle.com`).
2. This is the `kindle_email` recorded in the user's `CustomerConfig`.

## 3. Record the user's config

Create the user's `CustomerConfig` with:

- `recipient_email` — where the "delivered to your Kindle" notification goes
  (the user's normal email, e.g. their Gmail).
- `kindle_email` — the `@kindle.com` address from step 2.
- `approved_sources` — seed via
  `substack-kindle seed-senders --file <senders.md> --customer <id> --store <dir>`.

## 4. Pre-flight test send

Confirm the approval and address are correct end-to-end:

```bash
substack-kindle test-send --to <username>@kindle.com --store ~/.substack-kindle
```

The test EPUB should appear on the user's Kindle within a minute or two. If it
does not, re-check that the exact `WHITELIST_EMAIL` was added in step 1 (a
typo there is the most common failure) and that the `@kindle.com` address is
correct.
