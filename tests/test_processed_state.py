"""Tests for the processed-state store (SAT-239 / #3, Req 17).

Acceptance:
- State keyed by the Req-6 newsletter ID (and/or Gmail message-id), in the
  service datastore — NOT Gmail labels.
- "Has this newsletter been delivered?" returns correct true/false.
- Backfill dedup (D3) reads from this same store (delivered_ids / filter helpers).
"""

from datetime import UTC, datetime

import pytest

from substack_kindle.processed_state import InMemoryProcessedStateStore, ProcessedState


def test_unknown_newsletter_is_not_delivered():
    store = InMemoryProcessedStateStore()
    assert store.is_delivered("nl-1") is False
    assert store.is_parsed("nl-1") is False


def test_mark_delivered_then_is_delivered_true():
    store = InMemoryProcessedStateStore()
    store.mark_delivered("nl-1")
    assert store.is_delivered("nl-1") is True


def test_state_is_keyed_by_newsletter_id():
    store = InMemoryProcessedStateStore()
    store.mark_delivered("nl-1")
    assert store.is_delivered("nl-1") is True
    assert store.is_delivered("nl-2") is False


def test_parsed_is_distinct_from_delivered():
    store = InMemoryProcessedStateStore()
    store.mark_parsed("nl-1")
    assert store.is_parsed("nl-1") is True
    assert store.is_delivered("nl-1") is False
    assert store.state_of("nl-1") is ProcessedState.PARSED

    store.mark_delivered("nl-1")
    assert store.is_delivered("nl-1") is True
    # Predicates report the exact state: once delivered it is no longer "parsed".
    assert store.is_parsed("nl-1") is False
    assert store.state_of("nl-1") is ProcessedState.DELIVERED


def test_mark_delivered_is_idempotent():
    store = InMemoryProcessedStateStore()
    store.mark_delivered("nl-1")
    store.mark_delivered("nl-1")
    assert store.is_delivered("nl-1") is True
    assert len(store.delivered_ids()) == 1


def test_records_delivered_timestamp():
    store = InMemoryProcessedStateStore()
    when = datetime(2026, 5, 1, 8, 0, tzinfo=UTC)
    store.mark_delivered("nl-1", delivered_at=when)
    assert store.delivered_at("nl-1") == when


def test_secondary_lookup_by_gmail_message_id():
    store = InMemoryProcessedStateStore()
    store.mark_delivered("nl-1", gmail_message_id="gmail-abc")
    assert store.is_message_delivered("gmail-abc") is True
    assert store.is_message_delivered("gmail-xyz") is False


def test_superseded_gmail_message_id_is_not_a_stale_positive():
    store = InMemoryProcessedStateStore()
    store.mark_delivered("nl-1", gmail_message_id="gmail-old")
    store.mark_delivered("nl-1", gmail_message_id="gmail-new")
    assert store.is_message_delivered("gmail-new") is True
    assert store.is_message_delivered("gmail-old") is False


def test_delivered_ids_returns_only_delivered():
    store = InMemoryProcessedStateStore()
    store.mark_parsed("nl-parsed")
    store.mark_delivered("nl-done-1")
    store.mark_delivered("nl-done-2")
    assert store.delivered_ids() == {"nl-done-1", "nl-done-2"}


def test_filter_undelivered_supports_dedup():
    # D3 (backfill dedup) reads from this same store.
    store = InMemoryProcessedStateStore()
    store.mark_delivered("nl-1")
    candidates = ["nl-1", "nl-2", "nl-3"]
    assert store.filter_undelivered(candidates) == ["nl-2", "nl-3"]


def test_filter_undelivered_preserves_order_and_dups_input_unchanged():
    store = InMemoryProcessedStateStore()
    store.mark_delivered("nl-2")
    candidates = ["nl-3", "nl-2", "nl-1"]
    assert store.filter_undelivered(candidates) == ["nl-3", "nl-1"]
    assert candidates == ["nl-3", "nl-2", "nl-1"]


def test_no_gmail_coupling_in_module():
    # The store is service-side state, never Gmail labels: it must not import Gmail/Google clients.
    import substack_kindle.processed_state as mod

    with open(mod.__file__) as fh:
        text = fh.read().lower()
    assert "googleapiclient" not in text
    assert "from google" not in text
    assert "import google" not in text


@pytest.mark.parametrize("nid", ["", "nl-1", "a" * 64])
def test_accepts_arbitrary_id_strings(nid):
    store = InMemoryProcessedStateStore()
    store.mark_delivered(nid)
    assert store.is_delivered(nid) is True
