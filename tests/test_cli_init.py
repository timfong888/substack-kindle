from __future__ import annotations

from substack_kindle.adapters.json_store import JsonConfigStore
from substack_kindle.cli import init_customer


def test_init_customer_creates_config_row(tmp_path):
    store = JsonConfigStore(tmp_path / "c.json")
    init_customer(
        store,
        customer_id="me",
        recipient_email="timfong888@gmail.com",
        kindle_email="timfong888@kindle.com",
        newsletter_label="Newsletters",
    )
    cfg = JsonConfigStore(tmp_path / "c.json").get("me")
    assert cfg is not None
    assert cfg.recipient_email == "timfong888@gmail.com"
    assert cfg.kindle_email == "timfong888@kindle.com"
    assert cfg.approved_sources == []


def test_init_customer_preserves_existing_approved_sources(tmp_path):
    store = JsonConfigStore(tmp_path / "c.json")
    init_customer(
        store,
        customer_id="me",
        recipient_email="r@x.com",
        kindle_email="k@kindle.com",
        newsletter_label="NL",
    )
    store.add_approved_source("me", "a@x.com")
    # Re-init (e.g. to rotate the kindle address) must not drop approved senders.
    init_customer(
        store,
        customer_id="me",
        recipient_email="r@x.com",
        kindle_email="k2@kindle.com",
        newsletter_label="NL",
    )
    cfg = store.get("me")
    assert cfg.kindle_email == "k2@kindle.com"
    assert cfg.approved_sources == ["a@x.com"]
