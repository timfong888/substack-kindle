# substack-kindle

Newsletter-to-Kindle service. Reads labelled newsletters from a customer's Gmail
(read-only OAuth), converts them to EPUB deterministically, and delivers them to a
Kindle via Postmark.

> **Status:** MVP scaffold. See the planning docs (PRD, system design, user stories)
> in the owner's vault and the Linear project **Newsletter-to-Kindle** (team `SAT`).

## Architecture (summary)

A single window-parameterized pipeline runs for both scheduled and on-demand
(backfill) jobs:

```
trigger → resolve [start,end] → collect approved-sender mail → dedup
        → parse to Markdown → build one EPUB (TOC) → send via Postmark → notify → record
```

- **Compute:** Claude Managed Agents (short, per-run sessions).
- **Body conversion is deterministic (no LLM on newsletter text)** — keeps per-run
  cost roughly constant regardless of newsletter size.
- **Multi-tenant from day one** — per-customer config, shared sending identity.

## Development

```bash
uv sync          # or: pip install -e ".[dev]"
pytest           # run tests (TDD: tests first)
ruff check .     # lint
pre-commit install   # enable the secret-scan hook
```

### Merge gate

A change reaches `main` only after: **CI tests pass** + **Greptile review complete**
+ **PR approved**. No direct pushes to `main`. Every story ships with its tests.

## Security

This is a **public repository**. Never commit secrets. See [SECURITY.md](SECURITY.md).

<!-- greptile status-check probe 2026-05-26T01:04:15Z -->
