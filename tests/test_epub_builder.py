"""Tests for the readable-EPUB builder (SAT-245 / #9, Reqs 2, 14, 18).

Acceptance:
- Output is a valid EPUB (structurally; round-trips through an EPUB reader).
- Image policy: default is inline-if-under-budget, else strip.
- Semantic structure (headings, paragraphs) is preserved.
"""

import zipfile
from io import BytesIO

from ebooklib import epub

from substack_kindle.epub_builder import (
    DEFAULT_IMAGE_BUDGET_BYTES,
    NewsletterContent,
    build_epub,
)

# 1x1 transparent PNG.
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000154a24f1e0000000049454e44ae426082"
)


def _read_epub(data: bytes):
    return epub.read_epub(BytesIO(data))


def test_returns_bytes_with_correct_mimetype_first_entry():
    data = build_epub(NewsletterContent(title="Edition 1", markdown="# Hi\n\nbody"))
    assert isinstance(data, bytes)
    with zipfile.ZipFile(BytesIO(data)) as zf:
        names = zf.namelist()
        assert names[0] == "mimetype"
        info = zf.getinfo("mimetype")
        assert zf.read("mimetype") == b"application/epub+zip"
        assert info.compress_type == zipfile.ZIP_STORED  # spec: stored, uncompressed, first


def test_epub_round_trips_through_reader_with_title():
    data = build_epub(NewsletterContent(title="My Newsletter", markdown="text"))
    book = _read_epub(data)
    assert book.get_metadata("DC", "title")[0][0] == "My Newsletter"


def test_semantic_structure_preserved():
    md = "# Heading One\n\n## Heading Two\n\nA paragraph of text.\n\nAnother paragraph."
    data = build_epub(NewsletterContent(title="T", markdown=md))
    with zipfile.ZipFile(BytesIO(data)) as zf:
        xhtml = "\n".join(
            zf.read(n).decode("utf-8") for n in zf.namelist() if n.endswith((".xhtml", ".html"))
        )
    assert "<h1" in xhtml and "Heading One" in xhtml
    assert "<h2" in xhtml and "Heading Two" in xhtml
    assert "<p>" in xhtml and "Another paragraph." in xhtml


def test_image_inlined_when_under_budget():
    md = "Look: ![pic](cid:hero.png)"
    content = NewsletterContent(title="T", markdown=md, images={"cid:hero.png": _PNG})
    data = build_epub(content, image_budget_bytes=DEFAULT_IMAGE_BUDGET_BYTES)
    with zipfile.ZipFile(BytesIO(data)) as zf:
        names = zf.namelist()
        # The image bytes are embedded as an item...
        assert any(n.endswith(".png") for n in names)
        embedded = next(zf.read(n) for n in names if n.endswith(".png"))
        assert embedded == _PNG
        # ...and the remote/cid src no longer appears in the XHTML.
        xhtml = "\n".join(zf.read(n).decode("utf-8") for n in names if n.endswith(".xhtml"))
        assert "cid:hero.png" not in xhtml
        assert "<img" in xhtml


def test_image_url_with_query_string_yields_clean_entry_name():
    md = "CDN: ![pic](https://cdn.example.com/photo.jpg?w=800&fmt=webp)"
    content = NewsletterContent(
        title="T", markdown=md, images={"https://cdn.example.com/photo.jpg?w=800&fmt=webp": _PNG}
    )
    data = build_epub(content)
    with zipfile.ZipFile(BytesIO(data)) as zf:
        names = zf.namelist()
        # Extension comes from the URL path only — no "?" in the zip entry name.
        png_names = [n for n in names if n.endswith(".jpg")]
        assert png_names, names
        assert all("?" not in n for n in names)


def test_title_with_special_chars_produces_valid_epub():
    data = build_epub(NewsletterContent(title="Q&A: Tech & <Business>", markdown="body"))
    book = _read_epub(data)  # round-trips through the reader = well-formed XML
    assert book.get_metadata("DC", "title")[0][0] == "Q&A: Tech & <Business>"


def test_image_stripped_when_over_budget():
    md = "Big: ![pic](cid:big.png)"
    content = NewsletterContent(title="T", markdown=md, images={"cid:big.png": _PNG})
    data = build_epub(content, image_budget_bytes=len(_PNG) - 1)  # budget too small
    with zipfile.ZipFile(BytesIO(data)) as zf:
        names = zf.namelist()
        assert not any(n.endswith(".png") for n in names)
        xhtml = "\n".join(zf.read(n).decode("utf-8") for n in names if n.endswith(".xhtml"))
        assert "<img" not in xhtml
        assert "cid:big.png" not in xhtml


def test_image_without_bytes_is_stripped():
    md = "Remote: ![pic](https://example.com/x.png)"
    content = NewsletterContent(title="T", markdown=md)  # no image bytes provided
    data = build_epub(content)
    with zipfile.ZipFile(BytesIO(data)) as zf:
        xhtml = "\n".join(
            zf.read(n).decode("utf-8") for n in zf.namelist() if n.endswith(".xhtml")
        )
    assert "<img" not in xhtml


def test_no_llm_in_build_path():
    with open(__import__("substack_kindle.epub_builder", fromlist=["x"]).__file__) as fh:
        source = fh.read().lower()
    for forbidden in ("anthropic", "openai", "claude", "litellm"):
        assert forbidden not in source
