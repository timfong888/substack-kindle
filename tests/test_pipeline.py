"""Tests for the shared job pipeline + on-demand trigger (SAT-247 / #11, Reqs 11, 16).

Acceptance:
- An on-demand job is triggered with explicit dates; a job record is written with
  trigger=on-demand.
- The job runs the shared collect -> dedup -> build -> send pipeline (one code
  path for every trigger; no separate backfill path).
"""

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from substack_kindle.pipeline import (
    ON_DEMAND,
    SCHEDULED,
    JobRunResult,
    initiate_on_demand_job,
    run_job,
)


@dataclass
class _NL:
    newsletter_id: str


def _dt(day):
    return datetime(2026, 5, day, tzinfo=UTC)


class Harness:
    """Injected collaborators that record the order they were called in."""

    def __init__(self, collected):
        self.events = []
        self._collected = collected
        self.sent = []
        self.records = []

    def collect(self, start, end):
        self.events.append(("collect", start, end))
        return list(self._collected)

    def dedup(self, items):
        self.events.append(("dedup", [i.newsletter_id for i in items]))
        return items

    def build_epub(self, items):
        self.events.append(("build", [i.newsletter_id for i in items]))
        return b"EPUB-BYTES"

    def send(self, epub_bytes):
        self.events.append(("send", epub_bytes))
        self.sent.append(epub_bytes)

    def record(self, result):
        self.records.append(result)


def _run_on_demand(h, start=None, end=None):
    return initiate_on_demand_job(
        start or _dt(1), end or _dt(5), collect=h.collect, dedup=h.dedup,
        build_epub=h.build_epub, send=h.send, record=h.record,
    )


def test_on_demand_job_records_trigger_on_demand():
    h = Harness([_NL("a")])
    result = _run_on_demand(h)
    assert result.trigger == ON_DEMAND
    assert len(h.records) == 1
    assert h.records[0].trigger == ON_DEMAND
    assert h.records[0].start_date == _dt(1)
    assert h.records[0].end_date == _dt(5)


def test_runs_shared_pipeline_in_order():
    h = Harness([_NL("a"), _NL("b")])
    _run_on_demand(h)
    stages = [e[0] for e in h.events]
    assert stages == ["collect", "dedup", "build", "send"]


def test_delivered_ids_recorded_on_success():
    h = Harness([_NL("a"), _NL("b")])
    result = _run_on_demand(h)
    assert result.status == "succeeded"
    assert result.outcome == "delivered"
    assert result.delivered_newsletter_ids == ["a", "b"]
    assert h.sent == [b"EPUB-BYTES"]


def test_empty_after_dedup_skips_build_and_send():
    h = Harness([])  # nothing collected
    result = _run_on_demand(h)
    assert result.outcome == "empty"
    assert result.status == "succeeded"
    assert result.delivered_newsletter_ids == []
    assert h.sent == []
    assert [e[0] for e in h.events] == ["collect", "dedup"]  # no build/send


def test_explicit_dates_must_be_ordered():
    h = Harness([_NL("a")])
    with pytest.raises(ValueError):
        initiate_on_demand_job(
            _dt(5), _dt(1), collect=h.collect, dedup=h.dedup,
            build_epub=h.build_epub, send=h.send, record=h.record,
        )


def test_no_separate_path_scheduled_uses_same_run_job():
    # The scheduled trigger flows through the exact same run_job; only the label differs.
    h = Harness([_NL("a")])
    result = run_job(
        start_date=_dt(1), end_date=_dt(5), trigger=SCHEDULED,
        collect=h.collect, dedup=h.dedup, build_epub=h.build_epub, send=h.send, record=h.record,
    )
    assert result.trigger == SCHEDULED
    assert [e[0] for e in h.events] == ["collect", "dedup", "build", "send"]


def test_send_failure_records_failed_outcome_and_reraises():
    h = Harness([_NL("a")])

    def boom(_epub):
        raise RuntimeError("postmark down")

    with pytest.raises(RuntimeError, match="postmark down"):
        run_job(
            start_date=_dt(1), end_date=_dt(5), trigger=ON_DEMAND,
            collect=h.collect, dedup=h.dedup, build_epub=h.build_epub, send=boom, record=h.record,
        )
    assert len(h.records) == 1
    assert h.records[0].status == "failed"
    assert h.records[0].outcome == "error"


def test_build_failure_records_failed_outcome_and_reraises():
    h = Harness([_NL("a")])

    def boom(_items):
        raise RuntimeError("epub build broke")

    with pytest.raises(RuntimeError, match="epub build broke"):
        run_job(
            start_date=_dt(1), end_date=_dt(5), trigger=ON_DEMAND,
            collect=h.collect, dedup=h.dedup, build_epub=boom, send=h.send, record=h.record,
        )
    assert h.sent == []  # never reached send
    assert h.records[0].status == "failed"
    assert h.records[0].outcome == "error"


def test_record_failure_does_not_mask_original_pipeline_error():
    h = Harness([_NL("a")])

    def boom(_epub):
        raise RuntimeError("postmark down")

    def bad_record(_result):
        raise RuntimeError("recorder exploded")

    # The original pipeline error must surface, not the recorder's.
    with pytest.raises(RuntimeError, match="postmark down"):
        run_job(
            start_date=_dt(1), end_date=_dt(5), trigger=ON_DEMAND,
            collect=h.collect, dedup=h.dedup, build_epub=h.build_epub, send=boom, record=bad_record,
        )


def test_record_is_optional():
    h = Harness([_NL("a")])
    result = run_job(
        start_date=_dt(1), end_date=_dt(5), trigger=ON_DEMAND,
        collect=h.collect, dedup=h.dedup, build_epub=h.build_epub, send=h.send,
    )
    assert isinstance(result, JobRunResult)
