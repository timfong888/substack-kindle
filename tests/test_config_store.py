"""Tests for the per-customer config store (SAT-237 / #1).

Acceptance:
- Config holds recipient_email, gmail_oauth_token (ref, not raw), kindle_email,
  newsletter_label, approved_sources[], whitelisting_status (confirmed/unconfirmed).
- whitelist_email is NOT stored per customer (single shared system value).
- Reads/writes are keyed by customer; two customers' rows never collide.
- No secret values appear in logs / repr.
"""

import dataclasses

import pytest

from substack_kindle.config_store import (
    CustomerConfig,
    InMemoryConfigStore,
    shared_whitelist_email,
)


def make_config(customer_id="cust-1", **overrides):
    base = dict(
        customer_id=customer_id,
        recipient_email=f"{customer_id}@gmail.com",
        kindle_email=f"{customer_id}@kindle.com",
        newsletter_label="Newsletters",
        gmail_oauth_token_ref="secretref://gmail/cust-1",
    )
    base.update(overrides)
    return CustomerConfig(**base)


def test_config_holds_required_fields():
    cfg = make_config()
    field_names = {f.name for f in dataclasses.fields(cfg)}
    assert {
        "recipient_email",
        "gmail_oauth_token_ref",
        "kindle_email",
        "newsletter_label",
        "approved_sources",
        "whitelisting_status",
    } <= field_names
    assert cfg.approved_sources == []
    assert cfg.whitelisting_status == "unconfirmed"


def test_no_raw_token_field_only_a_reference():
    cfg = make_config()
    field_names = {f.name for f in dataclasses.fields(cfg)}
    # The raw token must never be a stored field; only an opaque reference is.
    assert "gmail_oauth_token" not in field_names
    assert "gmail_oauth_token_ref" in field_names


def test_whitelisting_status_must_be_valid():
    with pytest.raises(ValueError):
        make_config(whitelisting_status="maybe")
    assert make_config(whitelisting_status="confirmed").whitelisting_status == "confirmed"


def test_whitelist_email_is_not_per_customer():
    cfg = make_config()
    field_names = {f.name for f in dataclasses.fields(cfg)}
    assert "whitelist_email" not in field_names


def test_shared_whitelist_email_comes_from_env(monkeypatch):
    monkeypatch.setenv("WHITELIST_EMAIL", "kindle-system@whitelist.example")
    assert shared_whitelist_email() == "kindle-system@whitelist.example"


def test_shared_whitelist_email_missing_raises(monkeypatch):
    monkeypatch.delenv("WHITELIST_EMAIL", raising=False)
    with pytest.raises(RuntimeError):
        shared_whitelist_email()


def test_repr_does_not_leak_token_ref():
    cfg = make_config(gmail_oauth_token_ref="secretref://super/secret/value")
    assert "super/secret/value" not in repr(cfg)
    assert "redacted" in repr(cfg).lower()


def test_store_is_keyed_by_customer_no_collision():
    store = InMemoryConfigStore()
    a = make_config(customer_id="alice")
    b = make_config(customer_id="bob", newsletter_label="Reads")
    store.put(a)
    store.put(b)
    assert store.get("alice").newsletter_label == "Newsletters"
    assert store.get("bob").newsletter_label == "Reads"
    assert len(store) == 2


def test_store_get_unknown_returns_none():
    store = InMemoryConfigStore()
    assert store.get("nobody") is None


def test_approved_sources_are_isolated_per_customer():
    store = InMemoryConfigStore()
    store.put(make_config(customer_id="alice"))
    store.put(make_config(customer_id="bob"))
    store.add_approved_source("alice", "news@substack.com")
    assert store.get("alice").approved_sources == ["news@substack.com"]
    assert store.get("bob").approved_sources == []


def test_add_approved_source_is_idempotent():
    store = InMemoryConfigStore()
    store.put(make_config(customer_id="alice"))
    store.add_approved_source("alice", "news@substack.com")
    store.add_approved_source("alice", "news@substack.com")
    assert store.get("alice").approved_sources == ["news@substack.com"]
