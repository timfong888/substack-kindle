"""Tests for the job record store (SAT-240 / #4, PRD §Job model).

Acceptance:
- Job record stores start_date, end_date, trigger (scheduled/on-demand), status,
  outcome, delivered newsletter IDs.
- After a successful job, the next scheduled window derives from the last
  successful job's end_date.
"""

from datetime import UTC, datetime, timedelta

import pytest

from substack_kindle.job_store import (
    InMemoryJobStore,
    JobOutcome,
    JobRecord,
    JobStatus,
    JobTrigger,
)


def _dt(day, hour=0):
    return datetime(2026, 5, day, hour, tzinfo=UTC)


def make_job(job_id="job-1", customer_id="cust-1", **overrides):
    base = dict(
        job_id=job_id,
        customer_id=customer_id,
        start_date=_dt(1),
        end_date=_dt(2),
        trigger=JobTrigger.SCHEDULED,
    )
    base.update(overrides)
    return JobRecord(**base)


def test_job_record_holds_required_fields():
    job = make_job(
        status=JobStatus.SUCCEEDED,
        outcome=JobOutcome.DELIVERED,
        delivered_newsletter_ids=["nl-1", "nl-2"],
    )
    assert job.start_date == _dt(1)
    assert job.end_date == _dt(2)
    assert job.trigger is JobTrigger.SCHEDULED
    assert job.status is JobStatus.SUCCEEDED
    assert job.outcome is JobOutcome.DELIVERED
    assert job.delivered_newsletter_ids == ["nl-1", "nl-2"]


def test_job_defaults_are_pending_and_empty():
    job = make_job()
    assert job.status is JobStatus.PENDING
    assert job.outcome is None
    assert job.delivered_newsletter_ids == []


def test_trigger_values_cover_scheduled_and_on_demand():
    assert JobTrigger.SCHEDULED.value == "scheduled"
    assert JobTrigger.ON_DEMAND.value == "on-demand"


def test_store_add_and_get():
    store = InMemoryJobStore()
    job = make_job()
    store.add(job)
    assert store.get("job-1") is job
    assert store.get("missing") is None


def test_add_rejects_duplicate_job_id():
    store = InMemoryJobStore()
    store.add(make_job(job_id="dup"))
    with pytest.raises(ValueError, match="already exists"):
        store.add(make_job(job_id="dup", status=JobStatus.SUCCEEDED))
    # The original audit entry is preserved, not overwritten.
    assert store.get("dup").status is JobStatus.PENDING


def test_naive_datetimes_rejected_at_construction():
    naive = datetime(2026, 5, 1, 0, 0)
    with pytest.raises(ValueError, match="timezone-aware"):
        make_job(start_date=naive)
    with pytest.raises(ValueError, match="timezone-aware"):
        make_job(end_date=naive)


def test_running_job_excluded_from_last_successful():
    store = InMemoryJobStore()
    store.add(make_job(job_id="running", end_date=_dt(5), status=JobStatus.RUNNING))
    store.add(make_job(job_id="ok", end_date=_dt(3), status=JobStatus.SUCCEEDED))
    assert store.last_successful("cust-1").job_id == "ok"


def test_for_customer_isolated_and_ordered_by_end_date():
    store = InMemoryJobStore()
    store.add(make_job(job_id="a", customer_id="alice", start_date=_dt(1), end_date=_dt(2)))
    store.add(make_job(job_id="b", customer_id="alice", start_date=_dt(2), end_date=_dt(4)))
    store.add(make_job(job_id="c", customer_id="bob", start_date=_dt(1), end_date=_dt(3)))
    alice_jobs = store.for_customer("alice")
    assert [j.job_id for j in alice_jobs] == ["a", "b"]
    assert [j.job_id for j in store.for_customer("bob")] == ["c"]


def test_last_successful_ignores_failed_and_pending():
    store = InMemoryJobStore()
    store.add(make_job(job_id="ok-early", end_date=_dt(2), status=JobStatus.SUCCEEDED))
    store.add(make_job(job_id="failed-late", end_date=_dt(5), status=JobStatus.FAILED))
    store.add(make_job(job_id="pending-late", end_date=_dt(6), status=JobStatus.PENDING))
    store.add(make_job(job_id="ok-late", end_date=_dt(4), status=JobStatus.SUCCEEDED))
    last = store.last_successful("cust-1")
    assert last.job_id == "ok-late"


def test_last_successful_none_when_no_success():
    store = InMemoryJobStore()
    store.add(make_job(status=JobStatus.FAILED))
    assert store.last_successful("cust-1") is None


def test_derive_next_window_starts_at_last_success_end():
    store = InMemoryJobStore()
    store.add(make_job(job_id="ok", end_date=_dt(4), status=JobStatus.SUCCEEDED))
    now = _dt(7)
    start, end = store.derive_next_window("cust-1", now)
    assert start == _dt(4)
    assert end == now


def test_derive_next_window_without_prior_success_raises():
    store = InMemoryJobStore()
    with pytest.raises(LookupError):
        store.derive_next_window("cust-1", _dt(7))


def test_derive_next_window_inverted_raises():
    store = InMemoryJobStore()
    store.add(make_job(job_id="ok", end_date=_dt(5), status=JobStatus.SUCCEEDED))
    with pytest.raises(ValueError, match="must be after"):
        store.derive_next_window("cust-1", _dt(4))
    with pytest.raises(ValueError, match="must be after"):
        store.derive_next_window("cust-1", _dt(5))  # equal is not "after"


def test_window_is_contiguous_across_runs():
    store = InMemoryJobStore()
    store.add(make_job(job_id="j1", start_date=_dt(1), end_date=_dt(3), status=JobStatus.SUCCEEDED))
    now = _dt(3) + timedelta(days=2)
    start, end = store.derive_next_window("cust-1", now)
    assert start == _dt(3)  # no gap, no overlap with the prior window
    assert end == now
