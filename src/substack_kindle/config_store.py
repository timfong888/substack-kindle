"""Per-customer config store (SAT-237 / Req §Configuration).

Multi-tenant from day one: one ``CustomerConfig`` per customer, keyed by
``customer_id`` so two customers never collide. The Gmail OAuth token is held as
an opaque *reference* (resolved at runtime from a secrets manager) — never the
raw token — and is redacted from ``repr``. ``whitelist_email`` is a single shared
system value, not stored per customer; it is read from the environment at
runtime.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace

WHITELISTING_STATUSES = ("confirmed", "unconfirmed")


@dataclass(frozen=True)
class CustomerConfig:
    """One config row for a single customer.

    ``gmail_oauth_token_ref`` is a reference (e.g. ``secretref://...``) to the
    token in a secrets manager, never the raw token itself.

    Frozen so ``whitelisting_status`` cannot be reassigned to an invalid value
    after construction (which would bypass the ``__post_init__`` guard);
    ``approved_sources`` is still list-mutated in place by the store.

    Logging safety: ``__repr__`` redacts the token reference, but
    ``dataclasses.asdict()`` / ``vars()`` return it unredacted — do not feed this
    object to those helpers on a logging or serialization path.
    """

    customer_id: str
    recipient_email: str
    kindle_email: str
    newsletter_label: str
    gmail_oauth_token_ref: str
    approved_sources: list[str] = field(default_factory=list)
    whitelisting_status: str = "unconfirmed"

    def __post_init__(self) -> None:
        if self.whitelisting_status not in WHITELISTING_STATUSES:
            raise ValueError(
                f"whitelisting_status must be one of {WHITELISTING_STATUSES}, "
                f"got {self.whitelisting_status!r}"
            )

    def __repr__(self) -> str:
        # Redact the token reference so it never lands in logs/tracebacks.
        return (
            f"CustomerConfig(customer_id={self.customer_id!r}, "
            f"recipient_email={self.recipient_email!r}, "
            f"kindle_email={self.kindle_email!r}, "
            f"newsletter_label={self.newsletter_label!r}, "
            f"gmail_oauth_token_ref='***redacted***', "
            f"approved_sources={self.approved_sources!r}, "
            f"whitelisting_status={self.whitelisting_status!r})"
        )


def shared_whitelist_email() -> str:
    """Return the single shared sending/whitelist identity from the environment.

    This is intentionally NOT stored per customer; it is one system value
    supplied at runtime (never committed).
    """
    value = os.environ.get("WHITELIST_EMAIL")
    if not value:
        raise RuntimeError("WHITELIST_EMAIL is not configured in the environment")
    return value


class InMemoryConfigStore:
    """Customer-keyed config store. Reads/writes never collide across customers."""

    def __init__(self) -> None:
        self._rows: dict[str, CustomerConfig] = {}

    def put(self, config: CustomerConfig) -> None:
        """Store a customer's config.

        Re-putting an existing customer (e.g. to rotate ``kindle_email``)
        preserves any ``approved_sources`` already accumulated for that customer
        via :meth:`add_approved_source`: the incoming sources are unioned with
        the stored ones (order-preserving). Approved senders only ever grow
        (there is no remove path), so a routine config update never silently
        drops the approved-sender list.
        """
        existing = self._rows.get(config.customer_id)
        if existing is not None and existing.approved_sources:
            merged = list(config.approved_sources)
            for sender in existing.approved_sources:
                if sender not in merged:
                    merged.append(sender)
            config = replace(config, approved_sources=merged)
        self._rows[config.customer_id] = config

    def get(self, customer_id: str) -> CustomerConfig | None:
        return self._rows.get(customer_id)

    def add_approved_source(self, customer_id: str, sender: str) -> None:
        config = self._rows.get(customer_id)
        if config is None:
            raise KeyError(f"no config stored for customer {customer_id!r}")
        if sender not in config.approved_sources:
            config.approved_sources.append(sender)

    def __len__(self) -> int:
        return len(self._rows)
