"""Safety-constrained Amazon approval-email handling (SAT-254 / Req 13).

SECURITY-CRITICAL. During onboarding, after the customer adds whitelist_email to
their Amazon approved-sender list, Amazon emails a confirmation link. This module
*detects* such a message and surfaces it for **one-tap customer confirmation** —
it never auto-clicks. Detection is gated hard:

1. Only inside the onboarding window (``window_open``).
2. Only if the message is verified to genuinely originate from Amazon — both a
   trusted Amazon sender domain AND an injected authenticity check (DKIM/SPF/ARC
   in production).
3. Only if it actually looks like an approval email and carries an Amazon-hosted
   approval link.

Anything failing these checks yields ``None`` (no action). The actual click only
happens via ``confirm_pending_approval``, which the customer triggers explicitly.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from email.utils import parseaddr
from typing import Any
from urllib.parse import urlsplit

# Trusted Amazon sender domains and approval-link hosts.
AMAZON_SENDER_DOMAINS = frozenset(
    {"amazon.com", "amazonses.com", "kindle.com", "amazon.co.uk", "amazon.ca"}
)
AMAZON_LINK_DOMAINS = AMAZON_SENDER_DOMAINS

_APPROVAL_HINTS = ("approve", "approval", "confirm", "verify")
_URL_RE = re.compile(r"""https?://[^\s"'<>]+""")


@dataclass
class InboundEmail:
    from_address: str
    subject: str
    body: str


@dataclass
class PendingApproval:
    approval_url: str
    from_address: str


def _domain_of(address: str) -> str:
    return parseaddr(address)[1].rsplit("@", 1)[-1].lower()


def _host_in(url: str, domains: frozenset[str]) -> bool:
    host = urlsplit(url).hostname or ""
    host = host.lower()
    return any(host == d or host.endswith("." + d) for d in domains)


def _looks_like_approval(message: InboundEmail) -> bool:
    haystack = f"{message.subject}\n{message.body}".lower()
    return any(hint in haystack for hint in _APPROVAL_HINTS)


def _amazon_approval_link(body: str) -> str | None:
    for url in _URL_RE.findall(body):
        if _host_in(url, AMAZON_LINK_DOMAINS):
            return url
    return None


def detect_pending_approval(
    message: InboundEmail,
    *,
    window_open: bool,
    is_authentic: Callable[[InboundEmail], bool],
) -> PendingApproval | None:
    """Surface a one-tap pending approval, or ``None`` if it must not be actioned.

    Never performs the click. Returns ``None`` outside the onboarding window, for
    non-Amazon or unauthenticated mail, or for anything that isn't a genuine
    Amazon approval with an Amazon-hosted link.
    """
    if not window_open:
        return None
    if _domain_of(message.from_address) not in AMAZON_SENDER_DOMAINS:
        return None
    if not is_authentic(message):
        return None
    if not _looks_like_approval(message):
        return None
    url = _amazon_approval_link(message.body)
    if url is None:
        return None
    return PendingApproval(approval_url=url, from_address=parseaddr(message.from_address)[1])


def confirm_pending_approval(
    pending: PendingApproval | None, *, click: Callable[[str], Any]
) -> Any:
    """Perform the approval click — the customer's explicit one-tap action."""
    if pending is None:
        raise ValueError("no pending approval to confirm")
    return click(pending.approval_url)
