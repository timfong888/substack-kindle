"""Onboarding test-delivery gate (SAT-255 / Onboarding step 6).

Before scheduled runs are enabled, the customer sends a known test document
end-to-end and confirms it arrived on their Kindle. A successful Postmark send is
not sufficient — Amazon silently drops mail from a sender that isn't on the
approved list, so only an explicit customer confirmation unlocks scheduled runs.
The send function is injected (no live calls).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# A small, known placeholder document used for the end-to-end test send. In
# production this is a real minimal EPUB built by the C2/C3 builders.
KNOWN_TEST_DOCUMENT = b"substack-kindle test document\n"
TEST_FILENAME = "substack-kindle-test.epub"


@dataclass
class DeliveryGate:
    test_sent: bool = False
    test_confirmed: bool = False
    scheduled_runs_enabled: bool = False


def send_test_document(
    gate: DeliveryGate,
    *,
    to: str,
    from_: str,
    send: Callable[..., Any],
    document: bytes = KNOWN_TEST_DOCUMENT,
    filename: str = TEST_FILENAME,
) -> Any:
    """Send the known test document via the injected sender.

    ``gate.test_sent`` is only set once the send returns without error, so a
    delivery failure surfaces rather than masquerading as success.
    """
    result = send(epub_bytes=document, to=to, from_=from_, filename=filename)
    gate.test_sent = True
    return result


def confirm_test_delivery(gate: DeliveryGate) -> None:
    """Record the customer's confirmation that the test document reached the Kindle."""
    if not gate.test_sent:
        raise ValueError("send a test document before confirming delivery")
    gate.test_confirmed = True


def enable_scheduled_runs(gate: DeliveryGate) -> None:
    """Enable scheduled runs — only after a confirmed test delivery."""
    if not gate.test_confirmed:
        raise ValueError("a confirmed test delivery is required before enabling scheduled runs")
    gate.scheduled_runs_enabled = True
