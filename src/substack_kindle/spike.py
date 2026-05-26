"""Composition root for the end-to-end spike.

Assembles the unchanged pure modules (collect, dedup, build, send, notify) into a
single runnable pipeline. All real I/O (Gmail read, Postmark send, notification
email, processed-state) is injected so this stays testable with fakes and the
real adapters can be wired in by the CLI.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from substack_kindle.collection import (
    CollectedNewsletter,
    IncomingMessage,
    collect_newsletters,
)
from substack_kindle.dedup import deduplicate
from substack_kindle.ids import newsletter_id
from substack_kindle.job_epub import JobSection, build_job_epub
from substack_kindle.notify import send_delivery_notification
from substack_kindle.parsing import html_to_markdown
from substack_kindle.pipeline import JobRunResult, initiate_on_demand_job


@dataclass
class SpikeConfig:
    """Minimal config the spike pipeline needs to run a single job."""

    customer_id: str
    recipient_email: str
    kindle_email: str
    approved_sources: list[str] = field(default_factory=list)


def run_spike(
    cfg: SpikeConfig,
    *,
    incoming: Sequence[IncomingMessage],
    bodies: dict[str, str],
    window: tuple[datetime, datetime],
    send_epub: Callable[..., Any],
    send_email: Callable[..., Any],
    is_delivered: Callable[[str], bool],
    mark_delivered: Callable[..., Any],
) -> JobRunResult:
    """Run collect -> dedup -> build -> send -> notify for one window.

    On a delivered outcome each delivered newsletter ID is recorded via
    ``mark_delivered`` and a delivery notification is sent to the recipient.
    """
    window_start, window_end = window

    def collect(start: datetime, end: datetime) -> Sequence[CollectedNewsletter]:
        return collect_newsletters(
            incoming, cfg.approved_sources, start, end, id_fn=newsletter_id
        )

    def dedup(items: Sequence[CollectedNewsletter]) -> Sequence[CollectedNewsletter]:
        return deduplicate(items, is_delivered)

    def build_epub(deduped: Sequence[CollectedNewsletter]) -> bytes:
        sections = [
            JobSection(title=n.subject, markdown=html_to_markdown(bodies[n.message_id]))
            for n in deduped
        ]
        return build_job_epub(sections, book_title=f"Newsletters {window_start:%Y-%m-%d}")

    def send(epub: bytes) -> Any:
        return send_epub(epub_bytes=epub, to=cfg.kindle_email, filename="newsletters.epub")

    result = initiate_on_demand_job(
        window_start,
        window_end,
        collect=collect,
        dedup=dedup,
        build_epub=build_epub,
        send=send,
    )

    if result.outcome == "delivered":
        for newsletter_id_value in result.delivered_newsletter_ids:
            mark_delivered(newsletter_id_value)
        send_delivery_notification(result, to=cfg.recipient_email, send_email=send_email)

    return result
