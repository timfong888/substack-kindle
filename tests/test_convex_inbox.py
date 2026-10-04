"""Tests for the Convex inbound-email reader (proxy-inbox path).

The Convex client is injected; no test touches the network.
"""

from __future__ import annotations

import sys
import types
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from substack_kindle.convex_inbox import (
    LIST_INBOUND_FOR_WINDOW,
    fetch_inbound,
    make_client,
    pipeline_signature,
)
from substack_kindle.handler import InboundMessage

PROXY = "tim-abcdefgh@inbox.example.resend.app"
SECRET = "pipeline-test-secret-not-real"
START = datetime(2026, 10, 1, tzinfo=UTC)
END = datetime(2026, 10, 1, 23, 59, 59, tzinfo=UTC)
START_MS = 1_790_812_800_000  # 2026-10-01T00:00:00Z
NOW = datetime(2026, 10, 2, 6, 0, tzinfo=UTC)
NOW_MS = 1_790_920_800_000  # 2026-10-02T06:00:00Z


class FakeClient:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def query(self, name: str, args: dict[str, Any] | None = None) -> Any:
        self.calls.append((name, dict(args or {})))
        return self.rows


def _row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "resendEmailId": "em_1",
        "messageId": "<abc@substack.com>",
        "from": "Lenny <lenny@substack.com>",
        "subject": "Weekly issue",
        "receivedAt": float(START_MS + 3_600_000),
        "html": "<p>Hello</p>",
        "text": "Hello",
        "htmlTruncated": False,
        "textTruncated": False,
    }
    row.update(overrides)
    return row


def test_signature_matches_shared_cross_language_vector():
    # Same vector is asserted in web/convex/pipeline.test.ts; keeps both sides in lockstep.
    assert (
        pipeline_signature(
            "pipeline-test-secret-not-real",
            "tim-abcdefgh@test123.resend.app",
            1759276800000,
            1759363200000,
            1759280000000,
        )
        == "30a4ff66b959ecdd952de9b77a9d5718b83bae0bf00090f4efcf7a471bb3e9e6"
    )


def test_calls_window_query_with_signed_args_and_float_millis():
    client = FakeClient([])
    fetch_inbound(
        client,
        proxy_address=PROXY,
        window_start=START,
        window_end=END,
        secret=SECRET,
        now=lambda: NOW,
    )
    end_ms = START_MS + 86_399_000 + 1
    assert client.calls == [
        (
            LIST_INBOUND_FOR_WINDOW,
            {
                "proxyAddress": PROXY,
                "start": float(START_MS),
                # Python windows are inclusive of the end; the Convex query's end
                # is exclusive, so the end is sent 1 ms later.
                "end": float(end_ms),
                "issuedAt": float(NOW_MS),
                "signature": pipeline_signature(SECRET, PROXY, START_MS, end_ms, NOW_MS),
            },
        )
    ]
    # Convex v.number() is float64; a Python int would be encoded as int64.
    assert all(isinstance(client.calls[0][1][k], float) for k in ("start", "end", "issuedAt"))
    assert LIST_INBOUND_FOR_WINDOW == "pipeline:listInboundForWindow"


def test_raw_secret_is_never_sent():
    client = FakeClient([])
    fetch_inbound(client, proxy_address=PROXY, window_start=START, window_end=END, secret=SECRET)
    assert SECRET not in repr(client.calls)


def test_maps_rows_to_inbound_messages_in_order():
    client = FakeClient(
        [
            _row(),
            _row(
                resendEmailId="em_2",
                messageId="<def@x>",
                subject="Second",
                receivedAt=float(START_MS + 7_200_000),
            ),
        ]
    )
    got = fetch_inbound(
        client, proxy_address=PROXY, window_start=START, window_end=END, secret=SECRET
    )
    assert got == [
        InboundMessage(
            sender="Lenny <lenny@substack.com>",
            subject="Weekly issue",
            date_sent=datetime(2026, 10, 1, 1, 0, tzinfo=UTC),
            html_body="<p>Hello</p>",
            message_id="<abc@substack.com>",
        ),
        InboundMessage(
            sender="Lenny <lenny@substack.com>",
            subject="Second",
            date_sent=datetime(2026, 10, 1, 2, 0, tzinfo=UTC),
            html_body="<p>Hello</p>",
            message_id="<def@x>",
        ),
    ]


def test_date_sent_is_timezone_aware_utc():
    client = FakeClient([_row(receivedAt=float(START_MS + 1_500))])
    [msg] = fetch_inbound(
        client, proxy_address=PROXY, window_start=START, window_end=END, secret=SECRET
    )
    assert msg.date_sent.tzinfo is not None
    assert msg.date_sent == START + timedelta(milliseconds=1_500)


def test_missing_message_id_falls_back_to_resend_email_id():
    client = FakeClient([_row(messageId=None)])
    [msg] = fetch_inbound(
        client, proxy_address=PROXY, window_start=START, window_end=END, secret=SECRET
    )
    assert msg.message_id == "em_1"


def test_html_missing_falls_back_to_escaped_text():
    client = FakeClient([_row(html=None, htmlTruncated=True, text="a < b & c")])
    [msg] = fetch_inbound(
        client, proxy_address=PROXY, window_start=START, window_end=END, secret=SECRET
    )
    assert msg.html_body == "<pre>a &lt; b &amp; c</pre>"


def test_no_body_at_all_yields_empty_html():
    client = FakeClient([_row(html=None, text=None)])
    [msg] = fetch_inbound(
        client, proxy_address=PROXY, window_start=START, window_end=END, secret=SECRET
    )
    assert msg.html_body == ""


def test_non_utc_window_is_converted_to_epoch_millis():
    plus8 = timezone(timedelta(hours=8))
    client = FakeClient([])
    fetch_inbound(
        client,
        proxy_address=PROXY,
        window_start=datetime(2026, 10, 1, 8, 0, tzinfo=plus8),
        window_end=datetime(2026, 10, 1, 8, 0, tzinfo=plus8),
        secret=SECRET,
    )
    args = client.calls[0][1]
    assert args["start"] == float(START_MS)
    assert args["end"] == float(START_MS + 1)


def test_rejects_naive_window():
    client = FakeClient([])
    with pytest.raises(ValueError, match="timezone-aware"):
        fetch_inbound(
            client,
            proxy_address=PROXY,
            window_start=datetime(2026, 10, 1),
            window_end=END,
            secret=SECRET,
        )
    assert client.calls == []


def test_rejects_inverted_window():
    with pytest.raises(ValueError, match="window_start"):
        fetch_inbound(
            FakeClient([]),
            proxy_address=PROXY,
            window_start=END,
            window_end=START,
            secret=SECRET,
        )


def test_rejects_empty_secret():
    with pytest.raises(ValueError, match="secret"):
        fetch_inbound(
            FakeClient([]), proxy_address=PROXY, window_start=START, window_end=END, secret=""
        )


def test_both_bodies_dropped_for_size_yield_visible_placeholder():
    # Otherwise the body is empty and the handler silently skips the issue.
    client = FakeClient([_row(html=None, text=None, htmlTruncated=True, textTruncated=True)])
    [msg] = fetch_inbound(
        client, proxy_address=PROXY, window_start=START, window_end=END, secret=SECRET
    )
    assert "too large" in msg.html_body
    assert msg.subject == "Weekly issue"


def test_client_errors_propagate():
    class Boom:
        def query(self, name: str, args: dict[str, Any] | None = None) -> Any:
            raise RuntimeError("Unauthorized")

    with pytest.raises(RuntimeError, match="Unauthorized"):
        fetch_inbound(Boom(), proxy_address=PROXY, window_start=START, window_end=END, secret="x")


def test_make_client_imports_convex_lazily(monkeypatch):
    created: list[str] = []

    class FakeConvexClient:
        def __init__(self, url: str) -> None:
            created.append(url)

    fake = types.ModuleType("convex")
    fake.ConvexClient = FakeConvexClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "convex", fake)
    client = make_client("https://example-123.convex.cloud")
    assert isinstance(client, FakeConvexClient)
    assert created == ["https://example-123.convex.cloud"]


def test_make_client_without_package_raises_helpful_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "convex", None)
    with pytest.raises(ImportError, match="uv add convex"):
        make_client("https://example-123.convex.cloud")
