"""Refuse to run when the verified sender collides with the customer's Kindle alias.

Amazon's documented Send-to-Kindle approved-sender list bypasses per-document
verification — but a long-standing Amazon bug, reported on Mobileread and
elsewhere since 2019, fires the verification email anyway when the sending
address's local-part exactly equals the customer's Kindle address local-part.
See SAT-270 research for the full empirical picture.

The pipeline therefore refuses to start when ``WHITELIST_EMAIL`` and the
customer's ``KINDLE_EMAIL`` share a local-part — failing loudly is far better
than quietly hitting the verification trap on every send.
"""

from __future__ import annotations


class LocalPartCollision(Exception):
    """Raised when the sender and Kindle local-parts match (Amazon bug trap)."""


def _split(address: str) -> tuple[str, str]:
    if "@" not in address:
        raise ValueError(f"{address!r} is not a valid email (missing '@')")
    local, _, domain = address.partition("@")
    if not local or not domain:
        raise ValueError(f"{address!r} is not a valid email (empty local-part or domain)")
    return local, domain


def ensure_distinct_local_parts(*, whitelist_email: str, kindle_email: str) -> None:
    """Raise ``LocalPartCollision`` if the two local-parts match (case-insensitive)."""
    sender_local, _ = _split(whitelist_email)
    kindle_local, _ = _split(kindle_email)
    if sender_local.casefold() == kindle_local.casefold():
        raise LocalPartCollision(
            f"sender local-part {sender_local!r} equals Kindle local-part "
            f"{kindle_local!r}; Amazon will fire a verification email on every "
            "send despite the approved-sender list. Use a sender with a "
            "distinct local-part (e.g. digest@... or kindle@...)."
        )
