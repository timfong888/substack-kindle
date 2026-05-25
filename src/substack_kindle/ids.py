"""Deterministic newsletter IDs and their source mapping (SAT-238 / Req 6).

`newsletter_id` is the single place IDs are produced: a stable hash over the
normalized (sender, date_sent, subject) triple. `IdRegistry` keeps the reverse
mapping so any ID resolves back to the source values it was derived from.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

__all__ = ["NewsletterRef", "newsletter_id", "IdRegistry"]

# Separator that cannot appear in a normalized field, so distinct triples cannot
# be aliased by field-boundary ambiguity (e.g. ("a","b") vs ("a\x00b","")).
_SEP = "\x00"


def _coerce_datetime(value: str | datetime) -> datetime | None:
    """Parse a date value into a datetime, or None if the format is unrecognized."""
    if isinstance(value, datetime):
        return value
    text = value.strip()
    try:
        return datetime.fromisoformat(text)  # handles the "Z" suffix on 3.11+
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(text)  # RFC 2822 (typical email Date: header)
    except (TypeError, ValueError):
        return None


def _normalize_date(date_sent: str | datetime) -> str:
    """Canonicalize a date so equivalent instants collapse to one string.

    All recognized forms (ISO 8601 incl. ``Z``, RFC 2822) parse to a datetime
    and are normalized to UTC so the same instant expressed in different
    notations or offsets yields one ID. Naive datetimes (no tzinfo, e.g. from
    ``datetime.utcnow()``) are assumed to be UTC so they collapse to the same ID
    as the equivalent aware value. Unparseable values fall back to a stripped
    string so the function never raises in the ID path.
    """
    dt = _coerce_datetime(date_sent)
    if dt is None:
        return str(date_sent).strip()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


def _canonical(sender: str, date_sent: str | datetime, subject: str) -> tuple[str, str, str]:
    return (sender.strip(), _normalize_date(date_sent), subject.strip())


def newsletter_id(sender: str, date_sent: str | datetime, subject: str) -> str:
    """Return the deterministic ID for a newsletter.

    Same normalized inputs always produce the same ID; differing inputs produce
    differing IDs. This is the only function that mints newsletter IDs.
    """
    sender_n, date_n, subject_n = _canonical(sender, date_sent, subject)
    digest = hashlib.sha256(_SEP.join((sender_n, date_n, subject_n)).encode("utf-8"))
    return digest.hexdigest()


@dataclass(frozen=True)
class NewsletterRef:
    """The source values an ID was derived from."""

    sender: str
    date_sent: str
    subject: str


class IdRegistry:
    """In-memory mapping of newsletter ID -> source values.

    The datastore-backed implementation (A3/A4) reuses `newsletter_id`; this
    registry captures the reverse lookup the acceptance criteria require.
    """

    def __init__(self) -> None:
        self._by_id: dict[str, NewsletterRef] = {}

    def register(self, sender: str, date_sent: str | datetime, subject: str) -> str:
        """Mint (or look up) the ID for a newsletter and record its source values."""
        nid = newsletter_id(sender, date_sent, subject)
        if nid not in self._by_id:
            sender_n, date_n, subject_n = _canonical(sender, date_sent, subject)
            self._by_id[nid] = NewsletterRef(sender_n, date_n, subject_n)
        return nid

    def resolve(self, newsletter_id_value: str) -> NewsletterRef:
        """Return the source values for a known ID, or raise KeyError."""
        return self._by_id[newsletter_id_value]

    def __contains__(self, newsletter_id_value: str) -> bool:
        return newsletter_id_value in self._by_id

    def __len__(self) -> int:
        return len(self._by_id)
