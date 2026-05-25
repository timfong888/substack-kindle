"""Tests for collecting newsletters from approved senders within a window (SAT-243 / #7).

Acceptance:
- Given a job window, only messages from approved senders within [start, end] are returned.
- Each collected newsletter captures: Req-6 ID, sender, date, subject, issue/sequence number.
- Messages from non-approved senders are ignored.
"""

from datetime import UTC, datetime

import pytest

from substack_kindle.collection import (
    IncomingMessage,
    collect_newsletters,
    parse_issue_number,
)


def _dt(day):
    return datetime(2026, 5, day, 9, 0, tzinfo=UTC)


def _fake_id(sender, date_sent, subject):
    # Stand-in for the A2 (SAT-238) newsletter_id, injected by the pipeline.
    return f"id::{sender}::{date_sent}::{subject}"


WINDOW = (_dt(1), _dt(10))
APPROVED = ["alice@a.example", "bob@b.example"]


def test_parse_issue_number_common_patterns():
    assert parse_issue_number("Weekly Roundup #42") == 42
    assert parse_issue_number("Issue 7: Things") == 7
    assert parse_issue_number("No. 13 — Friday") == 13
    assert parse_issue_number("Edition 5") == 5
    assert parse_issue_number("Just a title") is None


def test_parse_issue_number_prefers_semantic_marker_over_bare_hash():
    # A bare "#N" elsewhere in the subject must not win over the real issue marker.
    assert parse_issue_number("See tweet #7 — Issue 15") == 15


def test_timezone_naive_date_raises():
    naive = datetime(2026, 5, 3, 9, 0)  # no tzinfo
    messages = [IncomingMessage("m1", "alice@a.example", naive, "hi")]
    with pytest.raises(ValueError, match="timezone-naive"):
        collect_newsletters(messages, APPROVED, *WINDOW, id_fn=_fake_id)


def test_collects_only_approved_senders():
    messages = [
        IncomingMessage("m1", "alice@a.example", _dt(2), "Alice #1"),
        IncomingMessage("m2", "stranger@x.example", _dt(2), "Spam #9"),
    ]
    collected = collect_newsletters(messages, APPROVED, *WINDOW, id_fn=_fake_id)
    assert [c.sender for c in collected] == ["alice@a.example"]


def test_window_is_inclusive_and_filters_outside():
    messages = [
        IncomingMessage("before", "alice@a.example", datetime(2026, 4, 30, tzinfo=UTC), "x"),
        IncomingMessage("start", "alice@a.example", _dt(1), "start"),
        IncomingMessage("mid", "alice@a.example", _dt(5), "mid"),
        IncomingMessage("end", "alice@a.example", _dt(10), "end"),
        IncomingMessage("after", "alice@a.example", datetime(2026, 5, 11, tzinfo=UTC), "y"),
    ]
    collected = collect_newsletters(messages, APPROVED, *WINDOW, id_fn=_fake_id)
    assert [c.message_id for c in collected] == ["start", "mid", "end"]


def test_collected_fields_are_captured():
    messages = [IncomingMessage("m1", "Bob@B.example", _dt(3), "Bob's Brief #12")]
    [c] = collect_newsletters(messages, APPROVED, *WINDOW, id_fn=_fake_id)
    assert c.message_id == "m1"
    assert c.sender == "bob@b.example"  # normalized lowercase
    assert c.date_sent == _dt(3)
    assert c.subject == "Bob's Brief #12"
    assert c.issue_number == 12
    assert c.newsletter_id == _fake_id("bob@b.example", _dt(3).isoformat(), "Bob's Brief #12")


def test_sender_match_is_case_insensitive():
    messages = [IncomingMessage("m1", "ALICE@A.EXAMPLE", _dt(2), "hi")]
    collected = collect_newsletters(messages, APPROVED, *WINDOW, id_fn=_fake_id)
    assert len(collected) == 1


def test_non_approved_senders_ignored_even_in_window():
    messages = [IncomingMessage("m1", "nope@x.example", _dt(2), "in window")]
    assert collect_newsletters(messages, APPROVED, *WINDOW, id_fn=_fake_id) == []


def test_issue_number_none_when_absent():
    messages = [IncomingMessage("m1", "alice@a.example", _dt(2), "No number here")]
    [c] = collect_newsletters(messages, APPROVED, *WINDOW, id_fn=_fake_id)
    assert c.issue_number is None


def test_id_fn_is_used_for_newsletter_id():
    calls = []

    def spy(sender, date_sent, subject):
        calls.append((sender, date_sent, subject))
        return "fixed-id"

    messages = [IncomingMessage("m1", "alice@a.example", _dt(2), "Subj #3")]
    [c] = collect_newsletters(messages, APPROVED, *WINDOW, id_fn=spy)
    assert c.newsletter_id == "fixed-id"
    assert calls == [("alice@a.example", _dt(2).isoformat(), "Subj #3")]


def test_result_order_follows_input_order():
    messages = [
        IncomingMessage("m2", "alice@a.example", _dt(5), "second"),
        IncomingMessage("m1", "bob@b.example", _dt(2), "first"),
    ]
    collected = collect_newsletters(messages, APPROVED, *WINDOW, id_fn=_fake_id)
    assert [c.message_id for c in collected] == ["m2", "m1"]
