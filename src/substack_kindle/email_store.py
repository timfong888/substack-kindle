"""The email store the pipeline reads and writes (SAT-1104, docs/design/intake-mailbox.md §5).

``EmailStore`` is the only interface the ingest and digest jobs depend on, so the
backend can change without touching them: Convex for the MVP, Supabase or
Parquet+DuckDB later. ``InMemoryEmailStore`` is the reference implementation
and the test double; every backend must pass ``tests/test_email_store.py``.

Gmail stays the raw archive. The store keeps Markdown only for ``newsletter``
rows, and ``purge_bodies_older_than`` drops those bodies once they have been
delivered for long enough, keeping the metadata row.

Windows are half-open, ``[since, until)``, so back-to-back daily windows never
select the same email twice. All datetimes are timezone-aware.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

Kind = Literal["newsletter", "notification", "account", "rejected"]


def _require_aware(name: str, value: datetime) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")


@dataclass(frozen=True)
class DeliveryTarget:
    user_code: str
    kindle_email: str | None


@dataclass(frozen=True)
class StoredEmail:
    user_code: str
    gmail_message_id: str
    message_id: str | None
    from_address: str
    from_name: str | None
    publication: str | None
    post_url: str | None
    subject: str
    received_at: datetime
    kind: Kind
    substack_user_id: str | None
    markdown: str | None
    stored_at: datetime
    delivered_at: datetime | None = None
    markdown_bytes: int = field(init=False)

    def __post_init__(self) -> None:
        _require_aware("received_at", self.received_at)
        _require_aware("stored_at", self.stored_at)
        if self.delivered_at is not None:
            _require_aware("delivered_at", self.delivered_at)
        object.__setattr__(
            self, "markdown_bytes", len(self.markdown.encode()) if self.markdown else 0
        )


@runtime_checkable
class EmailStore(Protocol):
    def exists(self, gmail_message_id: str) -> bool: ...

    def get(self, gmail_message_id: str) -> StoredEmail | None: ...

    def put(self, email: StoredEmail) -> None: ...

    def delivery_targets(self) -> list[DeliveryTarget]: ...

    def undelivered(
        self, user_code: str, *, since: datetime, until: datetime
    ) -> list[StoredEmail]: ...

    def mark_delivered(self, gmail_message_ids: list[str], *, delivered_at: datetime) -> None: ...

    def purge_bodies_older_than(self, cutoff: datetime) -> int: ...


class InMemoryEmailStore:
    """Reference ``EmailStore``: a dict keyed on the Gmail message id.

    ``targets`` seeds ``delivery_targets``; a real backend reads them from its
    users table, so this constructor argument is not part of the protocol.
    """

    def __init__(self, targets: list[DeliveryTarget] | None = None) -> None:
        self._rows: dict[str, StoredEmail] = {}
        self._targets = list(targets or [])

    def exists(self, gmail_message_id: str) -> bool:
        return gmail_message_id in self._rows

    def get(self, gmail_message_id: str) -> StoredEmail | None:
        return self._rows.get(gmail_message_id)

    def put(self, email: StoredEmail) -> None:
        # First write wins: a re-run after a crash between put and label must not
        # overwrite a row the digest may already have read.
        self._rows.setdefault(email.gmail_message_id, email)

    def delivery_targets(self) -> list[DeliveryTarget]:
        return [t for t in self._targets if t.kindle_email]

    def undelivered(self, user_code: str, *, since: datetime, until: datetime) -> list[StoredEmail]:
        _require_aware("since", since)
        _require_aware("until", until)
        if since > until:
            raise ValueError("since must not be after until")
        rows = [
            r
            for r in self._rows.values()
            if r.user_code == user_code
            and r.kind == "newsletter"
            and r.delivered_at is None
            and since <= r.received_at < until
        ]
        return sorted(rows, key=lambda r: (r.received_at, r.gmail_message_id))

    def mark_delivered(self, gmail_message_ids: list[str], *, delivered_at: datetime) -> None:
        _require_aware("delivered_at", delivered_at)
        for gid in gmail_message_ids:
            row = self._rows.get(gid)
            if row is not None and row.delivered_at is None:
                self._rows[gid] = replace(row, delivered_at=delivered_at)

    def purge_bodies_older_than(self, cutoff: datetime) -> int:
        """Drop the Markdown of rows delivered before ``cutoff``; return how many."""
        _require_aware("cutoff", cutoff)
        purged = 0
        for gid, row in list(self._rows.items()):
            if (
                row.markdown is not None
                and row.delivered_at is not None
                and row.delivered_at < cutoff
            ):
                self._rows[gid] = replace(row, markdown=None)
                purged += 1
        return purged


__all__ = ["DeliveryTarget", "EmailStore", "InMemoryEmailStore", "Kind", "StoredEmail"]
