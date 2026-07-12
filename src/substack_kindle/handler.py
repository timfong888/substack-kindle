"""Serverless entry point: process pre-fetched messages through the full pipeline.

This module is the composition root for serverless deployments (AWS Lambda,
GCP Cloud Run, launchd, etc.). It accepts pre-fetched message data — no Gmail
OAuth dependency — and runs the full parse → build → send pipeline. All I/O
is injected so the function is fully testable without live network calls.

Usage:

    from substack_kindle.handler import InboundMessage, process_messages

    result = process_messages(
        messages=[
            InboundMessage(
                sender=..., subject=..., date_sent=..., html_body=..., message_id=...
            )
        ],
        book_title="Newsletter Digest: June 5 2026",
        postmark_server_token=os.environ["POSTMARK_SERVER_TOKEN"],
        whitelist_email=os.environ["WHITELIST_EMAIL"],
        kindle_email=os.environ["KINDLE_EMAIL"],
        http_post=requests.post,
    )
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .fetch import sender_display_name
from .ids import newsletter_id as _newsletter_id
from .job_epub import JobSection, build_job_epub
from .parsing import html_to_markdown
from .postmark import send_epub
from .processed_state import ProcessedStateStore


@dataclass(frozen=True)
class InboundMessage:
    """Pre-fetched message ready for processing — no Gmail/OAuth dependency."""

    sender: str
    subject: str
    date_sent: datetime
    html_body: str
    message_id: str


@dataclass
class HandlerResult:
    status: str  # "ok" | "empty" | "error"
    delivered: int
    epub_size_bytes: int | None = None
    error: str | None = None


def process_messages(
    messages: list[InboundMessage],
    *,
    book_title: str,
    postmark_server_token: str,
    whitelist_email: str,
    kindle_email: str,
    http_post: Callable[..., Any],
    subtitle: str | None = None,
    state: ProcessedStateStore | None = None,
) -> HandlerResult:
    """Parse, build, and deliver a digest EPUB from pre-fetched messages.

    Args:
        messages: Pre-fetched newsletter messages. HTML bodies are run through
            the full parse pipeline (Substack chrome stripper + html_to_markdown).
        book_title: EPUB dc:title; typically a date-range string.
        postmark_server_token: Postmark server token for the outbound stream.
        whitelist_email: Verified Postmark sender; must be on Amazon's approved list.
        kindle_email: Destination Kindle address.
        http_post: Injected HTTP POST callable (use ``requests.post`` in production).
        subtitle: Optional service-version string written to dc:description and
            the front-matter page (see SAT-272).
        state: Optional processed-state store for dedup. Already-delivered
            newsletter IDs are skipped; newly delivered IDs are marked after send.

    Returns:
        HandlerResult with status "ok" (sent), "empty" (nothing to send), or
        raises PostmarkError on send failure (caller decides whether to retry).
    """
    sections: list[JobSection] = []
    ids_to_mark: list[str] = []

    for msg in messages:
        nid = _newsletter_id(msg.sender, msg.date_sent.isoformat(), msg.subject)
        if state is not None and state.is_delivered(nid):
            continue
        markdown = html_to_markdown(msg.html_body)
        if not markdown.strip():
            continue
        sections.append(
            JobSection(
                title=msg.subject,
                markdown=markdown,
                sender=sender_display_name(msg.sender),
            )
        )
        ids_to_mark.append(nid)

    if not sections:
        return HandlerResult(status="empty", delivered=0)

    epub_bytes = build_job_epub(sections, book_title=book_title, subtitle=subtitle)

    send_epub(
        epub_bytes=epub_bytes,
        to=kindle_email,
        from_=whitelist_email,
        filename=f"{book_title}.epub",
        server_token=postmark_server_token,
        http_post=http_post,
    )

    if state is not None:
        for nid in ids_to_mark:
            state.mark_delivered(nid)

    return HandlerResult(
        status="ok",
        delivered=len(sections),
        epub_size_bytes=len(epub_bytes),
    )
