"""Production end-to-end CLI: Gmail → EPUB → Postmark (SAT-269).

Wires the existing modules into a single per-run command:

    uv run substack-kindle --start 2026-05-03 --end 2026-05-09

Required env (matching ``runner.load_runtime_config`` plus a kindle target):

    POSTMARK_SERVER_TOKEN
    WHITELIST_EMAIL    # Verified Postmark sender signature; local-part MUST
                       # differ from the Kindle address local-part to avoid
                       # Amazon's verification trap (SAT-270 research).
    KINDLE_EMAIL

The collaborators (Gmail client builder, approved-sources list, HTTP transport)
are injected into ``main`` so the entire flow is testable without OAuth or live
network. The production wiring picks the real implementations when the args
are omitted.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, time
from pathlib import Path

from . import postmark, postmark_transport
from .digest_title import format_digest_title
from .fetch import fetch_newsletters
from .job_epub import JobSection, build_job_epub
from .pipeline import ON_DEMAND, run_job
from .processed_state import JsonFileProcessedStateStore
from .service_version import service_subheader
from .whitelist_check import ensure_distinct_local_parts

POSTMARK_URL = "https://api.postmarkapp.com/email"
REQUIRED_ENV = ("POSTMARK_SERVER_TOKEN", "WHITELIST_EMAIL", "KINDLE_EMAIL")
DEFAULT_APPROVED_SOURCES_PATH = "~/.config/substack-kindle/approved_sources.json"
DEFAULT_GMAIL_BUNDLE_PATH = "~/.config/substack-kindle/gmail/"
DEFAULT_STATE_PATH = Path("~/.config/substack-kindle/state.json")


@dataclass(frozen=True)
class CliConfig:
    postmark_server_token: str
    whitelist_email: str
    kindle_email: str
    approved_sources_path: Path
    gmail_bundle_path: Path

    def __repr__(self) -> str:
        return (
            "CliConfig(postmark_server_token='***redacted***', "
            f"whitelist_email={self.whitelist_email!r}, "
            f"kindle_email={self.kindle_email!r}, "
            f"approved_sources_path={self.approved_sources_path}, "
            f"gmail_bundle_path={self.gmail_bundle_path})"
        )


def _load_config(env: Mapping[str, str]) -> CliConfig:
    missing = [k for k in REQUIRED_ENV if not env.get(k)]
    if missing:
        raise RuntimeError(f"missing required env: {', '.join(missing)}")
    return CliConfig(
        postmark_server_token=env["POSTMARK_SERVER_TOKEN"],
        whitelist_email=env["WHITELIST_EMAIL"],
        kindle_email=env["KINDLE_EMAIL"],
        approved_sources_path=Path(
            env.get("APPROVED_SOURCES_PATH") or DEFAULT_APPROVED_SOURCES_PATH
        ).expanduser(),
        gmail_bundle_path=Path(
            env.get("GMAIL_BUNDLE_PATH") or DEFAULT_GMAIL_BUNDLE_PATH
        ).expanduser(),
    )


def _parse_iso_date(s: str) -> datetime:
    try:
        return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {s!r}") from exc


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="substack-kindle",
        description="Run one on-demand Newsletter-to-Kindle job for a date window.",
    )
    parser.add_argument("--start", type=_parse_iso_date, required=True,
                        help="Window start date (inclusive), YYYY-MM-DD.")
    parser.add_argument("--end", type=_parse_iso_date, required=True,
                        help="Window end date (inclusive), YYYY-MM-DD.")
    args = parser.parse_args(argv)
    # Fail fast on an inverted window so we don't issue a Gmail query that
    # cannot match anything and quietly succeed with an empty digest.
    if args.start > args.end:
        parser.error("--start must be on or before --end")
    return args


def _end_of_day(d: datetime) -> datetime:
    return datetime.combine(d.date(), time.max, tzinfo=d.tzinfo)


def _build_gmail_client_default(env: Mapping[str, str]):
    # Lazy import so non-network callers (tests, dry-runs) do not require google-api-python-client.
    from .gmail_api import build_gmail_client

    bundle = env.get("GMAIL_BUNDLE_PATH") or DEFAULT_GMAIL_BUNDLE_PATH
    return build_gmail_client(Path(bundle).expanduser())


def _load_approved_sources_default(path: Path) -> list[str]:
    import json

    return json.loads(path.read_text())["senders"]


def main(
    argv: list[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    build_client: Callable[[Mapping[str, str]], object] | None = None,
    approved_sources: list[str] | None = None,
    http_post: Callable[..., object] | None = None,
    state_path: Path | None = None,
) -> int:
    """Run one end-to-end job. Injectable seams keep this testable without OAuth."""
    args = _parse_args(argv)
    env = env if env is not None else os.environ
    config = _load_config(env)

    # Refuse to run on the Amazon local-part collision trap (SAT-270).
    ensure_distinct_local_parts(
        whitelist_email=config.whitelist_email,
        kindle_email=config.kindle_email,
    )

    build_client = build_client or _build_gmail_client_default
    client = build_client(env)
    sources = approved_sources if approved_sources is not None else _load_approved_sources_default(
        config.approved_sources_path
    )

    start_dt = args.start
    end_dt = _end_of_day(args.end)
    range_label = f"{start_dt:%Y-%m-%d}-{end_dt:%Y-%m-%d}"
    attachment_name = f"digest-{range_label}.epub"

    def _collect(s, e):
        return fetch_newsletters(
            client, approved_sources=sources, window_start=s, window_end=e
        )

    store = JsonFileProcessedStateStore(
        (state_path or DEFAULT_STATE_PATH).expanduser()
    )

    def _dedup(items):
        seen: set[str] = set()
        out: list[JobSection] = []
        for item in items:
            if item.title in seen or store.is_delivered(item.title):
                continue
            seen.add(item.title)
            out.append(item)
        return out

    def _record(result) -> None:
        for nid in result.delivered_newsletter_ids:
            store.mark_delivered(nid)

    def _build_epub(items):
        title = format_digest_title(start_dt.date(), end_dt.date())
        return build_job_epub(
            list(items), book_title=title, subtitle=service_subheader()
        )

    def _send(epub_bytes):
        return postmark.send_epub(
            epub_bytes=epub_bytes,
            to=config.kindle_email,
            from_=config.whitelist_email,
            filename=attachment_name,
            server_token=config.postmark_server_token,
            http_post=lambda url, **kwargs: postmark_transport.post(
                url, http_post=http_post, **kwargs
            ),
        )

    result = run_job(
        start_date=start_dt,
        end_date=end_dt,
        trigger=ON_DEMAND,
        collect=_collect,
        dedup=_dedup,
        build_epub=_build_epub,
        send=_send,
        record=_record,
        id_of=lambda section: section.title,
    )

    print(
        f"substack-kindle: trigger={result.trigger} status={result.status} "
        f"outcome={result.outcome} delivered={len(result.delivered_newsletter_ids)}"
    )
    return 0 if result.status == "succeeded" else 1


if __name__ == "__main__":  # pragma: no cover — exercised via the [project.scripts] entry
    raise SystemExit(main())
