from __future__ import annotations

from substack_kindle.adapters.json_store import JsonConfigStore
from substack_kindle.cli import seed_senders
from substack_kindle.config_store import CustomerConfig


def test_seed_senders_dedups_and_lowercases(tmp_path):
    f = tmp_path / "senders.md"
    f.write_text("A@X.com\n\nb@x.com\nA@x.com\n", encoding="utf-8")
    store = JsonConfigStore(tmp_path / "c.json")
    store.put(CustomerConfig("me", "r@x.com", "k@kindle.com", "NL", "secretref://t"))
    added = seed_senders(f, "me", store)
    assert added == ["a@x.com", "b@x.com"]
    assert store.get("me").approved_sources == ["a@x.com", "b@x.com"]
