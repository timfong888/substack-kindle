"""Sender registration via the Gmail label gesture (SAT-242 / Reqs §Label gesture).

When a customer labels a newsletter, its sender is registered in approved_sources.
Only the message's ``From`` header is read — never the body — and only read-only
Gmail access is used: the label is never removed and is never repurposed to mean
"processed". A sender already approved is not duplicated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from email.utils import parseaddr
from typing import Protocol


@dataclass
class EmailMessage:
    """A Gmail message as seen through read-only access (headers + label ids)."""

    message_id: str
    headers: dict[str, str] = field(default_factory=dict)
    label_ids: list[str] = field(default_factory=list)


class GmailReadOnlyClient(Protocol):
    """Read-only Gmail surface: list messages carrying a label. No mutators."""

    def messages_with_label(self, label: str) -> list[EmailMessage]: ...


def _header(message: EmailMessage, name: str) -> str | None:
    target = name.lower()
    for key, value in message.headers.items():
        if key.lower() == target:
            return value
    return None


def sender_of(message: EmailMessage) -> str | None:
    """Return the lowercased email address from the message's ``From`` header."""
    raw = _header(message, "From")
    if not raw:
        return None
    address = parseaddr(raw)[1]
    return address.lower() or None


def register_senders_from_label(
    client: GmailReadOnlyClient,
    label: str,
    approved_sources: list[str] | None = None,
) -> list[str]:
    """Register the senders of all messages carrying ``label`` into approved_sources.

    Returns a new list (the caller's list is not mutated); order is preserved and
    senders are de-duplicated case-insensitively.
    """
    approved = list(approved_sources or [])
    seen = {s.lower() for s in approved}
    for message in client.messages_with_label(label):
        sender = sender_of(message)
        if sender and sender not in seen:
            approved.append(sender)
            seen.add(sender)
    return approved
