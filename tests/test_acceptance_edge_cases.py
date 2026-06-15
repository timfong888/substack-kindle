"""Acceptance test: one kitchen-sink newsletter exercising every known
rendering edge case (GH #57 empty tables, layout vs data tables, nested data
tables, Substack chrome, zero-width padding).

The point is regression-proofing, not just one example. Alongside per-case
content markers it asserts *structural invariants* on the delivered EPUB —
chiefly "no table may be empty" — so any future input that reintroduces a
defect of the same class fails here, not only this exact sample.

The fixture lives in tests/fixtures/edge_case_newsletter.html; add a CASE there
and a marker/invariant here whenever a new rendering bug is found.
"""

from __future__ import annotations

import base64
import zipfile
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from bs4 import BeautifulSoup

from substack_kindle.handler import InboundMessage, process_messages

_FIXTURE = Path(__file__).parent / "fixtures" / "edge_case_newsletter.html"

# Real content that must survive the pipeline — one marker per fixture CASE.
_CONTENT_MARKERS = (
    "CONTENT_TITLE",
    "CONTENT_PROSE",
    "CONTENT_LAYOUT_HEADING",
    "CONTENT_LAYOUT_LEFT",
    "CONTENT_LAYOUT_RIGHT",
    "CONTENT_AFTER_EMPTY_TABLE",
    "CONTENT_DATA_CELL",
    "CONTENT_NESTED_CELL",
)

# Chrome that must NOT appear in the delivered body. (The tracking pixel, the
# "Forwarded this email?" banner, and the empty-table spacer image are all
# guaranteed stripped today.) NOTE: a bare "Read in app" app-link URL is NOT
# stripped unless it carries the email-read-in-app utm hint — a known SAT-265
# cleaner gap, deliberately not asserted here so this test only locks in real
# current behavior.
_CHROME_MARKERS = ("substackcdn.com", "Forwarded this email", "spacer.png")


class _PostmarkSpy:
    """Captures the Postmark call and returns a canned 200 OK."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    class _Resp:
        status_code = 200

        @staticmethod
        def json() -> dict:
            return {"MessageID": "msg-test", "ErrorCode": 0, "Message": "OK"}

        text = ""

    def __call__(self, url: str, *, json: dict, headers: dict, **kwargs) -> _Resp:
        self.calls.append({"json": json})
        return self._Resp()


def _deliver_fixture() -> bytes:
    """Run the edge-case fixture through the full pipeline; return the EPUB bytes."""
    spy = _PostmarkSpy()
    msg = InboundMessage(
        sender="digest@substack.com",
        subject="Markets Weekly — June 2026",
        date_sent=datetime(2026, 6, 4, 8, 0, tzinfo=UTC),
        html_body=_FIXTURE.read_text(encoding="utf-8"),
        message_id="acceptance-1",
    )
    result = process_messages(
        [msg],
        book_title="Acceptance Digest",
        postmark_server_token="test-token",
        whitelist_email="digest@example.com",
        kindle_email="reader@kindle.com",
        http_post=spy,
    )
    assert result.status == "ok"
    return base64.b64decode(spy.calls[-1]["json"]["Attachments"][0]["Content"])


def _section_bodies(epub: bytes) -> list[str]:
    with zipfile.ZipFile(BytesIO(epub)) as zf:
        return [
            zf.read(n).decode("utf-8", errors="replace")
            for n in zf.namelist()
            if n.endswith(".xhtml") and "section_" in n
        ]


def test_edge_case_fixture_produces_a_valid_epub():
    epub = _deliver_fixture()
    with zipfile.ZipFile(BytesIO(epub)) as zf:
        assert zf.read("mimetype") == b"application/epub+zip"


def test_all_real_content_survives_every_case():
    body = "\n".join(_section_bodies(_deliver_fixture()))
    missing = [m for m in _CONTENT_MARKERS if m not in body]
    assert not missing, f"content lost in pipeline: {missing}"


def test_chrome_and_tracking_are_stripped():
    body = "\n".join(_section_bodies(_deliver_fixture()))
    leaked = [m for m in _CHROME_MARKERS if m in body]
    assert not leaked, f"chrome/tracking leaked into body: {leaked}"


def test_invariant_no_empty_tables_in_delivered_epub():
    """The GH #57 class guard: no <table> in the EPUB may render with empty text.

    Fails for ANY input that reintroduces an empty/broken table, not just this
    fixture's CASE 3.
    """
    for body in _section_bodies(_deliver_fixture()):
        soup = BeautifulSoup(body, "html.parser")
        empty = [str(t)[:80] for t in soup.find_all("table") if not t.get_text(strip=True)]
        assert not empty, f"empty table(s) rendered in EPUB: {empty}"


def test_invariant_no_leaked_pipe_table_syntax():
    """Broken markdownify pipe tables surface as literal '|...' lines — never ship them."""
    for body in _section_bodies(_deliver_fixture()):
        text = BeautifulSoup(body, "html.parser").get_text("\n")
        pipe_lines = [ln for ln in text.splitlines() if ln.strip().startswith("|")]
        assert not pipe_lines, f"leaked pipe-table lines: {pipe_lines}"


def test_genuine_data_tables_are_preserved_with_content():
    """Data tables (CASE 4 + CASE 5) must survive as real, non-empty tables."""
    body = "\n".join(_section_bodies(_deliver_fixture()))
    soup = BeautifulSoup(body, "html.parser")
    data_tables = [t for t in soup.find_all("table") if t.get_text(strip=True)]
    assert data_tables, "expected genuine data tables to be preserved as HTML tables"
    joined = " ".join(t.get_text(" ", strip=True) for t in data_tables)
    assert "Active users" in joined  # CASE 4
    assert "BTC" in joined  # CASE 5 (nested)
