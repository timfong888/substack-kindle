"""Single-file delivery path: local file -> EPUB -> Postmark (SAT-830).

Covers the conversion module directly and the ``send-file`` CLI subcommand
end-to-end with an injected HTTP transport. No live network, no Kindle.
"""

from pathlib import Path

import pytest

from substack_kindle.cli import main
from substack_kindle.send_file import (
    UnsupportedFileType,
    build_single_file_epub,
    read_as_markdown,
    title_from_filename,
)


class _RecordingHttpxPost:
    """Captures the Postmark request; returns a canned success response."""

    def __init__(self):
        self.calls = []

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"MessageID": "msg-1", "ErrorCode": 0, "Message": "OK"}

        text = ""

    def __call__(self, url, *, json, headers, timeout):
        self.calls.append(
            {"url": url, "json": json, "headers": headers, "timeout": timeout}
        )
        return self._Resp()


def _env(**overrides):
    base = {
        "POSTMARK_SERVER_TOKEN": "postmark-token",
        "WHITELIST_EMAIL": "digest@example.com",
        "KINDLE_EMAIL": "reader@kindle.com",
    }
    base.update(overrides)
    return base


# --- conversion -----------------------------------------------------------


def test_markdown_is_read_verbatim():
    assert (
        read_as_markdown(b"# Heading\n\nBody text.\n", filename="note.md")
        == "# Heading\n\nBody text.\n"
    )


def test_txt_is_supported():
    assert read_as_markdown(b"plain body", filename="note.txt") == "plain body"


def test_unsupported_extension_is_rejected():
    with pytest.raises(UnsupportedFileType) as exc:
        read_as_markdown(b"nope", filename="sheet.xlsx")
    assert ".xlsx" in str(exc.value)


def test_empty_input_is_rejected():
    with pytest.raises(ValueError, match="no text"):
        build_single_file_epub(b"   \n\n", filename="empty.md")


def test_title_from_filename_humanizes_the_stem():
    assert title_from_filename("my-research_note.pdf") == "my research note"


def _epub_text(epub_bytes: bytes) -> str:
    """Decompressed text of every XHTML/OPF entry — the EPUB zip hides it otherwise."""
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(epub_bytes)) as zf:
        return "\n".join(
            zf.read(n).decode("utf-8", "ignore")
            for n in zf.namelist()
            if n.endswith((".xhtml", ".opf", ".html"))
        )


def test_epub_is_built_with_title_from_filename():
    epub_bytes = build_single_file_epub(
        b"# Inner\n\nBody.\n", filename="my-research-note.md"
    )
    assert epub_bytes.startswith(b"PK")  # zip container
    assert "my research note" in _epub_text(epub_bytes).lower()


def test_explicit_title_overrides_filename():
    epub_bytes = build_single_file_epub(
        b"# Inner\n\nBody.\n", filename="raw.md", title="Chosen Title"
    )
    assert "Chosen Title" in _epub_text(epub_bytes)


# --- PDF extraction -------------------------------------------------------

_FIXTURE_PDF = Path(__file__).parent / "fixtures" / "table_document.pdf"


def _fixture_pdf_bytes() -> bytes:
    return _FIXTURE_PDF.read_bytes()


def test_pdf_prose_is_extracted():
    markdown = read_as_markdown(_fixture_pdf_bytes(), filename="doc.pdf")
    assert "synthetic fixture prose" in markdown
    assert "Closing paragraph after the table." in markdown


def test_pdf_ruled_table_becomes_a_markdown_table():
    """The pypdf regression that motivated pdfplumber: tables must survive."""
    markdown = read_as_markdown(_fixture_pdf_bytes(), filename="doc.pdf")

    assert "| Objective | Total | Group A |" in markdown
    assert "|---|---|---|" in markdown
    assert "| Reducing widget latency | 70.3% | 66.7% |" in markdown
    assert "| Improving widget colour | 61.0% | 68.1% |" in markdown
    assert "| Lowering widget cost | 49.4% | 59.7% |" in markdown


def test_pdf_table_cells_are_not_also_emitted_as_loose_text():
    """Table regions are cut from the text layer, so cells appear exactly once."""
    markdown = read_as_markdown(_fixture_pdf_bytes(), filename="doc.pdf")
    assert markdown.count("Reducing widget latency") == 1


def test_pdf_table_survives_into_the_epub():
    epub_bytes = build_single_file_epub(_fixture_pdf_bytes(), filename="doc.pdf")
    html = _epub_text(epub_bytes)
    assert "<table>" in html
    assert "Reducing widget latency" in html


def test_scanned_pdf_with_no_text_layer_raises(tmp_path):
    """No OCR on this path — fail loudly rather than deliver a blank EPUB."""
    import pdfplumber  # noqa: F401  (ensures the extractor is installed)

    blank = (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
        b"trailer<</Root 1 0 R>>\n"
    )
    with pytest.raises(ValueError, match="no text"):
        build_single_file_epub(blank, filename="scan.pdf")


# --- CLI ------------------------------------------------------------------


def test_send_file_subcommand_sends_via_postmark(tmp_path):
    src = tmp_path / "note.md"
    src.write_text("# Heading\n\nBody text.\n")
    recorder = _RecordingHttpxPost()

    exit_code = main(
        argv=["send-file", str(src)],
        env=_env(),
        http_post=recorder,
    )

    assert exit_code == 0
    assert len(recorder.calls) == 1
    payload = recorder.calls[0]["json"]
    assert payload["To"] == "reader@kindle.com"
    assert payload["From"] == "digest@example.com"
    assert payload["Attachments"][0]["ContentType"] == "application/epub+zip"
    assert payload["Attachments"][0]["Name"] == "note.epub"


def test_send_file_uses_explicit_title(tmp_path):
    src = tmp_path / "note.md"
    src.write_text("# Heading\n\nBody.\n")
    recorder = _RecordingHttpxPost()

    exit_code = main(
        argv=["send-file", str(src), "--title", "Custom Subject"],
        env=_env(),
        http_post=recorder,
    )

    assert exit_code == 0
    assert recorder.calls[0]["json"]["Subject"] == "Custom Subject"


def test_send_file_does_not_require_feeds_registry(tmp_path):
    """The digest path reads feeds.json; send-file must not touch it."""
    src = tmp_path / "note.md"
    src.write_text("# Heading\n\nBody.\n")
    recorder = _RecordingHttpxPost()

    exit_code = main(
        argv=["send-file", str(src)],
        env=_env(FEEDS_PATH=str(tmp_path / "does-not-exist.json")),
        http_post=recorder,
    )

    assert exit_code == 0


def test_send_file_reports_missing_file(tmp_path, capsys):
    exit_code = main(
        argv=["send-file", str(tmp_path / "absent.md")],
        env=_env(),
        http_post=_RecordingHttpxPost(),
    )
    assert exit_code == 1
    assert "absent.md" in capsys.readouterr().err


def test_send_file_reports_unsupported_type(tmp_path, capsys):
    src = tmp_path / "sheet.xlsx"
    src.write_text("nope")
    exit_code = main(
        argv=["send-file", str(src)],
        env=_env(),
        http_post=_RecordingHttpxPost(),
    )
    assert exit_code == 1
    assert ".xlsx" in capsys.readouterr().err


def test_send_file_enforces_local_part_collision_guard(tmp_path, capsys):
    """SAT-270 guard applies to the one-off path too."""
    from substack_kindle.whitelist_check import LocalPartCollision

    src = tmp_path / "note.md"
    src.write_text("# Heading\n\nBody.\n")
    with pytest.raises(LocalPartCollision):
        main(
            argv=["send-file", str(src)],
            env=_env(WHITELIST_EMAIL="reader@fong888.com", KINDLE_EMAIL="reader@kindle.com"),
            http_post=_RecordingHttpxPost(),
        )


# --- back-compat ----------------------------------------------------------


def test_legacy_flat_argv_still_routes_to_the_digest_run(monkeypatch, tmp_path):
    """`substack-kindle --start X --end Y` (no subcommand) must keep working."""
    called = {}

    def _fake_fetch_posts(http_get, **kwargs):
        called["yes"] = True
        return []

    monkeypatch.setattr("substack_kindle.cli.fetch_posts", _fake_fetch_posts)
    exit_code = main(
        argv=["--start", "2026-06-14", "--end", "2026-06-24"],
        env=_env(),
        feeds=["https://example.substack.com/feed"],
        http_post=_RecordingHttpxPost(),
        state_path=tmp_path / "state.json",
    )
    assert called.get("yes") is True
    assert exit_code == 0


def test_explicit_run_subcommand_routes_to_the_digest_run(monkeypatch, tmp_path):
    called = {}

    def _fake_fetch_posts(http_get, **kwargs):
        called["yes"] = True
        return []

    monkeypatch.setattr("substack_kindle.cli.fetch_posts", _fake_fetch_posts)
    exit_code = main(
        argv=["run", "--start", "2026-06-14", "--end", "2026-06-24"],
        env=_env(),
        feeds=["https://example.substack.com/feed"],
        http_post=_RecordingHttpxPost(),
        state_path=tmp_path / "state.json",
    )
    assert called.get("yes") is True
    assert exit_code == 0
