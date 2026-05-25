"""Tests for the onboarding test-delivery gate (SAT-255 / #19, Onboarding step 6).

Acceptance:
- A "send test document" action delivers a known test EPUB end-to-end.
- Scheduled runs cannot be enabled until a test delivery is confirmed.
- Catches the silent-drop failure mode (whitelist not set): a successful send is
  not enough — the customer must confirm receipt.
"""

import pytest

from substack_kindle.onboarding_verification import (
    KNOWN_TEST_DOCUMENT,
    DeliveryGate,
    confirm_test_delivery,
    enable_scheduled_runs,
    send_test_document,
)


class SendSpy:
    def __init__(self, error=None):
        self.calls = []
        self._error = error

    def __call__(self, *, epub_bytes, to, from_, filename):
        self.calls.append(
            {"epub_bytes": epub_bytes, "to": to, "from_": from_, "filename": filename}
        )
        if self._error:
            raise self._error
        return {"MessageID": "test-msg"}


def _send(gate, spy, **overrides):
    kwargs = dict(to="reader@kindle.com", from_="whitelist@system.example", send=spy)
    kwargs.update(overrides)
    return send_test_document(gate, **kwargs)


def test_known_test_document_is_non_empty():
    assert isinstance(KNOWN_TEST_DOCUMENT, bytes)
    assert KNOWN_TEST_DOCUMENT


def test_send_test_document_sends_known_epub():
    gate = DeliveryGate()
    spy = SendSpy()
    _send(gate, spy)
    assert gate.test_sent is True
    assert spy.calls[0]["epub_bytes"] == KNOWN_TEST_DOCUMENT
    assert spy.calls[0]["to"] == "reader@kindle.com"
    assert spy.calls[0]["from_"] == "whitelist@system.example"


def test_send_failure_propagates_and_does_not_mark_sent():
    gate = DeliveryGate()
    spy = SendSpy(error=RuntimeError("postmark rejected"))
    with pytest.raises(RuntimeError, match="postmark rejected"):
        _send(gate, spy)
    assert gate.test_sent is False  # failure surfaced, not a silent success


def test_scheduled_runs_blocked_until_confirmed():
    gate = DeliveryGate()
    with pytest.raises(ValueError):
        enable_scheduled_runs(gate)  # nothing sent yet


def test_send_success_alone_does_not_enable_scheduled_runs():
    # Catches the silent-drop mode: Postmark accepting the mail is not proof the
    # Kindle received it (whitelist may be unset) — confirmation is still required.
    gate = DeliveryGate()
    _send(gate, SendSpy())
    with pytest.raises(ValueError):
        enable_scheduled_runs(gate)
    assert gate.scheduled_runs_enabled is False


def test_confirm_requires_a_prior_send():
    gate = DeliveryGate()
    with pytest.raises(ValueError):
        confirm_test_delivery(gate)


def test_full_happy_path_enables_scheduled_runs():
    gate = DeliveryGate()
    _send(gate, SendSpy())
    confirm_test_delivery(gate)
    assert gate.test_confirmed is True
    enable_scheduled_runs(gate)
    assert gate.scheduled_runs_enabled is True
