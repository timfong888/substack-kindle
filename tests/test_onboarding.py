"""Tests for the customer onboarding flow (SAT-253 / #17, Reqs 9, §Onboarding).

Acceptance:
- Flow: connect read-only Gmail -> enter kindle_email -> display the shared
  whitelist_email with instructions + the Amazon discovery path.
- Customer seeds approved_sources by labelling existing newsletters (B2).
"""

import pytest

from substack_kindle.onboarding import OnboardingFlow, OnboardingStep

WHITELIST = "kindle-system@whitelist.example"


def _flow(register_result=("news@substack.com",)):
    return OnboardingFlow(
        whitelist_email=WHITELIST,
        connect_gmail=lambda: "gmail-client",
        register_senders=lambda label: list(register_result),
    )


def test_first_step_is_connect_gmail():
    assert _flow().next_step() is OnboardingStep.CONNECT_GMAIL


def test_happy_path_reaches_done():
    flow = _flow()
    flow.connect_gmail()
    assert flow.next_step() is OnboardingStep.ENTER_KINDLE_EMAIL
    flow.set_kindle_email("reader@kindle.com")
    assert flow.next_step() is OnboardingStep.SHOW_WHITELIST
    instructions = flow.whitelist_instructions()
    assert flow.next_step() is OnboardingStep.SEED_SOURCES
    flow.seed_sources(label="Newsletters")
    assert flow.next_step() is OnboardingStep.DONE
    assert flow.is_complete()
    assert instructions.whitelist_email == WHITELIST


def test_whitelist_instructions_include_amazon_discovery_path():
    flow = _flow()
    flow.connect_gmail()
    flow.set_kindle_email("reader@kindle.com")
    instr = flow.whitelist_instructions()
    assert instr.whitelist_email == WHITELIST
    assert instr.amazon_discovery_path  # non-empty guidance to find the approved-sender list
    assert instr.instructions  # human-readable steps


def test_cannot_set_kindle_before_connecting_gmail():
    flow = _flow()
    with pytest.raises(ValueError):
        flow.set_kindle_email("reader@kindle.com")


def test_cannot_show_whitelist_before_kindle_set():
    flow = _flow()
    flow.connect_gmail()
    with pytest.raises(ValueError):
        flow.whitelist_instructions()


def test_empty_kindle_email_is_rejected():
    flow = _flow()
    flow.connect_gmail()
    with pytest.raises(ValueError):
        flow.set_kindle_email("")
    with pytest.raises(ValueError):
        flow.set_kindle_email("   ")
    assert flow.next_step() is OnboardingStep.ENTER_KINDLE_EMAIL


def test_whitespace_padded_kindle_email_is_stored_stripped():
    # A padded-but-valid address must be persisted without surrounding spaces,
    # or downstream delivery/Kindle address matching silently fails.
    flow = _flow()
    flow.connect_gmail()
    flow.set_kindle_email("  reader@kindle.com  ")
    assert flow.kindle_email == "reader@kindle.com"


def test_seed_with_zero_sources_does_not_complete_onboarding():
    flow = OnboardingFlow(
        whitelist_email=WHITELIST,
        connect_gmail=lambda: "gmail-client",
        register_senders=lambda label: [],  # label matched no senders
    )
    flow.connect_gmail()
    flow.set_kindle_email("reader@kindle.com")
    flow.whitelist_instructions()
    with pytest.raises(ValueError):
        flow.seed_sources(label="Newsletters")
    assert flow.is_complete() is False


def test_seed_sources_uses_label_gesture_and_records_sources():
    flow = _flow(register_result=("a@x.example", "b@y.example"))
    flow.connect_gmail()
    flow.set_kindle_email("reader@kindle.com")
    flow.whitelist_instructions()
    sources = flow.seed_sources(label="Newsletters")
    assert sources == ["a@x.example", "b@y.example"]
    assert flow.approved_sources == ["a@x.example", "b@y.example"]


def test_cannot_seed_sources_before_whitelist_shown():
    flow = _flow()
    flow.connect_gmail()
    flow.set_kindle_email("reader@kindle.com")
    with pytest.raises(ValueError):
        flow.seed_sources(label="Newsletters")


def test_kindle_email_is_stored():
    flow = _flow()
    flow.connect_gmail()
    flow.set_kindle_email("reader@kindle.com")
    assert flow.kindle_email == "reader@kindle.com"


def test_not_complete_until_sources_seeded():
    flow = _flow()
    flow.connect_gmail()
    flow.set_kindle_email("reader@kindle.com")
    flow.whitelist_instructions()
    assert flow.is_complete() is False
