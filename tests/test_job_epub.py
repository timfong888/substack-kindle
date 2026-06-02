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
    """Collect (text, href) for <a> tags inside the toc nav only (stdlib, no deps).

    Scoped to ``<nav epub:type="toc">`` so a future landmarks nav from ebooklib
    can't inflate the TOC-entry count.
    """

    def __init__(self):
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._in_toc = False
        self._nav_depth = 0
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        ad = dict(attrs)
        if tag == "nav":
            if not self._in_toc and ad.get("epub:type") == "toc":
                self._in_toc = True
                self._nav_depth = 1
            elif self._in_toc:
                self._nav_depth += 1
            return
        if self._in_toc and tag == "a":
            self._href = ad.get("href")
            self._text = []

    def handle_data(self, data):
        if self._in_toc and self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if self._in_toc and tag == "a" and self._href is not None:
            self.links.append(("".join(self._text).strip(), self._href))
            self._href = None
        elif self._in_toc and tag == "nav":
            self._nav_depth -= 1
            if self._nav_depth == 0:
                self._in_toc = False


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


def test_special_characters_in_titles_produce_valid_epub():
    sections = [JobSection(title="Growth & Strategy <Weekly>", markdown="# Hi\n\nbody")]
    data = build_job_epub(sections, book_title="Sophia's Weekly & More")
    # Round-trips through a reader = well-formed XML; nav reflects the escaped title.
    links = _nav_links(data)
    assert links[0][0] == "Growth & Strategy <Weekly>"
    with _zip(data) as zf:
        assert any(n.endswith(".opf") for n in zf.namelist())


# --- Author field (SAT-264) ---------------------------------------------------
# A constant author groups every digest under one entry in the Kindle library,
# instead of scattering them under "Unknown".


def _opf_text(data: bytes) -> str:
    with _zip(data) as zf:
        opf_name = next(n for n in zf.namelist() if n.endswith(".opf"))
        return zf.read(opf_name).decode("utf-8", errors="replace")


def test_default_author_is_substack_digest():
    data = build_job_epub(_sections(2), book_title="Substacks · May 19–26, 2026")
    assert "<dc:creator" in _opf_text(data)
    assert ">Substack Digest<" in _opf_text(data)


def test_explicit_author_is_preserved():
    data = build_job_epub(
        _sections(2), book_title="Custom", author="Weekly Mix"
    )
    assert ">Weekly Mix<" in _opf_text(data)


# --- Subtitle / front-matter page (SAT-272) -----------------------------------
# The subheader (e.g. "Newsletters to Kindle v0.2.0") must be visible both in
# OPF metadata (info panels) and inside the book on the first page.


def test_subtitle_writes_dc_description():
    data = build_job_epub(
        _sections(2),
        book_title="Newsletter Digest: May 3 – May 9 2026",
        subtitle="Newsletters to Kindle v0.2.0",
    )
    opf = _opf_text(data)
    assert "<dc:description" in opf
    assert ">Newsletters to Kindle v0.2.0<" in opf


def test_subtitle_renders_frontmatter_chapter_with_h4():
    data = build_job_epub(
        _sections(2),
        book_title="Newsletter Digest: May 3 – May 9 2026",
        subtitle="Newsletters to Kindle v0.2.0",
    )
    with _zip(data) as zf:
        names = zf.namelist()
        assert any(n.endswith("frontmatter.xhtml") for n in names)
        fm_name = next(n for n in names if n.endswith("frontmatter.xhtml"))
        fm = zf.read(fm_name).decode("utf-8")
    assert "<h1>Newsletter Digest: May 3 – May 9 2026</h1>" in fm
    assert "<h4>Newsletters to Kindle v0.2.0</h4>" in fm


def test_subtitle_frontmatter_is_not_in_toc():
    # The frontmatter chapter is a title page, not a navigable entry.
    data = build_job_epub(
        _sections(2),
        book_title="x",
        subtitle="Newsletters to Kindle v0.2.0",
    )
    links = _nav_links(data)
    # 2 sections in, 2 entries out — frontmatter does not inflate the count.
    assert len(links) == 2
    assert all("frontmatter" not in href for _text, href in links)


def test_no_subtitle_omits_frontmatter_and_description():
    # Back-compat: a digest built without a subtitle behaves like SAT-264 did —
    # no frontmatter chapter, no dc:description.
    data = build_job_epub(_sections(2), book_title="Plain")
    opf = _opf_text(data)
    assert "<dc:description" not in opf
    with _zip(data) as zf:
        assert not any(n.endswith("frontmatter.xhtml") for n in zf.namelist())
