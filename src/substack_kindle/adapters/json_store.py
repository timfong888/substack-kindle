"""JSON-file-backed config + processed-state stores for the end-to-end spike.

These mirror the behavior of ``config_store.InMemoryConfigStore`` and
``processed_state.InMemoryProcessedStateStore`` but persist to a JSON file on
disk so a CLI run can pick up state seeded by an earlier invocation. Only the
opaque ``gmail_oauth_token_ref`` string is ever written — never a raw token.
A missing file is treated as empty.
"""

from __future__ import annotations

import json
from pathlib import Path

from substack_kindle.config_store import CustomerConfig


class JsonConfigStore:
    """Customer-keyed config store persisted to a JSON file."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._rows: dict[str, CustomerConfig] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        for customer_id, row in raw.items():
            self._rows[customer_id] = CustomerConfig(
                customer_id=row["customer_id"],
                recipient_email=row["recipient_email"],
                kindle_email=row["kindle_email"],
                newsletter_label=row["newsletter_label"],
                gmail_oauth_token_ref=row["gmail_oauth_token_ref"],
                approved_sources=list(row.get("approved_sources", [])),
                whitelisting_status=row.get("whitelisting_status", "unconfirmed"),
            )

    def _save(self) -> None:
        out = {
            customer_id: {
                "customer_id": cfg.customer_id,
                "recipient_email": cfg.recipient_email,
                "kindle_email": cfg.kindle_email,
                "newsletter_label": cfg.newsletter_label,
                "gmail_oauth_token_ref": cfg.gmail_oauth_token_ref,
                "approved_sources": list(cfg.approved_sources),
                "whitelisting_status": cfg.whitelisting_status,
            }
            for customer_id, cfg in self._rows.items()
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(out, indent=2), encoding="utf-8")

    def put(self, config: CustomerConfig) -> None:
        """Store a customer's config, unioning any existing approved_sources.

        Mirrors ``InMemoryConfigStore.put``: approved senders only ever grow, so
        re-putting a config never silently drops the accumulated sender list.
        """
        existing = self._rows.get(config.customer_id)
        if existing is not None and existing.approved_sources:
            merged = list(config.approved_sources)
            for sender in existing.approved_sources:
                if sender not in merged:
                    merged.append(sender)
            config = CustomerConfig(
                customer_id=config.customer_id,
                recipient_email=config.recipient_email,
                kindle_email=config.kindle_email,
                newsletter_label=config.newsletter_label,
                gmail_oauth_token_ref=config.gmail_oauth_token_ref,
                approved_sources=merged,
                whitelisting_status=config.whitelisting_status,
            )
        self._rows[config.customer_id] = config
        self._save()

    def get(self, customer_id: str) -> CustomerConfig | None:
        return self._rows.get(customer_id)

    def add_approved_source(self, customer_id: str, sender: str) -> None:
        config = self._rows.get(customer_id)
        if config is None:
            raise KeyError(f"no config stored for customer {customer_id!r}")
        if sender not in config.approved_sources:
            config.approved_sources.append(sender)
            self._save()

    def __len__(self) -> int:
        return len(self._rows)


class JsonProcessedStateStore:
    """Processed-state store persisting delivered newsletter/message IDs to JSON."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._delivered_ids: set[str] = set()
        self._delivered_message_ids: set[str] = set()
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        self._delivered_ids = set(raw.get("delivered_ids", []))
        self._delivered_message_ids = set(raw.get("delivered_message_ids", []))

    def _save(self) -> None:
        out = {
            "delivered_ids": sorted(self._delivered_ids),
            "delivered_message_ids": sorted(self._delivered_message_ids),
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(out, indent=2), encoding="utf-8")

    def is_delivered(self, newsletter_id: str) -> bool:
        return newsletter_id in self._delivered_ids

    def mark_delivered(self, newsletter_id: str, *, gmail_message_id: str | None = None) -> None:
        self._delivered_ids.add(newsletter_id)
        if gmail_message_id is not None:
            self._delivered_message_ids.add(gmail_message_id)
        self._save()

    def is_message_delivered(self, gmail_message_id: str) -> bool:
        return gmail_message_id in self._delivered_message_ids
