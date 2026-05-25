"""Tests for one-EPUB-per-job assembly with a navigable TOC (SAT-246 / #10, Reqs 14, 18).

Acceptance:
- A job of N deduped newsletters produces exactly one EPUB with N TOC entries.
- Each TOC headline links to the corresponding newsletter section in the EPUB.
- A single-newsletter job produces a valid one-entry EPUB.
"""

import zipfile
from html.parser import HTMLParser
from io import BytesIO

import pytest

from substack_kindle.job_epub import JobSection, build_job_epub


def _zip(data: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(BytesIO(data))


class _AnchorCollector(HTMLParser):
    """Collect (text, href) for every <a> in the nav (stdlib only — no extra deps)."""

    def __init__(self):
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append(("".join(self._text).strip(), self._href))
            self._href = None


def _nav_links(data: bytes):
    """Return (text, href) pairs from the EPUB3 toc nav."""
    with _zip(data) as zf:
        nav_name = next(n for n in zf.namelist() if n.endswith("nav.xhtml"))
        nav_html = zf.read(nav_name).decode("utf-8")
    collector = _AnchorCollector()
    collector.feed(nav_html)
    return collector.links


def _sections(n):
    return [
        JobSection(title=f"Newsletter {i}", markdown=f"# Newsletter {i}\n\nBody {i}.")
        for i in range(n)
    ]


def test_n_sections_produce_n_toc_entries():
    data = build_job_epub(_sections(3), book_title="My Job")
    links = _nav_links(data)
    assert len(links) == 3
    assert [text for text, _ in links] == ["Newsletter 0", "Newsletter 1", "Newsletter 2"]


def test_each_toc_entry_links_to_an_existing_section_file():
    data = build_job_epub(_sections(3), book_title="My Job")
    links = _nav_links(data)
    with _zip(data) as zf:
        names = set(zf.namelist())
        for _text, href in links:
            target = href.split("#")[0].split("/")[-1]
            assert any(n.endswith(target) for n in names), f"missing section file for {href}"


def test_single_newsletter_job_is_valid_one_entry_epub():
    data = build_job_epub(_sections(1), book_title="Solo")
    links = _nav_links(data)
    assert len(links) == 1
    with _zip(data) as zf:
        names = zf.namelist()
        assert names[0] == "mimetype"
        assert zf.read("mimetype") == b"application/epub+zip"
        assert zf.getinfo("mimetype").compress_type == zipfile.ZIP_STORED


def test_sections_content_is_present_in_epub():
    data = build_job_epub(_sections(2), book_title="J")
    with _zip(data) as zf:
        body = "\n".join(
            zf.read(n).decode("utf-8") for n in zf.namelist() if n.endswith(".xhtml")
        )
    assert "Body 0." in body
    assert "Body 1." in body


def test_exactly_one_epub_artifact_is_returned():
    data = build_job_epub(_sections(4), book_title="J")
    assert isinstance(data, bytes)
    # It is a single EPUB container (one OPF package document).
    with _zip(data) as zf:
        opfs = [n for n in zf.namelist() if n.endswith(".opf")]
    assert len(opfs) == 1


def test_ncx_present_for_kindle_compatibility():
    data = build_job_epub(_sections(2), book_title="J")
    with _zip(data) as zf:
        assert any(n.endswith(".ncx") for n in zf.namelist())


def test_empty_job_raises():
    with pytest.raises(ValueError):
        build_job_epub([], book_title="Empty")
