from __future__ import annotations

import json

import pytest

from substack_kindle.adapters.gmail_messages import load_messages


def test_load_messages_maps_incoming_and_bodies(tmp_path):
    p = tmp_path / "messages.json"
    p.write_text(
        json.dumps(
            {
                "messages": [
                    {
                        "message_id": "m1",
                        "sender": "A@X.com",
                        "date_sent": "2026-05-24T15:30:38+00:00",
                        "subject": "Hi",
                        "html_body": "<p>x</p>",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    incoming, bodies = load_messages(p)
    assert incoming[0].message_id == "m1"
    assert incoming[0].date_sent.tzinfo is not None
    assert incoming[0].sender == "A@X.com"
    assert bodies["m1"] == "<p>x</p>"


def test_load_messages_rejects_naive_datetime(tmp_path):
    p = tmp_path / "messages.json"
    p.write_text(
        json.dumps(
            {
                "messages": [
                    {
                        "message_id": "m1",
                        "sender": "a@x.com",
                        "date_sent": "2026-05-24T15:30:38",
                        "subject": "Hi",
                        "html_body": "<p>x</p>",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_messages(p)
