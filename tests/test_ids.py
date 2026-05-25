"""Tests for the reusable newsletter-ID hash function and its source mapping.

SAT-238 (A2) / Req 6: one deterministic function produces every newsletter ID,
and every ID resolves back to its (sender, date_sent, subject) source values.
"""

from datetime import datetime

import pytest

from substack_kindle.ids import IdRegistry, NewsletterRef, newsletter_id

SENDER = "The Pragmatic Engineer <gergely@pragmaticengineer.com>"
DATE = "2026-05-20T09:00:00+00:00"
SUBJECT = "Issue 142: Scaling teams"


def test_same_inputs_always_yield_same_id():
    assert newsletter_id(SENDER, DATE, SUBJECT) == newsletter_id(SENDER, DATE, SUBJECT)


def test_id_is_nonempty_string():
    nid = newsletter_id(SENDER, DATE, SUBJECT)
    assert isinstance(nid, str)
    assert nid


@pytest.mark.parametrize(
    "sender,date,subject",
    [
        ("other@example.com", DATE, SUBJECT),
        (SENDER, "2026-05-21T09:00:00+00:00", SUBJECT),
        (SENDER, DATE, "Issue 143: Scaling teams"),
    ],
)
def test_different_inputs_yield_different_ids(sender, date, subject):
    assert newsletter_id(sender, date, subject) != newsletter_id(SENDER, DATE, SUBJECT)


def test_near_identical_subjects_collide_resistant():
    a = newsletter_id(SENDER, DATE, "Issue 142")
    b = newsletter_id(SENDER, DATE, "Issue 142 ")  # one trailing space distinguishes
    # whitespace-only difference is normalized away -> same ID
    assert a == b
    c = newsletter_id(SENDER, DATE, "Issue 143")
    assert a != c


def test_datetime_and_isostring_dates_are_equivalent():
    as_str = newsletter_id(SENDER, DATE, SUBJECT)
    as_dt = newsletter_id(SENDER, datetime.fromisoformat(DATE), SUBJECT)
    assert as_str == as_dt


@pytest.mark.parametrize(
    "equivalent_date",
    [
        "2026-05-20T09:00:00Z",  # "Z" UTC suffix
        "2026-05-20T04:00:00-05:00",  # same instant, different offset
        "20 May 2026 09:00:00 +0000",  # RFC 2822 (email Date: header form)
        datetime.fromisoformat(DATE),  # aware datetime object
    ],
)
def test_equivalent_instants_collapse_to_one_id(equivalent_date):
    # Req 6 dedup guarantee: the same instant in any recognized notation -> one ID.
    assert newsletter_id(SENDER, equivalent_date, SUBJECT) == newsletter_id(
        SENDER, DATE, SUBJECT
    )


def test_naive_datetime_is_assumed_utc():
    # A naive datetime (e.g. datetime.utcnow()) must collapse to the same ID as
    # the equivalent UTC-aware value, or the dedup guarantee silently breaks.
    naive = datetime(2026, 5, 20, 9, 0, 0)
    assert newsletter_id(SENDER, naive, SUBJECT) == newsletter_id(SENDER, DATE, SUBJECT)


def test_unparseable_date_is_stable_and_does_not_raise():
    a = newsletter_id(SENDER, "not-a-date", SUBJECT)
    b = newsletter_id(SENDER, "not-a-date", SUBJECT)
    assert a == b
    assert a != newsletter_id(SENDER, DATE, SUBJECT)


def test_registry_resolves_id_back_to_source():
    reg = IdRegistry()
    nid = reg.register(SENDER, DATE, SUBJECT)
    ref = reg.resolve(nid)
    assert isinstance(ref, NewsletterRef)
    assert ref.sender == SENDER
    assert ref.subject == SUBJECT
    assert DATE[:10] in ref.date_sent


def test_registry_register_matches_bare_function():
    reg = IdRegistry()
    assert reg.register(SENDER, DATE, SUBJECT) == newsletter_id(SENDER, DATE, SUBJECT)


def test_registry_register_is_idempotent():
    reg = IdRegistry()
    first = reg.register(SENDER, DATE, SUBJECT)
    second = reg.register(SENDER, DATE, SUBJECT)
    assert first == second
    assert len(reg) == 1


def test_registry_resolve_unknown_id_raises():
    reg = IdRegistry()
    with pytest.raises(KeyError):
        reg.resolve("does-not-exist")
