"""Tests for customer delivery notifications (SAT-252 / #16, Req 12).

Acceptance:
- After a successful delivery, a notification email is sent (no attachment needed).
- No notification is sent for a failed or empty (zero-newsletter) job.
"""

from dataclasses import dataclass

from substack_kindle.notify import DEFAULT_BODY, DEFAULT_SUBJECT, send_delivery_notification


@dataclass
class _Result:
    status: str
    outcome: str


class SendEmailSpy:
    def __init__(self):
        self.calls = []

    def __call__(self, *, to, subject, body):
        self.calls.append({"to": to, "subject": subject, "body": body})


def test_notifies_on_successful_delivery():
    spy = SendEmailSpy()
    sent = send_delivery_notification(
        _Result("succeeded", "delivered"), to="reader@example.com", send_email=spy
    )
    assert sent is True
    assert len(spy.calls) == 1
    assert spy.calls[0]["to"] == "reader@example.com"
    assert spy.calls[0]["subject"] == DEFAULT_SUBJECT
    assert spy.calls[0]["body"] == DEFAULT_BODY


def test_no_notification_for_empty_job():
    spy = SendEmailSpy()
    sent = send_delivery_notification(
        _Result("succeeded", "empty"), to="reader@example.com", send_email=spy
    )
    assert sent is False
    assert spy.calls == []


def test_no_notification_for_failed_job():
    spy = SendEmailSpy()
    sent = send_delivery_notification(
        _Result("failed", "error"), to="reader@example.com", send_email=spy
    )
    assert sent is False
    assert spy.calls == []


def test_notification_has_no_attachment_argument():
    # Req 12: the notification carries no attachment (MCP sendEmail is acceptable).
    spy = SendEmailSpy()
    send_delivery_notification(
        _Result("succeeded", "delivered"), to="reader@example.com", send_email=spy
    )
    assert set(spy.calls[0].keys()) == {"to", "subject", "body"}


def test_custom_subject_and_body_are_used():
    spy = SendEmailSpy()
    send_delivery_notification(
        _Result("succeeded", "delivered"),
        to="reader@example.com",
        send_email=spy,
        subject="Fresh reads on your Kindle",
        body="Your latest newsletters are ready.",
    )
    assert spy.calls[0]["subject"] == "Fresh reads on your Kindle"
    assert spy.calls[0]["body"] == "Your latest newsletters are ready."
