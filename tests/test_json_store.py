from __future__ import annotations

from pathlib import Path

from substack_kindle.adapters.json_store import JsonConfigStore, JsonProcessedStateStore
from substack_kindle.config_store import CustomerConfig


def test_config_roundtrips_and_unions_sources(tmp_path: Path):
    store = JsonConfigStore(tmp_path / "config.json")
    store.put(
        CustomerConfig(
            "me",
            "r@x.com",
            "k@kindle.com",
            "Newsletters",
            "secretref://t",
            approved_sources=["a@x.com"],
        )
    )
    store.add_approved_source("me", "b@x.com")
    store.add_approved_source("me", "a@x.com")  # idempotent
    reloaded = JsonConfigStore(tmp_path / "config.json")
    cfg = reloaded.get("me")
    assert cfg.approved_sources == ["a@x.com", "b@x.com"]


def test_processed_state_persists_delivered(tmp_path: Path):
    store = JsonProcessedStateStore(tmp_path / "state.json")
    assert store.is_delivered("n1") is False
    store.mark_delivered("n1", gmail_message_id="m1")
    assert JsonProcessedStateStore(tmp_path / "state.json").is_delivered("n1") is True
