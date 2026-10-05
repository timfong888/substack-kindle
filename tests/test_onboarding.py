"""Tests for the customer onboarding flow (SAT-253 / #17, Reqs 9, §Onboarding).

Acceptance:
- Flow: enter kindle_email -> display the shared whitelist_email with
  instructions + the Amazon discovery path.
"""

import pytest

from substack_kindle.onboarding import OnboardingFlow, OnboardingStep

WHITELIST = "kindle-system@whitelist.example"


def _flow():
    return OnboardingFlow(whitelist_email=WHITELIST)


def test_first_step_is_enter_kindle_email():
    assert _flow().next_step() is OnboardingStep.ENTER_KINDLE_EMAIL


def test_happy_path_reaches_done():
    flow = _flow()
    flow.set_kindle_email("reader@kindle.com")
    assert flow.next_step() is OnboardingStep.SHOW_WHITELIST
    instructions = flow.whitelist_instructions()
    assert flow.next_step() is OnboardingStep.DONE
    assert flow.is_complete()
    assert instructions.whitelist_email == WHITELIST


def test_whitelist_instructions_include_amazon_discovery_path():
    flow = _flow()
    flow.set_kindle_email("reader@kindle.com")
    instr = flow.whitelist_instructions()
    assert instr.whitelist_email == WHITELIST
    assert instr.amazon_discovery_path  # non-empty guidance to find the approved-sender list
    assert instr.instructions  # human-readable steps


def test_cannot_show_whitelist_before_kindle_set():
    flow = _flow()
    with pytest.raises(ValueError):
        flow.whitelist_instructions()


def test_empty_kindle_email_is_rejected():
    flow = _flow()
    with pytest.raises(ValueError):
        flow.set_kindle_email("")
    with pytest.raises(ValueError):
        flow.set_kindle_email("   ")
    assert flow.next_step() is OnboardingStep.ENTER_KINDLE_EMAIL


def test_whitespace_padded_kindle_email_is_stored_stripped():
    # A padded-but-valid address must be persisted without surrounding spaces,
    # or downstream delivery/Kindle address matching silently fails.
    flow = _flow()
    flow.set_kindle_email("  reader@kindle.com  ")
    assert flow.kindle_email == "reader@kindle.com"


def test_kindle_email_is_stored():
    flow = _flow()
    flow.set_kindle_email("reader@kindle.com")
    assert flow.kindle_email == "reader@kindle.com"


def test_not_complete_until_whitelist_shown():
    flow = _flow()
    flow.set_kindle_email("reader@kindle.com")
    assert flow.is_complete() is False
