"""Tests for within-job deduplication (SAT-249 / #13, Req 17).

Acceptance:
- Given newsletters already in the processed-state store (A3), a new job
  (scheduled or backfill) excludes them.
- Dedup behaves identically regardless of trigger type.
"""

from dataclasses import dataclass

from substack_kindle.dedup import deduplicate


@dataclass
class _Item:
    newsletter_id: str


class FakeDeliveredStore:
    """Stand-in for the A3 processed-state store: knows what's been delivered."""

    def __init__(self, delivered):
        self._delivered = set(delivered)

    def is_delivered(self, newsletter_id):
        return newsletter_id in self._delivered


def test_excludes_already_delivered():
    store = FakeDeliveredStore({"b"})
    items = [_Item("a"), _Item("b"), _Item("c")]
    kept = deduplicate(items, store.is_delivered)
    assert [i.newsletter_id for i in kept] == ["a", "c"]


def test_nothing_delivered_keeps_all():
    store = FakeDeliveredStore(set())
    items = [_Item("a"), _Item("b")]
    assert deduplicate(items, store.is_delivered) == items


def test_collapses_intra_batch_duplicates():
    store = FakeDeliveredStore(set())
    items = [_Item("a"), _Item("a"), _Item("b")]
    kept = deduplicate(items, store.is_delivered)
    assert [i.newsletter_id for i in kept] == ["a", "b"]


def test_order_is_preserved():
    store = FakeDeliveredStore({"x"})
    items = [_Item("c"), _Item("x"), _Item("a"), _Item("b")]
    kept = deduplicate(items, store.is_delivered)
    assert [i.newsletter_id for i in kept] == ["c", "a", "b"]


def test_empty_input():
    assert deduplicate([], FakeDeliveredStore(set()).is_delivered) == []


def test_dedup_has_no_trigger_parameter():
    # Trigger-independence is structural: dedup must not branch on trigger at all,
    # so its signature never gains a trigger parameter.
    import inspect

    assert "trigger" not in inspect.signature(deduplicate).parameters


def test_repeated_delivered_id_checked_only_once():
    calls = []

    def counting_is_delivered(nid):
        calls.append(nid)
        return nid == "b"

    items = [_Item("b"), _Item("b"), _Item("a"), _Item("a")]
    kept = deduplicate(items, counting_is_delivered)
    assert [i.newsletter_id for i in kept] == ["a"]
    # Each unique ID is checked at most once, even when it repeats in the batch.
    assert sorted(calls) == ["a", "b"]


def test_custom_key_supports_plain_id_strings():
    store = FakeDeliveredStore({"b"})
    kept = deduplicate(["a", "b", "c"], store.is_delivered, key=lambda s: s)
    assert kept == ["a", "c"]


def test_does_not_mutate_input():
    store = FakeDeliveredStore({"b"})
    items = [_Item("a"), _Item("b")]
    snapshot = list(items)
    deduplicate(items, store.is_delivered)
    assert items == snapshot
