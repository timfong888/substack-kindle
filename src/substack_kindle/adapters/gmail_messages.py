"""Load pre-fetched Gmail messages from a JSON file (the read seam).

The agent queries the Gmail MCP and writes ``messages.json`` in the schema below;
this adapter turns that file into the pure pipeline's inputs. It deliberately
holds the read seam so the real OAuth ``GmailTransport`` can replace it later
without touching the pipeline.

Schema::

    {"messages": [{"message_id": "...", "sender": "...",
                   "date_sent": "2026-05-24T15:30:38+00:00",
                   "subject": "...", "html_body": "<html>..."}]}
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from substack_kindle.collection import IncomingMessage


def load_messages(path: str | Path) -> tuple[list[IncomingMessage], dict[str, str]]:
    """Return ``(incoming_messages, message_id -> html_body)`` from ``path``.

    ``date_sent`` is parsed with ``datetime.fromisoformat`` and must be
    timezone-aware (raises ``ValueError`` otherwise). The original sender case is
    preserved — ``collection.collect_newsletters`` lowercases internally.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    incoming: list[IncomingMessage] = []
    bodies: dict[str, str] = {}
    for entry in raw.get("messages", []):
        date_sent = datetime.fromisoformat(entry["date_sent"])
        if date_sent.tzinfo is None:
            raise ValueError(
                f"message {entry['message_id']!r} has a timezone-naive date_sent; "
                "all datetimes must be timezone-aware"
            )
        incoming.append(
            IncomingMessage(
                message_id=entry["message_id"],
                sender=entry["sender"],
                date_sent=date_sent,
                subject=entry["subject"],
            )
        )
        bodies[entry["message_id"]] = entry["html_body"]
    return incoming, bodies
