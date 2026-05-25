"""Job record store (SAT-240 / PRD §Job model).

Every run is recorded as a ``JobRecord`` for auditability and to compute the next
scheduled window. The next window starts at the last successful job's ``end_date``,
so consecutive scheduled windows are contiguous (no gaps, no overlap).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime


class JobTrigger(enum.Enum):
    SCHEDULED = "scheduled"
    ON_DEMAND = "on-demand"


class JobStatus(enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class JobOutcome(enum.Enum):
    DELIVERED = "delivered"
    EMPTY = "empty"
    ERROR = "error"


@dataclass
class JobRecord:
    job_id: str
    customer_id: str
    start_date: datetime
    end_date: datetime
    trigger: JobTrigger
    status: JobStatus = JobStatus.PENDING
    outcome: JobOutcome | None = None
    delivered_newsletter_ids: list[str] = field(default_factory=list)


class InMemoryJobStore:
    """In-memory job records, queryable per customer."""

    def __init__(self) -> None:
        self._by_id: dict[str, JobRecord] = {}

    def add(self, job: JobRecord) -> None:
        self._by_id[job.job_id] = job

    def get(self, job_id: str) -> JobRecord | None:
        return self._by_id.get(job_id)

    def for_customer(self, customer_id: str) -> list[JobRecord]:
        jobs = [j for j in self._by_id.values() if j.customer_id == customer_id]
        return sorted(jobs, key=lambda j: j.end_date)

    def last_successful(self, customer_id: str) -> JobRecord | None:
        succeeded = [
            j for j in self.for_customer(customer_id) if j.status is JobStatus.SUCCEEDED
        ]
        return succeeded[-1] if succeeded else None

    def derive_next_window(
        self, customer_id: str, now: datetime
    ) -> tuple[datetime, datetime]:
        """Return ``(start, end)`` for the next scheduled run.

        Start is the last successful job's ``end_date`` (contiguous windows); end
        is ``now``. Raises ``LookupError`` when there is no prior success — the
        first scheduled window must be bootstrapped explicitly (D2).
        """
        last = self.last_successful(customer_id)
        if last is None:
            raise LookupError(f"no successful job for customer {customer_id!r} to derive a window")
        return last.end_date, now
