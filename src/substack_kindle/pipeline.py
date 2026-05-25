"""Shared job pipeline + on-demand trigger (SAT-247 / Reqs 11, 16).

One window-parameterized pipeline runs for every trigger — there is no separate
backfill code path. ``run_job`` executes collect -> dedup -> build -> send and
records a job result (A4); ``initiate_on_demand_job`` is the on-demand entry that
takes explicit dates and runs that same pipeline with trigger=on-demand. The
collaborators (collect/dedup/build/send/record) are injected so this orchestrator
stays decoupled from the concrete Gmail/EPUB/Postmark/store modules.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

SCHEDULED = "scheduled"
ON_DEMAND = "on-demand"


@dataclass
class JobRunResult:
    trigger: str
    start_date: datetime
    end_date: datetime
    status: str  # "succeeded" | "failed"
    outcome: str  # "delivered" | "empty" | "error"
    delivered_newsletter_ids: list[str] = field(default_factory=list)
    error: str | None = None


def run_job(
    *,
    start_date: datetime,
    end_date: datetime,
    trigger: str,
    collect: Callable[[datetime, datetime], Sequence[Any]],
    dedup: Callable[[Sequence[Any]], Sequence[Any]],
    build_epub: Callable[[Sequence[Any]], bytes],
    send: Callable[[bytes], Any],
    record: Callable[[JobRunResult], Any] | None = None,
    id_of: Callable[[Any], str] = lambda n: n.newsletter_id,
) -> JobRunResult:
    """Run the shared pipeline for a window and record the result.

    An empty job (nothing left after dedup) is a success with outcome ``empty``
    and no send. A send failure is recorded as ``failed``/``error`` and re-raised.
    """
    collected = collect(start_date, end_date)
    deduped = list(dedup(collected))

    if not deduped:
        result = JobRunResult(trigger, start_date, end_date, "succeeded", "empty", [])
        if record is not None:
            record(result)
        return result

    try:
        delivered_ids = [id_of(n) for n in deduped]
        epub_bytes = build_epub(deduped)
        send(epub_bytes)
    except Exception as exc:
        result = JobRunResult(
            trigger, start_date, end_date, "failed", "error", [], error=str(exc)
        )
        if record is not None:
            # Don't let a recording failure mask the original pipeline exception.
            with contextlib.suppress(Exception):
                record(result)
        raise

    result = JobRunResult(trigger, start_date, end_date, "succeeded", "delivered", delivered_ids)
    if record is not None:
        record(result)
    return result


def initiate_on_demand_job(
    start_date: datetime,
    end_date: datetime,
    *,
    collect: Callable[[datetime, datetime], Sequence[Any]],
    dedup: Callable[[Sequence[Any]], Sequence[Any]],
    build_epub: Callable[[Sequence[Any]], bytes],
    send: Callable[[bytes], Any],
    record: Callable[[JobRunResult], Any] | None = None,
    id_of: Callable[[Any], str] = lambda n: n.newsletter_id,
) -> JobRunResult:
    """Start an on-demand job from explicit dates, via the shared pipeline."""
    if start_date > end_date:
        raise ValueError("start_date must not be after end_date")
    return run_job(
        start_date=start_date,
        end_date=end_date,
        trigger=ON_DEMAND,
        collect=collect,
        dedup=dedup,
        build_epub=build_epub,
        send=send,
        record=record,
        id_of=id_of,
    )
