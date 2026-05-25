"""Tests for scheduled runs with an auto-derived window (SAT-248 / #12, Req 7).

Acceptance:
- A scheduled trigger creates a job with trigger=scheduled and an auto-derived
  window (last success -> now).
- Sessions are short and per-run (one pass, no held-open loop).
"""

from datetime import UTC, datetime

import pytest

from substack_kindle.scheduler import SCHEDULED, initiate_scheduled_job


def _dt(day):
    return datetime(2026, 5, day, tzinfo=UTC)


class RunSpy:
    def __init__(self, result="RESULT"):
        self.calls = []
        self._result = result

    def __call__(self, *, start_date, end_date, trigger):
        self.calls.append((start_date, end_date, trigger))
        return self._result


def test_derives_window_from_last_success_to_now():
    run = RunSpy()
    initiate_scheduled_job(now=_dt(10), last_successful_end=_dt(4), run=run)
    assert run.calls == [(_dt(4), _dt(10), SCHEDULED)]


def test_returns_run_result():
    run = RunSpy(result={"ok": True})
    assert initiate_scheduled_job(now=_dt(10), last_successful_end=_dt(4), run=run) == {"ok": True}


def test_trigger_is_scheduled():
    run = RunSpy()
    initiate_scheduled_job(now=_dt(10), last_successful_end=_dt(4), run=run)
    assert run.calls[0][2] == "scheduled"


def test_window_is_contiguous_with_last_success():
    run = RunSpy()
    initiate_scheduled_job(now=_dt(8), last_successful_end=_dt(5), run=run)
    start, end, _ = run.calls[0]
    assert start == _dt(5)  # no gap or overlap with the previous window
    assert end == _dt(8)


def test_no_prior_success_raises():
    run = RunSpy()
    with pytest.raises(LookupError):
        initiate_scheduled_job(now=_dt(10), last_successful_end=None, run=run)
    assert run.calls == []  # nothing run without a derivable window


def test_now_must_advance_past_last_success():
    run = RunSpy()
    with pytest.raises(ValueError):
        initiate_scheduled_job(now=_dt(4), last_successful_end=_dt(4), run=run)
    with pytest.raises(ValueError):
        initiate_scheduled_job(now=_dt(3), last_successful_end=_dt(4), run=run)
    assert run.calls == []


def test_runs_exactly_once_per_invocation():
    # Per-run, short session: one pass through the pipeline, no held-open loop.
    run = RunSpy()
    initiate_scheduled_job(now=_dt(10), last_successful_end=_dt(4), run=run)
    assert len(run.calls) == 1
