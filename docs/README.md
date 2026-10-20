# Docs

Start here for product context before changing code.

| Doc | What it is |
|---|---|
| [requirements.md](requirements.md) | Product requirements, provider decisions, merge gate, bootstrap. Source of truth. |
| [design/intake-mailbox.md](design/intake-mailbox.md) | **Decided (2026-10-20):** Workspace intake mailbox with plus-addresses, Gmail-native pass-through, swappable email store. Supersedes the Resend proxy inbox. |
| [design/ingestion-rss-and-proxy-inbox.md](design/ingestion-rss-and-proxy-inbox.md) | Superseded exploration: RSS-first ingestion, paid newsletters via private feeds / Resend proxy inbox. |
| [design/reading-experience.md](design/reading-experience.md) | Exploration: EPUB across Kindle e-ink, Kindle iOS/Mac apps, Apple Books; features to leverage; positioning. |

Conventions:
- Requirements change only via PR. Record the change in the revision note at the top of `requirements.md`.
- Explorations live in `design/` until accepted, then their decisions are folded into `requirements.md`.
