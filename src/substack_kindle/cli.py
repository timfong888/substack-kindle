"""Command-line entry point for the end-to-end spike.

Subcommands wire the pure pipeline to real I/O: ``seed-senders`` ingests an
approved-sender list, ``fetch-template`` writes a ``messages.json`` skeleton,
``test-send`` delivers a tiny known EPUB, and ``run`` executes the full
collect -> build -> send -> notify pipeline for a window.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, time, timedelta
from pathlib import Path

from substack_kindle.adapters.gmail_messages import load_messages
from substack_kindle.adapters.json_store import JsonConfigStore, JsonProcessedStateStore
from substack_kindle.adapters.postmark_http import make_http_post, make_send_email
from substack_kindle.config_store import CustomerConfig
from substack_kindle.job_epub import JobSection, build_job_epub
from substack_kindle.postmark import send_epub
from substack_kindle.runner import load_runtime_config
from substack_kindle.spike import SpikeConfig, run_spike

# Module-level HTTP seam: ``None`` means "use the live httpx transport"; tests
# monkeypatch this to a fake ``(url, json, headers) -> response`` callable.
_client_post = None

# The spike reads Gmail via the MCP, so no raw OAuth token is stored; this is the
# token *reference* a production gmail_oauth.py adapter would later resolve.
_GMAIL_TOKEN_REF = "mcp://gmail"


def init_customer(
    store: JsonConfigStore,
    *,
    customer_id: str,
    recipient_email: str,
    kindle_email: str,
    newsletter_label: str,
) -> CustomerConfig:
    """Create (or update) the customer's config row.

    Re-initializing an existing customer preserves any already-seeded
    ``approved_sources`` (the store unions them on ``put``), so rotating the
    Kindle address never drops the approved-sender list.
    """
    config = CustomerConfig(
        customer_id=customer_id,
        recipient_email=recipient_email,
        kindle_email=kindle_email,
        newsletter_label=newsletter_label,
        gmail_oauth_token_ref=_GMAIL_TOKEN_REF,
    )
    store.put(config)
    return store.get(customer_id)


def seed_senders(file_path: str | Path, customer_id: str, store: JsonConfigStore) -> list[str]:
    """Ingest an approved-sender list from ``file_path`` into the customer's config.

    Lines are stripped, lowercased, and deduplicated in order; blank lines and
    lines without an ``@`` are skipped. Returns the full normalized sender list
    parsed from the file (idempotent: re-seeding an existing sender is a no-op
    against the store).
    """
    text = Path(file_path).read_text(encoding="utf-8")
    normalized: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip().lower()
        if not line or "@" not in line:
            continue
        if line not in normalized:
            normalized.append(line)
    for sender in normalized:
        store.add_approved_source(customer_id, sender)
    return normalized


def build_window(name: str, now: datetime) -> tuple[datetime, datetime]:
    """Resolve a named window to a tz-aware ``(start, end)`` pair.

    ``"yesterday"`` returns ``[00:00:00, 23:59:59]`` of the day before ``now``,
    preserving ``now``'s timezone.
    """
    if name != "yesterday":
        raise ValueError(f"unsupported window {name!r}")
    tz = now.tzinfo
    if tz is None:
        raise ValueError("now must be timezone-aware")
    prior = (now - timedelta(days=1)).date()
    start = datetime.combine(prior, time(0, 0, 0), tzinfo=tz)
    end = datetime.combine(prior, time(23, 59, 59), tzinfo=tz)
    return start, end


def _store_paths(store_dir: str | Path) -> tuple[Path, Path]:
    base = Path(store_dir)
    return base / "config.json", base / "state.json"


def _cmd_init_customer(args: argparse.Namespace) -> int:
    config_path, _ = _store_paths(args.store)
    store = JsonConfigStore(config_path)
    cfg = init_customer(
        store,
        customer_id=args.customer,
        recipient_email=args.recipient_email,
        kindle_email=args.kindle_email,
        newsletter_label=args.newsletter_label,
    )
    print(
        f"customer {cfg.customer_id!r} configured "
        f"(recipient={cfg.recipient_email}, kindle={cfg.kindle_email}, "
        f"approved_sources={len(cfg.approved_sources)})"
    )
    return 0


def _cmd_seed_senders(args: argparse.Namespace) -> int:
    config_path, _ = _store_paths(args.store)
    store = JsonConfigStore(config_path)
    added = seed_senders(args.file, args.customer, store)
    print(f"seeded {len(added)} approved senders for {args.customer!r}")
    return 0


def _cmd_fetch_template(args: argparse.Namespace) -> int:
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"messages": []}, indent=2), encoding="utf-8")
    print(f"wrote messages.json skeleton to {out}")
    return 0


def _cmd_test_send(args: argparse.Namespace) -> int:
    runtime = load_runtime_config(os.environ)
    epub_bytes = build_job_epub(
        [JobSection("Test", "# Hello\n\nThis is a Send-to-Kindle test.")],
        book_title="Test",
    )
    http_post = make_http_post(client_post=_client_post)
    result = send_epub(
        epub_bytes=epub_bytes,
        to=args.to,
        from_=runtime.whitelist_email,
        filename="test.epub",
        server_token=runtime.postmark_server_token,
        http_post=http_post,
    )
    print(f"test EPUB sent to {result.to} (MessageID={result.message_id})")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    runtime = load_runtime_config(os.environ)
    config_path, state_path = _store_paths(args.store)
    config_store = JsonConfigStore(config_path)
    cfg = config_store.get(args.customer)
    if cfg is None:
        print(f"no config stored for customer {args.customer!r}")
        return 1
    state_store = JsonProcessedStateStore(state_path)

    incoming, bodies = load_messages(args.messages)
    window = build_window(args.window, datetime.now().astimezone())

    http_post = make_http_post(client_post=_client_post)

    def send_epub_adapter(*, epub_bytes, to, filename):
        return send_epub(
            epub_bytes=epub_bytes,
            to=to,
            from_=runtime.whitelist_email,
            filename=filename,
            server_token=runtime.postmark_server_token,
            http_post=http_post,
        )

    send_email = make_send_email(
        server_token=runtime.postmark_server_token,
        from_=runtime.whitelist_email,
        client_post=_client_post,
    )

    spike_cfg = SpikeConfig(
        customer_id=cfg.customer_id,
        recipient_email=cfg.recipient_email,
        kindle_email=cfg.kindle_email,
        approved_sources=list(cfg.approved_sources),
    )
    result = run_spike(
        spike_cfg,
        incoming=incoming,
        bodies=bodies,
        window=window,
        send_epub=send_epub_adapter,
        send_email=send_email,
        is_delivered=state_store.is_delivered,
        mark_delivered=state_store.mark_delivered,
    )
    print(
        f"job {result.status}/{result.outcome}; "
        f"delivered {len(result.delivered_newsletter_ids)} newsletter(s)"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="substack-kindle")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init-customer", help="create or update a customer config row")
    p_init.add_argument("--customer", required=True)
    p_init.add_argument("--recipient-email", required=True)
    p_init.add_argument("--kindle-email", required=True)
    p_init.add_argument("--newsletter-label", default="Newsletters")
    p_init.add_argument("--store", required=True)
    p_init.set_defaults(func=_cmd_init_customer)

    p_seed = sub.add_parser("seed-senders", help="ingest approved senders from a file")
    p_seed.add_argument("--file", required=True)
    p_seed.add_argument("--customer", required=True)
    p_seed.add_argument("--store", required=True)
    p_seed.set_defaults(func=_cmd_seed_senders)

    p_tmpl = sub.add_parser("fetch-template", help="write an empty messages.json skeleton")
    p_tmpl.add_argument("--out", required=True)
    p_tmpl.set_defaults(func=_cmd_fetch_template)

    p_test = sub.add_parser("test-send", help="send a tiny known EPUB to a Kindle address")
    p_test.add_argument("--to", required=True)
    p_test.add_argument("--store", required=True)
    p_test.set_defaults(func=_cmd_test_send)

    p_run = sub.add_parser("run", help="run the full pipeline for a window")
    p_run.add_argument("--window", default="yesterday")
    p_run.add_argument("--messages", required=True)
    p_run.add_argument("--customer", required=True)
    p_run.add_argument("--store", required=True)
    p_run.set_defaults(func=_cmd_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
