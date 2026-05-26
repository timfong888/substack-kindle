"""Command-line entry point for the end-to-end spike.

Subcommands wire the pure pipeline to real I/O: ``seed-senders`` ingests an
approved-sender list, ``fetch-template`` writes a ``messages.json`` skeleton,
``test-send`` delivers a tiny known EPUB, and ``run`` executes the full
collect -> build -> send -> notify pipeline for a window.
"""

from __future__ import annotations

from pathlib import Path

from substack_kindle.adapters.json_store import JsonConfigStore


def seed_senders(file_path: str | Path, customer_id: str, store: JsonConfigStore) -> list[str]:
    """Ingest an approved-sender list from ``file_path`` into the customer's config.

    Lines are stripped, lowercased, and deduplicated in order; blank lines and
    lines without an ``@`` are skipped. Returns the full normalized sender list
    parsed from the file (idempotent: re-seeding an existing sender is a no-op
    against the store).
    """
    text = Path(file_path).read_text(encoding="utf-8")
    normalized: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip().lower()
        if not line or "@" not in line:
            continue
        if line not in normalized:
            normalized.append(line)
    for sender in normalized:
        store.add_approved_source(customer_id, sender)
    return normalized
