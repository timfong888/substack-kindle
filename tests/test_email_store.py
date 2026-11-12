"""Tests for the swappable email store (SAT-1104, docs/design/intake-mailbox.md §5).

``InMemoryEmailStore`` is the reference implementation of the ``EmailStore``
protocol; every backend (Convex now, Supabase or Parquet later) must pass this
same suite, so the tests are written against the protocol only.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from substack_kindle.email_store import (
    DeliveryTarget,
    EmailStore,
    InMemoryEmailStore,
    StoredEmail,
)

T0 = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


def _email(**overrides) -> StoredEmail:
    base = dict(
        user_code="tim-ab12cd34",
        gmail_message_id="1a154ce366ce6029",
        message_id="<20261019153103.3.3227308d743d5e0d@mg-d1.substack.com>",
        from_address="bytebytego@substack.com",
        from_name="ByteByteGo",
        publication="bytebytego.substack.com",
        post_url="https://blog.bytebytego.com/p/how-uber-built-a-genie",
        subject="How Uber Built a Genie to Answer Questions Correctly",
        received_at=T0,
        kind="newsletter",
        substack_user_id="3002150",
        markdown="# How Uber Built a Genie\n\nBody.",
        stored_at=T0 + timedelta(hours=1),
    )
    base.update(overrides)
    return StoredEmail(**base)


TARGETS = [
    DeliveryTarget(user_code="tim-ab12cd34", kindle_email="reader@kindle.com"),
    DeliveryTarget(user_code="pending-user", kindle_email=None),
]


@pytest.fixture
def store() -> EmailStore:
    """The backend under test. Another backend overrides this fixture (and seeds
    ``TARGETS`` into its users table) to run the same conformance suite."""
    return InMemoryEmailStore(targets=TARGETS)


# ---------------------------------------------------------------- put / exists


def test_put_then_exists(store):
    assert store.exists("1a154ce366ce6029") is False
    store.put(_email())
    assert store.exists("1a154ce366ce6029") is True


def test_put_is_idempotent_on_gmail_message_id(store):
    store.put(_email())
    store.put(_email(subject="edited"))  # a re-run after a crash between put and label
    rows = store.undelivered(
        "tim-ab12cd34", since=T0 - timedelta(days=1), until=T0 + timedelta(days=1)
    )
    assert [r.subject for r in rows] == ["How Uber Built a Genie to Answer Questions Correctly"]


def test_markdown_bytes_is_derived_from_markdown():
    email = _email(markdown="héllo")
    assert email.markdown_bytes == len("héllo".encode())
    assert _email(markdown=None).markdown_bytes == 0


# ---------------------------------------------------------------- undelivered


def test_undelivered_is_oldest_first_over_a_half_open_window(store):
    store.put(_email(gmail_message_id="b", received_at=T0 + timedelta(hours=2), subject="second"))
    store.put(_email(gmail_message_id="a", received_at=T0, subject="first"))
    store.put(_email(gmail_message_id="c", received_at=T0 + timedelta(days=1), subject="boundary"))
    # [since, until): the start is included, the end excluded, so back-to-back daily
    # windows never select the same email twice.
    day1 = store.undelivered("tim-ab12cd34", since=T0, until=T0 + timedelta(days=1))
    day2 = store.undelivered(
        "tim-ab12cd34", since=T0 + timedelta(days=1), until=T0 + timedelta(days=2)
    )
    assert [r.subject for r in day1] == ["first", "second"]
    assert [r.subject for r in day2] == ["boundary"]


def test_stored_email_rejects_naive_datetimes():
    with pytest.raises(ValueError, match="received_at"):
        _email(received_at=datetime(2026, 10, 19, 12, 0))
    with pytest.raises(ValueError, match="stored_at"):
        _email(stored_at=datetime(2026, 10, 19, 13, 0))


def test_undelivered_excludes_other_users(store):
    store.put(_email(gmail_message_id="a"))
    store.put(_email(gmail_message_id="b", user_code="someone-else"))
    rows = store.undelivered(
        "tim-ab12cd34", since=T0 - timedelta(days=1), until=T0 + timedelta(days=1)
    )
    assert [r.gmail_message_id for r in rows] == ["a"]


def test_undelivered_excludes_non_newsletters(store):
    store.put(_email(gmail_message_id="a"))
    store.put(_email(gmail_message_id="n", kind="notification", markdown=None))
    store.put(_email(gmail_message_id="r", kind="rejected", markdown=None))
    store.put(_email(gmail_message_id="acct", kind="account", markdown=None))
    rows = store.undelivered(
        "tim-ab12cd34", since=T0 - timedelta(days=1), until=T0 + timedelta(days=1)
    )
    assert [r.gmail_message_id for r in rows] == ["a"]


def test_undelivered_excludes_delivered(store):
    store.put(_email(gmail_message_id="a"))
    store.put(_email(gmail_message_id="b", received_at=T0 + timedelta(hours=1)))
    store.mark_delivered(["a"], delivered_at=T0 + timedelta(days=1))
    rows = store.undelivered(
        "tim-ab12cd34", since=T0 - timedelta(days=1), until=T0 + timedelta(days=1)
    )
    assert [r.gmail_message_id for r in rows] == ["b"]


def test_undelivered_rejects_naive_or_inverted_window(store):
    with pytest.raises(ValueError, match="timezone-aware"):
        store.undelivered("tim-ab12cd34", since=datetime(2026, 10, 1), until=T0)
    with pytest.raises(ValueError, match="since"):
        store.undelivered("tim-ab12cd34", since=T0 + timedelta(days=1), until=T0)


# ---------------------------------------------------------------- mark_delivered


def test_mark_delivered_is_idempotent_and_ignores_unknown_ids(store):
    store.put(_email(gmail_message_id="a"))
    first = T0 + timedelta(days=1)
    store.mark_delivered(["a", "missing"], delivered_at=first)
    store.mark_delivered(["a"], delivered_at=T0 + timedelta(days=2))
    rows = store.undelivered(
        "tim-ab12cd34", since=T0 - timedelta(days=1), until=T0 + timedelta(days=1)
    )
    assert rows == []
    # The first delivery time is kept: it is what retention counts from.
    assert store.get("a").delivered_at == first
    assert store.get("missing") is None


def test_mark_delivered_rejects_naive_timestamp(store):
    store.put(_email(gmail_message_id="a"))
    with pytest.raises(ValueError, match="timezone-aware"):
        store.mark_delivered(["a"], delivered_at=datetime(2026, 10, 20))


# ---------------------------------------------------------------- delivery targets


def test_delivery_targets_lists_users_with_a_kindle_address_only(store):
    assert store.delivery_targets() == [
        DeliveryTarget(user_code="tim-ab12cd34", kindle_email="reader@kindle.com")
    ]


# ---------------------------------------------------------------- purge


def test_purge_keys_on_delivery_time_and_keeps_metadata(store):
    store.put(_email(gmail_message_id="old", received_at=T0 - timedelta(days=40)))
    store.put(_email(gmail_message_id="recent", received_at=T0 - timedelta(days=5)))
    # Received long ago but delivered late (backfill): retention counts from delivery.
    store.put(_email(gmail_message_id="late", received_at=T0 - timedelta(days=40)))
    store.put(_email(gmail_message_id="undelivered-old", received_at=T0 - timedelta(days=40)))
    store.mark_delivered(["old"], delivered_at=T0 - timedelta(days=39))
    store.mark_delivered(["recent", "late"], delivered_at=T0 - timedelta(days=4))

    purged = store.purge_bodies_older_than(T0 - timedelta(days=30))

    assert purged == 1
    old = store.get("old")
    assert old is not None and old.markdown is None and old.subject  # metadata kept
    assert old.markdown_bytes == 0  # the byte count describes the remaining body
    assert old.delivered_at == T0 - timedelta(days=39)
    assert store.get("recent").markdown is not None
    assert store.get("late").markdown is not None
    # Never purge what has not been delivered yet, however old.
    assert store.get("undelivered-old").markdown is not None


def test_purge_is_idempotent(store):
    store.put(_email(gmail_message_id="old", received_at=T0 - timedelta(days=40)))
    store.mark_delivered(["old"], delivered_at=T0 - timedelta(days=39))
    assert store.purge_bodies_older_than(T0 - timedelta(days=30)) == 1
    assert store.purge_bodies_older_than(T0 - timedelta(days=30)) == 0


# ---------------------------------------------------------------- protocol


def test_in_memory_store_satisfies_the_protocol():
    assert isinstance(InMemoryEmailStore(), EmailStore)
