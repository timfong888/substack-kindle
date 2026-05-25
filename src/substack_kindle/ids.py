"""Deterministic newsletter IDs and their source mapping (SAT-238 / Req 6).

`newsletter_id` is the single place IDs are produced: a stable hash over the
normalized (sender, date_sent, subject) triple. `IdRegistry` keeps the reverse
mapping so any ID resolves back to the source values it was derived from.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

__all__ = ["NewsletterRef", "newsletter_id", "IdRegistry"]

# Separator that cannot appear in a normalized field, so distinct triples cannot
# be aliased by field-boundary ambiguity (e.g. ("a","b") vs ("a\x00b","")).
_SEP = "\x00"


def _normalize_date(date_sent: str | datetime) -> str:
    if isinstance(date_sent, datetime):
        return date_sent.isoformat()
    return str(date_sent).strip()


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
