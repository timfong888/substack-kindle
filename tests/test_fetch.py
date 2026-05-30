"""Tests for the Gmail → JobSection fetch pipeline (SAT-269).

The fetch layer is the bridge between a ``ReadOnlyGmailClient`` and the EPUB
builder: it lists messages from approved senders within a window, extracts the
HTML body and From/Subject/Date headers, runs them through ``parsing`` (which
includes the SAT-265 Substack cleaner), and returns ``JobSection`` plus
``CollectedNewsletter`` records ready for ``pipeline.run_job``.
"""

import base64
from datetime import UTC, datetime

from substack_kindle.fetch import (
    GmailFetchError,
    extract_body_html,
    extract_headers,
    fetch_newsletters,
)


def _b64(data: str) -> str:
    return base64.urlsafe_b64encode(data.encode("utf-8")).decode("ascii")


def _msg(message_id, *, frm, subject, date, body_html):
    return {
        "id": message_id,
        "payload": {
            "headers": [
                {"name": "From", "value": frm},
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": date},
            ],
            "mimeType": "text/html",
            "body": {"data": _b64(body_html)},
        },
    }


def _multipart_msg(message_id, *, frm, subject, date, html_body, plain_body=""):
    return {
        "id": message_id,
        "payload": {
            "headers": [
                {"name": "From", "value": frm},
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": date},
            ],
            "mimeType": "multipart/alternative",
            "parts": [
                {"mimeType": "text/plain", "body": {"data": _b64(plain_body)}},
                {"mimeType": "text/html", "body": {"data": _b64(html_body)}},
            ],
        },
    }


class _StubClient:
    """A ReadOnlyGmailClient stand-in: returns canned ids and message dicts."""

    def __init__(self, *, ids, messages):
        self._ids = ids
        self._messages = messages
        self.list_calls = []

    def list_message_ids(self, query=None):
        self.list_calls.append(query)
        return list(self._ids)

    def get_message(self, message_id):
        return self._messages[message_id]


# --- Header / body extraction ------------------------------------------------


_DATE = "Sat, 9 May 2026 12:00:00 +0000"


def test_extract_headers_picks_from_subject_and_date():
    headers = extract_headers(_msg(
        "m1", frm="Lenny <lenny@substack.com>", subject="Hi", date=_DATE, body_html="x",
    ))
    assert headers.from_address == "lenny@substack.com"
    assert headers.subject == "Hi"
    assert headers.date.tzinfo is not None
    assert headers.date == datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC)


def test_extract_headers_handles_bare_from_with_no_display_name():
    headers = extract_headers(_msg(
        "m1", frm="lenny@substack.com", subject="Hi", date=_DATE, body_html="x",
    ))
    assert headers.from_address == "lenny@substack.com"


def test_extract_body_html_from_single_part_message():
    msg = _msg("m1", frm="x@x", subject="x", date=_DATE, body_html="<p>Hello</p>")
    assert extract_body_html(msg) == "<p>Hello</p>"


def test_extract_body_html_prefers_text_html_part_in_multipart():
    msg = _multipart_msg(
        "m1", frm="x@x", subject="x", date=_DATE,
        html_body="<p>HTML</p>", plain_body="PLAIN",
    )
    assert extract_body_html(msg) == "<p>HTML</p>"


def _headers_only():
    return [
        {"name": "From", "value": "x@x"},
        {"name": "Subject", "value": "x"},
        {"name": "Date", "value": _DATE},
    ]


def test_extract_body_html_falls_back_to_text_plain_when_no_html():
    msg = {"id": "m1", "payload": {
        "headers": _headers_only(),
        "mimeType": "text/plain",
        "body": {"data": _b64("just text")},
    }}
    assert extract_body_html(msg) == "just text"


def test_extract_body_html_raises_when_message_has_no_body():
    msg = {"id": "m1", "payload": {
        "headers": _headers_only(),
        "mimeType": "multipart/mixed",
        "parts": [{"mimeType": "image/png", "body": {"attachmentId": "a1"}}],
    }}
    import pytest

    with pytest.raises(GmailFetchError, match="no body"):
        extract_body_html(msg)


# --- End-to-end fetch with stub client --------------------------------------


def test_fetch_newsletters_returns_sections_in_window_order():
    msgs = {
        "m1": _msg(
            "m1",
            frm="lenny@substack.com",
            subject="Lenny issue 12",
            date="Mon, 4 May 2026 09:00:00 +0000",
            body_html="<h1>Lenny</h1><p>Body 1</p>",
        ),
        "m2": _msg(
            "m2",
            frm="thetokendispatch@substack.com",
            subject="TD weekly",
            date="Wed, 6 May 2026 10:00:00 +0000",
            body_html="<h1>TD</h1><p>Body 2</p>",
        ),
        "m3": _msg(
            "m3",
            frm="unapproved@spam.com",
            subject="Spam",
            date="Tue, 5 May 2026 10:00:00 +0000",
            body_html="<p>spam</p>",
        ),
    }
    client = _StubClient(ids=["m1", "m2", "m3"], messages=msgs)
    sections = fetch_newsletters(
        client,
        approved_sources=["lenny@substack.com", "thetokendispatch@substack.com"],
        window_start=datetime(2026, 5, 3, tzinfo=UTC),
        window_end=datetime(2026, 5, 9, 23, 59, 59, tzinfo=UTC),
    )
    # Unapproved sender filtered out; remaining ordered by date ascending.
    assert [s.title for s in sections] == ["Lenny issue 12", "TD weekly"]
    # Body went through parsing.html_to_markdown — the H1 survives as "# Lenny".
    assert "Lenny" in sections[0].markdown
    assert "TD" in sections[1].markdown


def test_fetch_newsletters_uses_gmail_query_to_narrow_results():
    client = _StubClient(ids=[], messages={})
    fetch_newsletters(
        client,
        approved_sources=["lenny@substack.com"],
        window_start=datetime(2026, 5, 3, tzinfo=UTC),
        window_end=datetime(2026, 5, 9, 23, 59, 59, tzinfo=UTC),
    )
    # Single query call; it includes the sender and date bounds.
    assert len(client.list_calls) == 1
    query = client.list_calls[0]
    assert "from:" in query and "lenny@substack.com" in query
    assert "after:2026/05/03" in query
    assert "before:2026/05/10" in query  # Gmail "before:" is exclusive; +1 day


def test_fetch_newsletters_rejects_empty_approved_sources():
    # An empty allowlist is a misconfiguration: the function refuses rather
    # than issuing a mailbox-wide Gmail query that drops every result.
    import pytest

    client = _StubClient(ids=[], messages={})
    with pytest.raises(ValueError, match="approved_sources"):
        fetch_newsletters(
            client,
            approved_sources=[],
            window_start=datetime(2026, 5, 3, tzinfo=UTC),
            window_end=datetime(2026, 5, 9, 23, 59, 59, tzinfo=UTC),
        )
    # Crucially, no Gmail call was made.
    assert client.list_calls == []
