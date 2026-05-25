"""Processed-state store (SAT-239 / Req 17).

Service-side record of which newsletters have been parsed/delivered, keyed by the
Req-6 newsletter ID (with optional secondary lookup by Gmail message-id). This is
the dedup substrate read by backfill dedup (D3); it is deliberately independent of
Gmail labels and of the Kindle — neither is the source of truth for delivery.
"""

from __future__ import annotations

import enum
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime


class ProcessedState(enum.Enum):
    PARSED = "parsed"
    DELIVERED = "delivered"


@dataclass
class _Record:
    newsletter_id: str
    state: ProcessedState
    gmail_message_id: str | None = None
    delivered_at: datetime | None = None


class InMemoryProcessedStateStore:
    """In-memory processed-state store keyed by newsletter ID."""

    def __init__(self) -> None:
        self._by_id: dict[str, _Record] = {}
        self._delivered_message_ids: set[str] = set()

    def mark_parsed(self, newsletter_id: str, *, gmail_message_id: str | None = None) -> None:
        record = self._by_id.get(newsletter_id)
        if record is None:
            self._by_id[newsletter_id] = _Record(
                newsletter_id=newsletter_id,
                state=ProcessedState.PARSED,
                gmail_message_id=gmail_message_id,
            )
        elif gmail_message_id is not None:
            record.gmail_message_id = gmail_message_id

    def mark_delivered(
        self,
        newsletter_id: str,
        *,
        gmail_message_id: str | None = None,
        delivered_at: datetime | None = None,
    ) -> None:
        record = self._by_id.get(newsletter_id)
        if record is None:
            record = _Record(newsletter_id=newsletter_id, state=ProcessedState.DELIVERED)
            self._by_id[newsletter_id] = record
        record.state = ProcessedState.DELIVERED
        if gmail_message_id is not None and gmail_message_id != record.gmail_message_id:
            # Drop a superseded message-id so it is not a stale "delivered" false positive.
            if record.gmail_message_id is not None:
                self._delivered_message_ids.discard(record.gmail_message_id)
            record.gmail_message_id = gmail_message_id
        if delivered_at is not None:
            record.delivered_at = delivered_at
        if record.gmail_message_id is not None:
            self._delivered_message_ids.add(record.gmail_message_id)

    def state_of(self, newsletter_id: str) -> ProcessedState | None:
        record = self._by_id.get(newsletter_id)
        return record.state if record else None

    def is_parsed(self, newsletter_id: str) -> bool:
        record = self._by_id.get(newsletter_id)
        return record is not None and record.state is ProcessedState.PARSED

    def is_delivered(self, newsletter_id: str) -> bool:
        record = self._by_id.get(newsletter_id)
        return record is not None and record.state is ProcessedState.DELIVERED

    def is_message_delivered(self, gmail_message_id: str) -> bool:
        return gmail_message_id in self._delivered_message_ids

    def delivered_at(self, newsletter_id: str) -> datetime | None:
        record = self._by_id.get(newsletter_id)
        return record.delivered_at if record else None

    def delivered_ids(self) -> set[str]:
        return {nid for nid, r in self._by_id.items() if r.state is ProcessedState.DELIVERED}

    def filter_undelivered(self, newsletter_ids: Iterable[str]) -> list[str]:
        """Return the input IDs, in order, that have NOT been delivered (dedup substrate)."""
        return [nid for nid in newsletter_ids if not self.is_delivered(nid)]
