from __future__ import annotations

from datetime import UTC, datetime

from substack_kindle.collection import IncomingMessage
from substack_kindle.spike import SpikeConfig, run_spike


def test_run_spike_delivers_and_notifies():
    msgs = [
        IncomingMessage("m1", "a@x.com", datetime(2026, 5, 24, 12, tzinfo=UTC), "Issue 1")
    ]
    bodies = {"m1": "<p>hello</p>"}
    sends = {}

    def fake_send_epub(*, epub_bytes, to, **kw):
        sends["epub_to"] = to
        return type("R", (), {"message_id": "x", "to": to})()

    notes = {}

    def fake_send_email(*, to, subject, body):
        notes["to"] = to

    cfg = SpikeConfig(
        customer_id="me",
        recipient_email="timfong888@gmail.com",
        kindle_email="timfong888@kindle.com",
        approved_sources=["a@x.com"],
    )
    result = run_spike(
        cfg,
        incoming=msgs,
        bodies=bodies,
        window=(
            datetime(2026, 5, 24, 0, 0, tzinfo=UTC),
            datetime(2026, 5, 24, 23, 59, 59, tzinfo=UTC),
        ),
        send_epub=fake_send_epub,
        send_email=fake_send_email,
        is_delivered=lambda _id: False,
        mark_delivered=lambda *a, **k: None,
    )
    assert result.status == "succeeded" and result.outcome == "delivered"
    assert sends["epub_to"] == "timfong888@kindle.com"
    assert notes["to"] == "timfong888@gmail.com"
