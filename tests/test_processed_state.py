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


# ---------------------------------------------------------------------------
# SAT-284: JsonFileProcessedStateStore
# ---------------------------------------------------------------------------

import threading  # noqa: E402 (grouped under the section header above)

from substack_kindle.processed_state import (  # noqa: E402
    JsonFileProcessedStateStore,
)


def test_json_file_store_returns_not_delivered_when_file_absent(tmp_path):
    store = JsonFileProcessedStateStore(tmp_path / "state.json")
    assert store.is_delivered("nl-1") is False


def test_json_file_store_persists_across_instances(tmp_path):
    """mark_delivered in one instance must be visible to a new instance on the same file."""
    path = tmp_path / "state.json"
    JsonFileProcessedStateStore(path).mark_delivered("nl-1")
    assert JsonFileProcessedStateStore(path).is_delivered("nl-1") is True


def test_json_file_store_unknown_id_is_not_delivered_after_other_deliveries(tmp_path):
    path = tmp_path / "state.json"
    store = JsonFileProcessedStateStore(path)
    store.mark_delivered("nl-1")
    assert store.is_delivered("nl-2") is False


def test_json_file_store_creates_valid_versioned_json(tmp_path):
    """The file must have version=1 and a records dict after first write."""
    import json
    path = tmp_path / "state.json"
    JsonFileProcessedStateStore(path).mark_delivered("nl-1", sender="a@b.com", subject="Hi")
    data = json.loads(path.read_text())
    assert data["version"] == 1
    assert "nl-1" in data["records"]
    assert data["records"]["nl-1"]["state"] == "delivered"
    assert data["records"]["nl-1"]["sender"] == "a@b.com"


def test_json_file_store_mark_delivered_is_idempotent(tmp_path):
    path = tmp_path / "state.json"
    store = JsonFileProcessedStateStore(path)
    store.mark_delivered("nl-1")
    store.mark_delivered("nl-1")
    assert store.is_delivered("nl-1") is True
    assert len(store.delivered_ids()) == 1


def test_json_file_store_filter_undelivered(tmp_path):
    path = tmp_path / "state.json"
    store = JsonFileProcessedStateStore(path)
    store.mark_delivered("nl-1")
    assert store.filter_undelivered(["nl-1", "nl-2", "nl-3"]) == ["nl-2", "nl-3"]


def test_json_file_store_delivered_ids(tmp_path):
    path = tmp_path / "state.json"
    store = JsonFileProcessedStateStore(path)
    store.mark_delivered("nl-1")
    store.mark_delivered("nl-2")
    assert store.delivered_ids() == {"nl-1", "nl-2"}


def test_json_file_store_write_leaves_valid_json_on_disk(tmp_path):
    """os.replace atomicity: the file must always be valid JSON (never a partial write)."""
    import json
    path = tmp_path / "state.json"
    store = JsonFileProcessedStateStore(path)
    for i in range(20):
        store.mark_delivered(f"nl-{i}")
    data = json.loads(path.read_text())
    assert data["version"] == 1
    assert len(data["records"]) == 20


def test_json_file_store_concurrent_writes_do_not_corrupt(tmp_path):
    """Two threads writing simultaneously must not corrupt the file."""
    import json
    path = tmp_path / "state.json"
    errors: list[Exception] = []

    def write_batch(start: int) -> None:
        store = JsonFileProcessedStateStore(path)
        for i in range(start, start + 20):
            try:
                store.mark_delivered(f"nl-{i}")
            except Exception as exc:
                errors.append(exc)

    t1 = threading.Thread(target=write_batch, args=(0,))
    t2 = threading.Thread(target=write_batch, args=(20,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert errors == [], f"exceptions during concurrent writes: {errors}"
    data = json.loads(path.read_text())
    assert data["version"] == 1
    # All 40 IDs must be present (no record lost to a race).
    assert len(data["records"]) == 40


# ---------------------------------------------------------------------------
# SAT-284: ProcessedStateStore protocol
# ---------------------------------------------------------------------------

from substack_kindle.processed_state import ProcessedStateStore  # noqa: E402


def test_in_memory_store_satisfies_protocol():
    """InMemoryProcessedStateStore must satisfy the ProcessedStateStore protocol."""
    store = InMemoryProcessedStateStore()
    assert isinstance(store, ProcessedStateStore)


def test_json_file_store_satisfies_protocol(tmp_path):
    """JsonFileProcessedStateStore must satisfy the ProcessedStateStore protocol."""
    store = JsonFileProcessedStateStore(tmp_path / "state.json")
    assert isinstance(store, ProcessedStateStore)
