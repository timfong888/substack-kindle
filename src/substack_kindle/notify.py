"""Customer delivery notification (SAT-252 / Req 12).

After a job successfully delivers an EPUB to the customer's Kindle, send a plain
notification email (no attachment — the MCP send-email path is fine). Nothing is
sent for a failed job or an empty (zero-newsletter) job, so the customer is only
pinged when there is genuinely a new update. The email sender is injected.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

DEFAULT_SUBJECT = "New newsletters on your Kindle"
DEFAULT_BODY = "Your latest newsletters have been delivered to your Kindle."


class DeliveryResult(Protocol):
    status: str
    outcome: str


def send_delivery_notification(
    result: DeliveryResult,
    *,
    to: str,
    send_email: Callable[..., object],
    subject: str = DEFAULT_SUBJECT,
    body: str = DEFAULT_BODY,
) -> bool:
    """Notify the customer iff the job actually delivered.

    Returns ``True`` when a notification was sent. The injected ``send_email`` is
    expected to raise on failure (the project's send convention), so a returned
    ``True`` means the send completed without error; ``False`` means no
    notification was warranted (a failed or empty job).
    """
    if result.status != "succeeded" or result.outcome != "delivered":
        return False
    send_email(to=to, subject=subject, body=body)
    return True
