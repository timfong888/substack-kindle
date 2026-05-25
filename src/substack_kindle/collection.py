"""Collect newsletters from approved senders within a job window (SAT-243 / Reqs 1,3,4,5).

Given a window and the customer's approved_sources, return the messages from
approved senders whose date falls within ``[start, end]`` (inclusive). Each
collected newsletter captures its Req-6 ID, sender, date, subject, and issue/
sequence number. The Req-6 ID function is injected (``id_fn``) so this layer does
not depend on the hashing module directly; the pipeline passes A2's
``newsletter_id``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime

# Common newsletter sequence markers: "#42", "Issue 7", "No. 13", "Edition 5".
_ISSUE_PATTERNS = (
    re.compile(r"#\s*(\d+)"),
    re.compile(r"\b(?:issue|no\.?|edition|vol\.?|volume)\s*#?\s*(\d+)\b", re.IGNORECASE),
)


@dataclass
class IncomingMessage:
    """A message offered to the collector (sender already resolved upstream)."""

    message_id: str
    sender: str
    date_sent: datetime
    subject: str


@dataclass
class CollectedNewsletter:
    """A newsletter accepted into a job."""

    newsletter_id: str
    message_id: str
    sender: str
    date_sent: datetime
    subject: str
    issue_number: int | None


def parse_issue_number(subject: str) -> int | None:
    """Best-effort extraction of an issue/sequence number from a subject line."""
    for pattern in _ISSUE_PATTERNS:
        match = pattern.search(subject)
        if match:
            return int(match.group(1))
    return None


def collect_newsletters(
    messages: Iterable[IncomingMessage],
    approved_sources: Iterable[str],
    window_start: datetime,
    window_end: datetime,
    *,
    id_fn: Callable[[str, str, str], str],
) -> list[CollectedNewsletter]:
    """Return collected newsletters from approved senders within the window.

    Senders are matched case-insensitively; the window is inclusive of both
    bounds; input order is preserved.
    """
    approved = {s.lower() for s in approved_sources}
    collected: list[CollectedNewsletter] = []
    for message in messages:
        sender = message.sender.lower()
        if sender not in approved:
            continue
        if not (window_start <= message.date_sent <= window_end):
            continue
        collected.append(
            CollectedNewsletter(
                newsletter_id=id_fn(sender, message.date_sent.isoformat(), message.subject),
                message_id=message.message_id,
                sender=sender,
                date_sent=message.date_sent,
                subject=message.subject,
                issue_number=parse_issue_number(message.subject),
            )
        )
    return collected
