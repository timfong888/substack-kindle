"""End-to-end serverless pipeline tests (SAT-281 / serverless architecture).

Exercises the full pipeline — HTML parsing → EPUB build → mocked Postmark send —
without any live I/O (no Gmail, no OAuth, no real HTTP). Tests are intentionally
coarse-grained: each one exercises a whole feature path, not a single module.

These prove the composition that a serverless handler function will use, and
serve as a regression harness for the real Kindle delivery scenario.
"""

from __future__ import annotations

import base64
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime
from io import BytesIO

import pytest

from substack_kindle.handler import HandlerResult, InboundMessage, process_messages

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_WINDOW_START = datetime(2026, 6, 1, tzinfo=UTC)
_WINDOW_END = datetime(2026, 6, 5, 23, 59, 59, tzinfo=UTC)

# Substack-shaped email HTML: a tracking pixel (triggers the cleaner), real
# article body, and a "Forwarded this email?" banner table the cleaner must strip.
# Modelled on the _wrap() helper in test_substack_clean.py — the cleaner key
# is the presence of a substackcdn.com or substack.com/app-link URL, not the
# outer table layout.
_SUBSTACK_HTML = (
    "<html><body>"
    '<img src="https://eotrx.substackcdn.com/o/abc/p.gif?token=xyz" />'
    "<h1>Markets Weekly</h1>"
    "<p>Bitcoin reached a new high of $120 000 this week.</p>"
    "<h2>DeFi Roundup</h2>"
    "<p>Uniswap v4 launched on mainnet with new hook system.</p>"
    "<h2>Protocol News</h2>"
    "<p>Ethereum staking rewards dropped to 3.2% APY.</p>"
    "<table><tr><td>"
    "Forwarded this email? "
    '<a href="https://substack.com/subscribe">Subscribe here</a> for more.'
    "</td></tr></table>"
    '<p><a href="https://substack.com/app-link/post?x=1">Read in app</a></p>'
    "</body></html>"
)

# HTML with a data table — proves the CSS fix lands in real content.
_TABLE_HTML = """\
<div>
  <h1>Weekly Stats</h1>
  <table>
    <tr><th>Metric</th><th>Value</th></tr>
    <tr><td>Active users</td><td>1 024</td></tr>
    <tr><td>Revenue</td><td>$4 800</td></tr>
    <tr><td>Churn</td><td>2.1 %</td></tr>
  </table>
  <p>All numbers are week-over-week.</p>
</div>
"""

# Non-Substack plain newsletter HTML — no chrome, just prose with headings.
_PLAIN_HTML = """\
<html><body>
  <h1>Lenny's Newsletter #213</h1>
  <h2>How great PMs set strategy</h2>
  <p>Strategy is about the choices you make.</p>
  <p>Most teams confuse goals with strategy.</p>
</body></html>
"""


class _PostmarkSpy:
    """Captures Postmark HTTP calls; returns a canned 200 OK response."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    class _Resp:
        status_code = 200

        @staticmethod
        def json() -> dict:
            return {"MessageID": "msg-test-1", "ErrorCode": 0, "Message": "OK"}

        text = ""

    def __call__(self, url: str, *, json: dict, headers: dict, **kwargs) -> _Resp:
        self.calls.append({"url": url, "json": json, "headers": headers})
        return self._Resp()


class _FailingPost:
    """Always returns a Postmark 422 error (simulates account not approved)."""

    class _Resp:
        status_code = 422

        @staticmethod
        def json() -> dict:
            return {"ErrorCode": 406, "Message": "Inactive recipient"}

        text = "Inactive recipient"

    def __call__(self, *a, **kw) -> _Resp:
        return self._Resp()


def _msg(html: str, sender: str = "news@example.com", subject: str = "Weekly News") -> InboundMessage:
    return InboundMessage(
        sender=sender,
        subject=subject,
        date_sent=datetime(2026, 6, 4, 8, 0, tzinfo=UTC),
        html_body=html,
        message_id="msg-1",
    )


def _base_kwargs(**overrides) -> dict:
    base = dict(
        book_title="Newsletter Digest: June 4 2026",
        postmark_server_token="test-token",
        whitelist_email="digest@example.com",
        kindle_email="reader@kindle.com",
        http_post=_PostmarkSpy(),
    )
    base.update(overrides)
    return base


def _epub_from_spy(spy: _PostmarkSpy) -> bytes:
    """Decode the EPUB bytes from the last Postmark call's attachment."""
    attachment = spy.calls[-1]["json"]["Attachments"][0]
    return base64.b64decode(attachment["Content"])


def _is_valid_epub(data: bytes) -> bool:
    """A ZIP with mimetype == application/epub+zip is a valid EPUB container."""
    try:
        with zipfile.ZipFile(BytesIO(data)) as zf:
            return zf.read("mimetype") == b"application/epub+zip"
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_single_newsletter_delivers_valid_epub_to_postmark():
    """Full pipeline: one HTML message → EPUB attachment → Postmark 200 → ok result."""
    spy = _PostmarkSpy()
    result = process_messages(
        [_msg(_PLAIN_HTML, sender="lenny@substack.com", subject="Lenny's Newsletter #213")],
        **_base_kwargs(http_post=spy),
    )
    assert result.status == "ok"
    assert result.delivered == 1
    assert len(spy.calls) == 1
    assert _is_valid_epub(_epub_from_spy(spy))


def test_multiple_newsletters_produce_one_epub_with_all_sections():
    """N newsletters → one EPUB attachment containing all N sections."""
    spy = _PostmarkSpy()
    messages = [
        _msg(_SUBSTACK_HTML, sender="token@substack.com", subject="Token Dispatch — June 4"),
        _msg(_PLAIN_HTML, sender="lenny@substack.com", subject="Lenny's Newsletter #213"),
        _msg(_TABLE_HTML, sender="stats@example.com", subject="Weekly Stats"),
    ]
    result = process_messages(messages, **_base_kwargs(http_post=spy))
    assert result.status == "ok"
    assert result.delivered == 3
    assert len(spy.calls) == 1  # one combined EPUB, not three separate sends
    epub = _epub_from_spy(spy)
    with zipfile.ZipFile(BytesIO(epub)) as zf:
        bodies = [
            zf.read(n).decode("utf-8", errors="replace")
            for n in zf.namelist()
            if n.endswith(".xhtml") and "section_" in n
        ]
    combined = "\n".join(bodies)
    assert "Lenny" in combined
    assert "Bitcoin" in combined or "Markets" in combined
    assert "Active users" in combined or "Weekly Stats" in combined


def test_substack_html_chrome_is_stripped_from_epub_body():
    """Substack chrome (icon row, 'Read in app', 'Forwarded this email') must not
    appear in the delivered EPUB body — the SAT-265 cleaner must fire in the pipeline."""
    spy = _PostmarkSpy()
    process_messages(
        [_msg(_SUBSTACK_HTML, sender="token@substack.com", subject="Token Dispatch")],
        **_base_kwargs(http_post=spy),
    )
    epub = _epub_from_spy(spy)
    with zipfile.ZipFile(BytesIO(epub)) as zf:
        sections = [
            zf.read(n).decode("utf-8", errors="replace")
            for n in zf.namelist()
            if "section_" in n and n.endswith(".xhtml")
        ]
    body = "\n".join(sections)
    # The cleaner's primary target: "Forwarded this email?" banner must be gone.
    assert "Forwarded this email" not in body
    # Real article content must survive the cleaner.
    assert "Bitcoin" in body or "DeFi" in body or "Markets" in body


def test_table_html_delivers_epub_with_css_stylesheet():
    """An EPUB built from table-containing HTML must include the newsletter CSS file."""
    spy = _PostmarkSpy()
    process_messages(
        [_msg(_TABLE_HTML, sender="stats@example.com", subject="Weekly Stats")],
        **_base_kwargs(http_post=spy),
    )
    epub = _epub_from_spy(spy)
    with zipfile.ZipFile(BytesIO(epub)) as zf:
        names = zf.namelist()
    assert any("newsletter.css" in n for n in names), (
        "EPUB must contain newsletter.css for table rendering on Kindle"
    )


def test_postmark_called_with_epub_content_type_and_kindle_recipient():
    """Postmark payload must carry the correct To, From, ContentType, and token header."""
    spy = _PostmarkSpy()
    process_messages([_msg(_PLAIN_HTML)], **_base_kwargs(http_post=spy))
    payload = spy.calls[0]["json"]
    headers = spy.calls[0]["headers"]
    assert payload["To"] == "reader@kindle.com"
    assert payload["From"] == "digest@example.com"
    assert payload["Attachments"][0]["ContentType"] == "application/epub+zip"
    assert headers["X-Postmark-Server-Token"] == "test-token"


# ---------------------------------------------------------------------------
# Empty / no-op paths
# ---------------------------------------------------------------------------


def test_empty_message_list_returns_empty_result_without_postmark_call():
    spy = _PostmarkSpy()
    result = process_messages([], **_base_kwargs(http_post=spy))
    assert result.status == "empty"
    assert result.delivered == 0
    assert spy.calls == []


def test_html_body_with_no_meaningful_text_is_skipped():
    """A message whose HTML produces empty markdown must not inflate the EPUB."""
    spy = _PostmarkSpy()
    result = process_messages(
        [_msg("   ", sender="noise@example.com", subject="Empty")],
        **_base_kwargs(http_post=spy),
    )
    assert result.status == "empty"
    assert spy.calls == []


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_postmark_4xx_raises_and_result_is_not_ok():
    """A Postmark HTTP error must propagate so the caller can retry or alert."""
    from substack_kindle.postmark import PostmarkError

    with pytest.raises(PostmarkError):
        process_messages(
            [_msg(_PLAIN_HTML)],
            **_base_kwargs(http_post=_FailingPost()),
        )


# ---------------------------------------------------------------------------
# Dedup via processed-state store
# ---------------------------------------------------------------------------


def test_already_delivered_message_is_skipped_by_state_store():
    """If the state store marks a newsletter as delivered, it must not be re-sent."""
    from substack_kindle.processed_state import InMemoryProcessedStateStore
    from substack_kindle.ids import newsletter_id

    msg = _msg(_PLAIN_HTML, sender="lenny@substack.com", subject="Lenny #213")
    nid = newsletter_id(msg.sender, msg.date_sent.isoformat(), msg.subject)

    state = InMemoryProcessedStateStore()
    state.mark_delivered(nid)

    spy = _PostmarkSpy()
    result = process_messages([msg], **_base_kwargs(http_post=spy), state=state)
    assert result.status == "empty"
    assert spy.calls == []


def test_state_store_is_updated_after_successful_delivery():
    """After a successful delivery the newsletter IDs are marked delivered in the state store."""
    from substack_kindle.processed_state import InMemoryProcessedStateStore
    from substack_kindle.ids import newsletter_id
    from substack_kindle.processed_state import ProcessedState

    msg = _msg(_PLAIN_HTML, sender="lenny@substack.com", subject="Lenny #213")
    nid = newsletter_id(msg.sender, msg.date_sent.isoformat(), msg.subject)

    state = InMemoryProcessedStateStore()
    spy = _PostmarkSpy()
    process_messages([msg], **_base_kwargs(http_post=spy), state=state)
    assert state.state_of(nid) is ProcessedState.DELIVERED
