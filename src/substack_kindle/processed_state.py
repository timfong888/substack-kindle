"""Processed-state store (SAT-239 / Req 17, SAT-284).

Service-side record of which newsletters have been parsed/delivered, keyed by the
Req-6 newsletter ID (with optional secondary lookup by Gmail message-id). This is
the dedup substrate read by backfill dedup (D3); it is deliberately independent of
Gmail labels and of the Kindle — neither is the source of truth for delivery.

SAT-284 adds:
- ``ProcessedStateStore`` — runtime-checkable Protocol; the seam for swapping backends.
- ``JsonFileProcessedStateStore`` — durable, concurrency-safe JSON file backend.
"""

from __future__ import annotations

import enum
import fcntl
import json
import os
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


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


# ---------------------------------------------------------------------------
# SAT-284: Protocol + JSON file backend
# ---------------------------------------------------------------------------

@runtime_checkable
class ProcessedStateStore(Protocol):
    """Structural interface satisfied by every state-store backend.

    All callers (CLI, handler) depend only on this protocol. Swapping from
    ``JsonFileProcessedStateStore`` to a network-call backend (SAT-282) requires
    no changes to callers.
    """

    def is_delivered(self, newsletter_id: str) -> bool: ...
    def mark_delivered(self, newsletter_id: str, **kwargs: Any) -> None: ...
    def filter_undelivered(self, newsletter_ids: Iterable[str]) -> list[str]: ...
    def delivered_ids(self) -> set[str]: ...


_SCHEMA_VERSION = 1
_EMPTY_STATE: dict[str, Any] = {"version": _SCHEMA_VERSION, "records": {}}


class JsonFileProcessedStateStore:
    """Durable, concurrency-safe state store backed by a JSON file (SAT-284 Phase 1).

    Concurrency strategy for light overlap (scheduled + manual run):
    - Exclusive ``fcntl.flock`` on a sidecar ``.lock`` file held only during the
      read-modify-write cycle; readers outside that window see a complete file.
    - ``os.replace`` atomic rename so a crash mid-write never corrupts existing records.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock_path = path.with_suffix(".lock")
        path.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Internal I/O helpers
    # ------------------------------------------------------------------

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self._path.read_text())
            if data.get("version") != _SCHEMA_VERSION:
                return dict(_EMPTY_STATE, records={})
            return data
        except (FileNotFoundError, json.JSONDecodeError):
            return dict(_EMPTY_STATE, records={})

    def _write(self, data: dict[str, Any]) -> None:
        fd, tmp = tempfile.mkstemp(dir=self._path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(data, fh, indent=2)
            os.replace(tmp, self._path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _locked_rw(self, fn) -> None:  # type: ignore[type-arg]
        """Hold exclusive flock, load current state, apply fn, write atomically."""
        with open(self._lock_path, "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                data = self._read()
                fn(data)
                self._write(data)
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)

    # ------------------------------------------------------------------
    # ProcessedStateStore interface
    # ------------------------------------------------------------------

    def is_delivered(self, newsletter_id: str) -> bool:
        return self._read()["records"].get(newsletter_id, {}).get("state") == "delivered"

    def mark_delivered(
        self,
        newsletter_id: str,
        *,
        sender: str | None = None,
        subject: str | None = None,
        delivered_at: datetime | None = None,
        **_: Any,
    ) -> None:
        def _update(data: dict[str, Any]) -> None:
            data["records"][newsletter_id] = {
                "state": "delivered",
                "delivered_at": (delivered_at or datetime.now(UTC)).isoformat(),
                "sender": sender,
                "subject": subject,
            }
        self._locked_rw(_update)

    def filter_undelivered(self, newsletter_ids: Iterable[str]) -> list[str]:
        records = self._read()["records"]
        return [nid for nid in newsletter_ids if records.get(nid, {}).get("state") != "delivered"]

    def delivered_ids(self) -> set[str]:
        return {
            nid for nid, r in self._read()["records"].items()
            if r.get("state") == "delivered"
        }
