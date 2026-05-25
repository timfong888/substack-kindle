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

    def __post_init__(self) -> None:
        # Timezone-aware only: mixing naive/aware datetimes raises deep in the
        # for_customer sort rather than at construction.
        if self.start_date.tzinfo is None or self.end_date.tzinfo is None:
            raise ValueError("start_date and end_date must be timezone-aware datetimes")


class InMemoryJobStore:
    """In-memory job records, queryable per customer.

    Records are kept mutable so callers advance a job through its lifecycle
    (status/outcome/delivered IDs) in place; ``add`` is append-only and rejects a
    duplicate ``job_id`` so an existing audit entry is never silently overwritten.
    """

    def __init__(self) -> None:
        self._by_id: dict[str, JobRecord] = {}

    def add(self, job: JobRecord) -> None:
        if job.job_id in self._by_id:
            raise ValueError(f"job {job.job_id!r} already exists in the store")
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
        first scheduled window must be bootstrapped explicitly (D2). Raises
        ``ValueError`` if ``now`` does not advance past the last ``end_date``,
        rather than handing back an inverted window.
        """
        last = self.last_successful(customer_id)
        if last is None:
            raise LookupError(f"no successful job for customer {customer_id!r} to derive a window")
        if now <= last.end_date:
            raise ValueError(
                f"now ({now.isoformat()}) must be after the last successful end_date "
                f"({last.end_date.isoformat()})"
            )
        return last.end_date, now
