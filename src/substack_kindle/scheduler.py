"""Scheduled runs with an auto-derived window (SAT-248 / Req 7).

A scheduled trigger runs the shared pipeline over a window derived from the last
successful job: ``[last_successful_end, now]``, which keeps consecutive scheduled
windows contiguous. Each invocation is a single, short, per-run pass (no
held-open session), supporting the Managed Agents pricing model. The pipeline
runner is injected (``run``) so this stays decoupled from the orchestrator and
the job store.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

SCHEDULED = "scheduled"


def initiate_scheduled_job(
    *,
    now: datetime,
    last_successful_end: datetime | None,
    run: Callable[..., Any],
) -> Any:
    """Run one scheduled job over ``[last_successful_end, now]`` via the shared pipeline.

    Raises ``LookupError`` if there is no prior success to derive a window from
    (the first scheduled window must be bootstrapped explicitly), and ``ValueError``
    if ``now`` does not advance past ``last_successful_end``.
    """
    if last_successful_end is None:
        raise LookupError("no last successful job to derive the scheduled window from")
    if (now.tzinfo is None) != (last_successful_end.tzinfo is None):
        # Avoid a bare TypeError from comparing offset-naive vs offset-aware datetimes.
        raise TypeError(
            "now and last_successful_end must both be timezone-aware or both naive; "
            f"got now={now!r}, last_successful_end={last_successful_end!r}"
        )
    if now <= last_successful_end:
        raise ValueError(
            f"now ({now.isoformat()}) must be after the last successful end "
            f"({last_successful_end.isoformat()})"
        )
    return run(start_date=last_successful_end, end_date=now, trigger=SCHEDULED)
