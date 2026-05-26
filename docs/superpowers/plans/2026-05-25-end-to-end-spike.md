# End-to-End Spike Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Read yesterday's newsletters from `timfong888@gmail.com` (via Gmail MCP), build one EPUB, deliver it to `timfong888@kindle.com` via Postmark, and email a "sent to your Kindle" confirmation to `timfong888@gmail.com`.

**Architecture:** The existing pure modules (collect, dedup, parse, `build_job_epub`, `send_epub`, `notify`, stores) are unchanged. A new composition root + thin adapters + CLI wire real I/O. The Gmail read is supplied as a pre-fetched `messages.json` (the agent queries the Gmail MCP and writes it), consumed behind a file adapter — so the Python pipeline is fully runnable and testable, and the real Google OAuth `GmailTransport` can replace the seam later.

**Tech Stack:** Python 3.11, httpx (new dep), ebooklib/markdownify/bs4 (existing), pytest/ruff, Postmark `/email` REST, Gmail MCP (`mcp__claude_ai_Gmail__*`).

---

## Reference: existing signatures the adapters call

- `collection.IncomingMessage(message_id, sender, date_sent: datetime, subject)` — datetimes must be tz-aware.
- `collection.collect_newsletters(messages, approved_sources, window_start, window_end, *, id_fn) -> list[CollectedNewsletter]` where `CollectedNewsletter(newsletter_id, message_id, sender, date_sent, subject, issue_number)`.
- `ids.newsletter_id(sender, date_sent, subject) -> str`.
- `dedup.deduplicate(items, is_delivered, *, key=lambda i: i.newsletter_id) -> list`.
- `parsing.html_to_markdown(html) -> str`.
- `job_epub.JobSection(title, markdown)` and `job_epub.build_job_epub(sections, *, book_title, identifier=None) -> bytes`.
- `postmark.send_epub(*, epub_bytes, to, from_, filename, server_token, http_post, subject=..., text_body=..., message_stream="outbound") -> SendResult` — `http_post(url, json=..., headers=...) -> response` where response has `.status_code`, `.json()`, `.text`.
- `notify.send_delivery_notification(result, *, to, send_email, subject=..., body=...) -> bool` — `send_email(to=, subject=, body=)` raises on failure.
- `pipeline.initiate_on_demand_job(start, end, *, collect, dedup, build_epub, send, record=None, id_of=lambda n: n.newsletter_id) -> JobRunResult` (`.status`, `.outcome`, `.delivered_newsletter_ids`).
- `config_store.CustomerConfig(customer_id, recipient_email, kindle_email, newsletter_label, gmail_oauth_token_ref, approved_sources=[], whitelisting_status="unconfirmed")`, `InMemoryConfigStore`.
- `processed_state.InMemoryProcessedStateStore` with `is_delivered(id)`, `mark_delivered(id, *, gmail_message_id=None)`.

## File structure

- Create `src/substack_kindle/adapters/__init__.py`
- Create `src/substack_kindle/adapters/postmark_http.py` — real `http_post` (httpx) + a `send_email` notify adapter.
- Create `src/substack_kindle/adapters/gmail_messages.py` — load `messages.json` → `list[IncomingMessage]` + `dict[message_id -> html_body]`.
- Create `src/substack_kindle/adapters/json_store.py` — JSON-file backed config + processed-state.
- Create `src/substack_kindle/spike.py` — composition root: assemble collaborators, run the on-demand job, send, notify.
- Create `src/substack_kindle/cli.py` — `seed-senders`, `fetch-template`, `run`, `test-send`.
- Modify `pyproject.toml` — add `httpx` dep + `[project.scripts]` console entry.
- Tests: one `tests/test_<module>.py` per new module.
- Docs: `docs/setup/operator-postmark.md`, `docs/setup/user-onboarding.md`, `docs/spike-runbook.md`.

---

## Task 1: Add httpx dependency + console script

**Files:** Modify `pyproject.toml`

- [ ] **Step 1: Add `httpx` to dependencies** — under `[project] dependencies` add `"httpx>=0.27"`.
- [ ] **Step 2: Add console script** — new table:
```toml
[project.scripts]
substack-kindle = "substack_kindle.cli:main"
```
- [ ] **Step 3: Install** — `pip install -e ".[dev]"` ; Expected: installs httpx, no errors.
- [ ] **Step 4: Commit** — `git commit -am "build: add httpx dep and console script"`

## Task 2: JSON-backed store

**Files:** Create `src/substack_kindle/adapters/json_store.py`, `tests/test_json_store.py`

- [ ] **Step 1: Failing test**
```python
from pathlib import Path
from substack_kindle.config_store import CustomerConfig
from substack_kindle.adapters.json_store import JsonConfigStore, JsonProcessedStateStore

def test_config_roundtrips_and_unions_sources(tmp_path: Path):
    store = JsonConfigStore(tmp_path / "config.json")
    store.put(CustomerConfig("me", "r@x.com", "k@kindle.com", "Newsletters", "secretref://t",
                             approved_sources=["a@x.com"]))
    store.add_approved_source("me", "b@x.com")
    store.add_approved_source("me", "a@x.com")  # idempotent
    reloaded = JsonConfigStore(tmp_path / "config.json")
    cfg = reloaded.get("me")
    assert cfg.approved_sources == ["a@x.com", "b@x.com"]

def test_processed_state_persists_delivered(tmp_path: Path):
    store = JsonProcessedStateStore(tmp_path / "state.json")
    assert store.is_delivered("n1") is False
    store.mark_delivered("n1", gmail_message_id="m1")
    assert JsonProcessedStateStore(tmp_path / "state.json").is_delivered("n1") is True
```
- [ ] **Step 2: Run, verify fail** — `pytest tests/test_json_store.py -v` → FAIL (module missing).
- [ ] **Step 3: Implement** — `JsonConfigStore` wraps a dict keyed by customer_id persisted to JSON; `get` returns a `CustomerConfig`; `put` unions `approved_sources` with any existing (order-preserving, mirroring `InMemoryConfigStore.put`); `add_approved_source` appends if absent then saves. `JsonProcessedStateStore` persists a set of delivered newsletter_ids + message_ids; `is_delivered`/`mark_delivered` mirror the in-memory store's relevant methods. Never store tokens beyond the existing `gmail_oauth_token_ref` string. Load tolerates a missing file (empty).
- [ ] **Step 4: Run, verify pass** — `pytest tests/test_json_store.py -v` → PASS.
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: JSON-backed config + processed-state stores"`

## Task 3: seed-senders (manual approved-sender ingestion)

**Files:** Create `src/substack_kindle/cli.py` (partial), `tests/test_cli_seed.py`

- [ ] **Step 1: Failing test** — a `seed_senders(file_path, customer_id, store)` helper:
```python
from substack_kindle.adapters.json_store import JsonConfigStore
from substack_kindle.config_store import CustomerConfig
from substack_kindle.cli import seed_senders

def test_seed_senders_dedups_and_lowercases(tmp_path):
    f = tmp_path / "senders.md"
    f.write_text("A@X.com\n\nb@x.com\nA@x.com\n", encoding="utf-8")
    store = JsonConfigStore(tmp_path / "c.json")
    store.put(CustomerConfig("me","r@x.com","k@kindle.com","NL","secretref://t"))
    added = seed_senders(f, "me", store)
    assert added == ["a@x.com", "b@x.com"]
    assert store.get("me").approved_sources == ["a@x.com", "b@x.com"]
```
- [ ] **Step 2: Run, verify fail** — `pytest tests/test_cli_seed.py -v` → FAIL.
- [ ] **Step 3: Implement** — `seed_senders` reads file, strips blank lines, lowercases, dedups in order, calls `store.add_approved_source` for each not already present, returns the net-new (or full normalized) list. Lines must contain `@`.
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** — `git commit -am "feat: seed-senders ingests approved senders from file"`

## Task 4: Gmail fetched-messages adapter

**Files:** Create `src/substack_kindle/adapters/gmail_messages.py`, `tests/test_gmail_messages.py`

**`messages.json` shape (written by the agent from the Gmail MCP):**
```json
{"messages":[{"message_id":"19e...","sender":"bytebytego@substack.com",
  "date_sent":"2026-05-24T15:30:38+00:00","subject":"...","html_body":"<html>..."}]}
```

- [ ] **Step 1: Failing test**
```python
import json
from substack_kindle.adapters.gmail_messages import load_messages

def test_load_messages_maps_incoming_and_bodies(tmp_path):
    p = tmp_path / "messages.json"
    p.write_text(json.dumps({"messages":[{"message_id":"m1","sender":"A@X.com",
        "date_sent":"2026-05-24T15:30:38+00:00","subject":"Hi","html_body":"<p>x</p>"}]}), encoding="utf-8")
    incoming, bodies = load_messages(p)
    assert incoming[0].message_id == "m1"
    assert incoming[0].date_sent.tzinfo is not None
    assert incoming[0].sender == "A@X.com"
    assert bodies["m1"] == "<p>x</p>"
```
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** — `load_messages(path) -> tuple[list[IncomingMessage], dict[str,str]]`. Parse `date_sent` with `datetime.fromisoformat` (tz-aware required; raise if naive). Return `IncomingMessage` list (preserving original sender case — collection lowercases internally) and a `message_id -> html_body` map.
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** — `git commit -am "feat: gmail fetched-messages file adapter"`

## Task 5: Postmark HTTP adapter + notify sender

**Files:** Create `src/substack_kindle/adapters/postmark_http.py`, `tests/test_postmark_http.py`

- [ ] **Step 1: Failing test** — uses a fake httpx-style response; verifies a real-shaped post and a notify `send_email`:
```python
from substack_kindle.adapters.postmark_http import make_http_post, make_send_email

class _Resp:
    status_code = 200
    def __init__(self, body): self._b = body
    def json(self): return self._b
    text = ""

def test_http_post_calls_client_and_returns_response():
    calls = {}
    def fake_post(url, json, headers):
        calls["url"] = url; calls["json"] = json; return _Resp({"ErrorCode":0,"MessageID":"x"})
    http_post = make_http_post(client_post=fake_post)
    resp = http_post("https://api.postmarkapp.com/email", json={"a":1}, headers={"h":"v"})
    assert resp.status_code == 200 and calls["url"].endswith("/email")

def test_send_email_posts_textbody(monkeypatch):
    sent = {}
    def fake_post(url, json, headers): sent.update(json); return _Resp({"ErrorCode":0,"MessageID":"x"})
    send_email = make_send_email(server_token="t", from_="kindle_whitelist@fong888.com", client_post=fake_post)
    send_email(to="timfong888@gmail.com", subject="S", body="B")
    assert sent["To"] == "timfong888@gmail.com" and sent["TextBody"] == "B" and sent["From"].endswith("fong888.com")
```
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** — `make_http_post(client_post=None)` returns a callable `(url, *, json, headers) -> response`; default `client_post` uses `httpx.post(url, json=json, headers=headers, timeout=30)`. `make_send_email(server_token, from_, client_post=None)` returns a `send_email(*, to, subject, body)` that POSTs to `POSTMARK_EMAIL_URL` with `From/To/Subject/TextBody/MessageStream` headers `X-Postmark-Server-Token`, and raises `postmark.PostmarkError` on non-2xx or non-zero `ErrorCode` (reuse the same checks as `postmark.send_epub`).
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** — `git commit -am "feat: real Postmark http_post + notify send_email adapters"`

## Task 6: Composition root + run

**Files:** Create `src/substack_kindle/spike.py`, `tests/test_spike.py`

- [ ] **Step 1: Failing test** — assemble with fakes (canned messages, fake post) and assert a delivered job + a notification:
```python
from datetime import datetime, timezone
from substack_kindle.collection import IncomingMessage
from substack_kindle.spike import run_spike, SpikeConfig

def test_run_spike_delivers_and_notifies():
    msgs = [IncomingMessage("m1","a@x.com", datetime(2026,5,24,12,tzinfo=timezone.utc), "Issue 1")]
    bodies = {"m1": "<p>hello</p>"}
    sends = {}
    def fake_send_epub(*, epub_bytes, to, **kw): sends["epub_to"] = to; return type("R",(),{"message_id":"x","to":to})()
    notes = {}
    def fake_send_email(*, to, subject, body): notes["to"] = to
    cfg = SpikeConfig(customer_id="me", recipient_email="timfong888@gmail.com",
                      kindle_email="timfong888@kindle.com", approved_sources=["a@x.com"])
    result = run_spike(cfg, incoming=msgs, bodies=bodies,
                       window=(datetime(2026,5,24,0,0,tzinfo=timezone.utc), datetime(2026,5,24,23,59,59,tzinfo=timezone.utc)),
                       send_epub=fake_send_epub, send_email=fake_send_email,
                       is_delivered=lambda _id: False, mark_delivered=lambda *a, **k: None)
    assert result.status == "succeeded" and result.outcome == "delivered"
    assert sends["epub_to"] == "timfong888@kindle.com"
    assert notes["to"] == "timfong888@gmail.com"
```
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** — `SpikeConfig` dataclass; `run_spike(cfg, *, incoming, bodies, window, send_epub, send_email, is_delivered, mark_delivered)`:
  1. `collect = lambda s,e: collect_newsletters(incoming, cfg.approved_sources, s, e, id_fn=newsletter_id)`
  2. `dedup = lambda items: deduplicate(items, is_delivered)`
  3. `build_epub = _build` where `_build(deduped)` maps each to `JobSection(title=n.subject, markdown=html_to_markdown(bodies[n.message_id]))` then `build_job_epub(sections, book_title=f"Newsletters {window_start:%Y-%m-%d}")`.
  4. `send = lambda epub: send_epub(epub_bytes=epub, to=cfg.kindle_email, filename="newsletters.epub")`
  5. Call `initiate_on_demand_job(window[0], window[1], collect=collect, dedup=dedup, build_epub=build_epub, send=send)`.
  6. On `result.outcome == "delivered"`: `mark_delivered` each id; `send_delivery_notification(result, to=cfg.recipient_email, send_email=send_email)`.
  7. Return `result`.
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** — `git commit -am "feat: spike composition root (collect→build→send→notify)"`

## Task 7: CLI wiring (run + test-send + fetch-template)

**Files:** Modify `src/substack_kindle/cli.py`, `tests/test_cli_run.py`

- [ ] **Step 1: Failing test** — `build_window("yesterday", now)` returns a tz-aware [00:00, 23:59:59] of the prior local day; `main(["test-send", ...])`-level wiring is smoke-tested via a fake post.
```python
from datetime import datetime, timezone
from substack_kindle.cli import build_window
def test_build_window_yesterday():
    now = datetime(2026,5,25,9,0,tzinfo=timezone.utc)
    start, end = build_window("yesterday", now)
    assert start.hour == 0 and end.hour == 23 and start.date().day == 24 and start.tzinfo is not None
```
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** — argparse `main(argv=None)` with subcommands:
  - `seed-senders --file PATH --customer ID --store DIR` → `seed_senders`.
  - `fetch-template --out PATH` → writes an empty `messages.json` skeleton (so the agent/MCP step has a target schema).
  - `test-send --to ADDR --store DIR` → builds a tiny known EPUB via `build_job_epub([JobSection("Test","# Hello\n\nThis is a Send-to-Kindle test.")], book_title="Test")` and sends via `postmark.send_epub` using env `POSTMARK_SERVER_TOKEN`/`WHITELIST_EMAIL` and `make_http_post()`.
  - `run --window yesterday --messages PATH --customer ID --store DIR` → load config + processed-state from `--store`, `load_messages`, assemble `send_epub`/`send_email` from `postmark_http`, call `run_spike`, print the JobRunResult.
  `build_window(name, now)` supports `"yesterday"`. Env read via `runner.load_runtime_config(os.environ)`.
- [ ] **Step 4: Run, verify pass + full suite** — `pytest -q && ruff check .` → all PASS.
- [ ] **Step 5: Commit** — `git commit -am "feat: CLI run/test-send/seed/fetch-template wiring"`

## Task 8: Setup + runbook docs

**Files:** Create `docs/setup/operator-postmark.md`, `docs/setup/user-onboarding.md`, `docs/spike-runbook.md`

- [ ] **Step 1: operator-postmark.md** — one-time: add `fong888.com` domain in Postmark, add DKIM + Return-Path DNS records, click Verify; set `WHITELIST_EMAIL=kindle_whitelist@fong888.com` + `POSTMARK_SERVER_TOKEN` in `~/development/substack-kindle/.env`.
- [ ] **Step 2: user-onboarding.md** — per user: add the shared `whitelist_email` to Amazon Approved Personal Document E-mail List (Manage Your Content & Devices → Preferences → Personal Document Settings); provide `@kindle.com`; run `test-send`. Note: Amazon exposes **no API** for the approved list — manual step, by design.
- [ ] **Step 3: spike-runbook.md** — the live sequence (Task 9). 
- [ ] **Step 4: Commit** — `git commit -am "docs: operator/user setup + spike runbook"`

## Task 9: Live execution (manual, agent-performed)

> Not a CI test — the real run. Performed once prerequisites are met.

- [ ] **Step 1: Prereqs** — `.env` has `POSTMARK_SERVER_TOKEN` + `WHITELIST_EMAIL`; `fong888.com` verified in Postmark; Amazon whitelist done (✅).
- [ ] **Step 2: Seed senders** — `substack-kindle seed-senders --file "<vault>/Newsletter Senders for Kindle Skill.md" --customer me --store ~/.substack-kindle` (after writing the owner's `CustomerConfig` with recipient `timfong888@gmail.com`, kindle `timfong888@kindle.com`).
- [ ] **Step 3: Pre-flight test send** — `substack-kindle test-send --to timfong888@kindle.com --store ~/.substack-kindle`; confirm the test EPUB lands on the Kindle.
- [ ] **Step 4: Fetch yesterday's mail (agent via Gmail MCP)** — query `from:(<approved senders>) after:YYYY/MM/DD before:YYYY/MM/DD`, fetch each thread FULL_CONTENT for the HTML body, write `messages.json` in the Task-4 schema.
- [ ] **Step 5: Run** — `substack-kindle run --window yesterday --messages messages.json --customer me --store ~/.substack-kindle`.
- [ ] **Step 6: Verify** — EPUB with TOC on `timfong888@kindle.com`; confirmation email in `timfong888@gmail.com`; JobRunResult `succeeded/delivered`.

---

## Self-review notes
- Spec coverage: seeding (T3), Gmail read seam (T4), Postmark send (T5), notify (T6), CLI/run + test-send (T7), setup docs (T8), live run (T9). ✅
- The `gmail_oauth.py` production adapter is intentionally **out of scope** (seam preserved) per the spec.
- Bodies are carried in `messages.json` and keyed by `message_id`; `CollectedNewsletter` does not hold the body, so `build_epub` looks bodies up by `message_id` — matches Task 6 Step 3.
