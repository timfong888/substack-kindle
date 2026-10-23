# substack-kindle

Newsletter → EPUB → Kindle digest service. Newsletters arrive at a per-user address on a Google Workspace mailbox we own (`substack+<code>@fong888.com`, decided 2026-10-20 — see `docs/design/intake-mailbox.md`), are stored as Markdown, and go out once a day as a single EPUB via Resend to the user's Kindle. Pass-through to the customer's inbox is Gmail-native forwarding. Resend is send-only. We never read the customer's own mailbox. The RSS path (`cli.py`) remains for free publications. Implementation: SAT-1104 / SAT-1105.

**Requirements and design docs:** [`docs/`](docs/README.md) — read `docs/requirements.md` before feature work.

## Stack

- Python 3.14, `uv` for dependency management
- `ebooklib` — EPUB assembly
- `markdownify` + `BeautifulSoup` — HTML → Markdown parsing
- `python-markdown` with `extra` extension — Markdown → XHTML
- Resend REST API — email delivery (`POST /emails` with a base64 attachment; injected transport)
- Linear team: **Satchel** (`SAT-*` ticket prefix)
- Repo: `~/development/substack-kindle`

## Architecture

- `handler.py` — serverless composition root; processes pre-fetched `InboundMessage`s
- `job_epub.py` — EPUB builder; CSS tables, hierarchical TOC, H1→H2 downgrade
- `pipeline.py` — shared run_job orchestrator; all collaborators injected
- `cli.py` — local entry point; reads `.env` for secrets
- `parsing.py` + `substack_clean.py` — deterministic HTML→Markdown; no LLM on body
- `processed_state.py` — dedup substrate (in-memory; persistent store: SAT-284)

## Key invariants

- **No LLM on newsletter body** — parsing is library-only (Req 8/15)
- **All I/O injected** — modules never make live network calls directly; collaborators passed in
- **TDD** — tests written before production code; Claude Code review skills before every PR; Sourcery + CodeRabbit (under evaluation) review server-side (see docs/requirements.md)
- **Surgical changes** — touch only what the task requires; don't refactor adjacent code

## Dev loop

```
uv run pytest          # full suite (310 tests)
/code-review           # Claude review skill before opening/updating a PR; Sourcery + CodeRabbit review the PR
git push / gh pr       # via HTTPS (SSH port 22 blocked on some networks)
```

Secrets live in `.env` (gitignored): `RESEND_API_KEY`, `WHITELIST_EMAIL`, `KINDLE_EMAIL`.

## Owner instructions (human actions)

Whenever a step needs the owner to act (dashboards, accounts, secrets, merges), give an exact numbered list:
one action per step, the direct link to the specific page (not just the site), the exact menu path and button
label, the exact value or variable name to enter, and where it comes from. Never write "configure X" or
"go to settings". Never ask the owner to paste secrets into chat.
Every step names the **service** (Vercel, Convex, Clerk, Resend, GitHub) and the **environment or instance** it
applies to (e.g. Clerk *Development* instance, Convex *Production* deployment, Vercel *Production* environment).
For every value to copy, give its source: the exact page and the exact on-screen label it sits under.

## Karpathy coding guidelines

This project benefits from the Karpathy guidelines given its clean module boundaries and injected-collaborator design. The skill is available as `andrej-karpathy-skills:karpathy-guidelines`. Apply for any feature work, bug fixes, or multi-file changes. Not required for simple one-line fixes or exploratory queries — use judgment.
