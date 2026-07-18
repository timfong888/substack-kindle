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


def _section_xhtml(data: bytes, index: int = 0) -> str:
    """Return the decoded content of section_{index}.xhtml from the EPUB zip."""
    with _zip(data) as zf:
        name = next(n for n in zf.namelist() if n.endswith(f"section_{index}.xhtml"))
        return zf.read(name).decode("utf-8")


def _nav_top_links(data: bytes):
    """Return only top-level TOC links (no anchor fragment — newsletter-level entries)."""
    return [(text, href) for text, href in _nav_links(data) if "#" not in href]


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
    # Top-level count must stay N regardless of sub-heading children.
    data = build_job_epub(_sections(3), book_title="My Job")
    top = _nav_top_links(data)
    assert len(top) == 3
    assert [text for text, _ in top] == ["Newsletter 0", "Newsletter 1", "Newsletter 2"]


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
    assert len(_nav_top_links(data)) == 1
    with _zip(data) as zf:
        names = zf.namelist()
        assert names[0] == "mimetype"
        assert zf.read("mimetype") == b"application/epub+zip"
        assert zf.getinfo("mimetype").compress_type == zipfile.ZIP_STORED


def test_sections_content_is_present_in_epub():
    data = build_job_epub(_sections(2), book_title="J")
    with _zip(data) as zf:
        body = "\n".join(zf.read(n).decode("utf-8") for n in zf.namelist() if n.endswith(".xhtml"))
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
    data = build_job_epub(_sections(2), book_title="Custom", author="Weekly Mix")
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
    top = _nav_top_links(data)
    # 2 sections in, 2 top-level entries out — frontmatter does not inflate the count.
    assert len(top) == 2
    assert all("frontmatter" not in href for _text, href in top)


def test_no_subtitle_omits_frontmatter_and_description():
    # Back-compat: a digest built without a subtitle behaves like SAT-264 did —
    # no frontmatter chapter, no dc:description.
    data = build_job_epub(_sections(2), book_title="Plain")
    opf = _opf_text(data)
    assert "<dc:description" not in opf
    with _zip(data) as zf:
        assert not any(n.endswith("frontmatter.xhtml") for n in zf.namelist())


# --- SAT-281: CSS table stylesheet (Bug 1) -----------------------------------


def test_epub_has_css_stylesheet_file():
    """EPUB zip must contain a CSS stylesheet for table rendering."""
    data = build_job_epub(_sections(1), book_title="Test")
    with _zip(data) as zf:
        assert any("newsletter.css" in n for n in zf.namelist()), (
            "expected a newsletter.css file in the EPUB zip"
        )


def test_section_xhtml_references_css_stylesheet():
    """Every section XHTML must have a <link> to the newsletter stylesheet."""
    data = build_job_epub(_sections(2), book_title="Test")
    for i in range(2):
        xhtml = _section_xhtml(data, i)
        assert "newsletter.css" in xhtml, f"section_{i}.xhtml does not reference newsletter.css"


def test_pipe_table_markdown_renders_as_html_table_element():
    """Pipe-table markdown must appear as <table> in the XHTML body, not plain pipe text."""
    section = JobSection(
        title="Stats",
        markdown="| Metric | Value |\n|---|---|\n| Users | 1 000 |\n| Revenue | $5k |",
    )
    data = build_job_epub([section], book_title="Test")
    xhtml = _section_xhtml(data)
    assert "<table" in xhtml, "expected HTML <table> element in rendered XHTML"


# --- SAT-281: H1 downgrade + hierarchical TOC (Bug 2) -----------------------


def test_h1_in_markdown_body_is_downgraded_to_h2():
    """A # H1 in the article's own markdown must render as <h2>, never a bare <h1>.

    The only <h1> in a section is the SAT-550 article-title header
    (``<h1 class="article-title">``) injected by the builder — a single
    authoritative top-level heading. Headings that come from the newsletter's
    own body are still downgraded H1→H2 so they sit beneath it in a clean
    hierarchy and don't confuse Kindle's heading navigation.
    """
    section = JobSection(
        title="My Newsletter",
        markdown="# My Newsletter\n\n## Section One\n\nContent here.",
    )
    data = build_job_epub([section], book_title="Digest")
    xhtml = _section_xhtml(data)
    assert "<h1>" not in xhtml, "body-sourced H1 must be downgraded; found bare <h1>"
    assert "<h2" in xhtml, "expected at least one <h2> after H1 downgrade"


def test_section_with_h2_subheadings_has_child_nav_links():
    """A section with ## headings must produce child nav entries (the Kindle 'caret')."""
    section = JobSection(
        title="Token Dispatch",
        markdown="# Token Dispatch\n\n## Markets Update\n\nContent.\n\n## Protocol News\n\nMore.",
    )
    data = build_job_epub([section], book_title="Digest")
    all_links = _nav_links(data)
    child_texts = [text for text, href in all_links if "#" in href]
    assert "Markets Update" in child_texts, "expected 'Markets Update' as child nav entry"
    assert "Protocol News" in child_texts, "expected 'Protocol News' as child nav entry"


def test_section_without_subheadings_has_flat_nav_entry():
    """A section with no headings must produce exactly one flat TOC entry (no caret)."""
    section = JobSection(title="Plain Post", markdown="Just a paragraph with no headings.")
    data = build_job_epub([section], book_title="Digest")
    all_links = _nav_links(data)
    child_links = [href for _, href in all_links if "#" in href]
    assert child_links == [], f"expected no child links, got {child_links}"


# --- SAT-288: TOC sender prefix ---------------------------------------------------


def test_toc_includes_sender_prefix_when_sender_is_set():
    """TOC label must be 'Sender — Title' when sender is populated."""
    section = JobSection(
        title="Weekly Roundup",
        markdown="# Weekly Roundup\n\nBody.",
        sender="ByteByteGo",
    )
    data = build_job_epub([section], book_title="Digest")
    top = _nav_top_links(data)
    assert top[0][0] == "ByteByteGo — Weekly Roundup"


def test_toc_falls_back_to_title_only_when_sender_is_empty():
    """TOC label must be the bare title when sender is empty (default)."""
    section = JobSection(title="Solo Post", markdown="Body.")
    data = build_job_epub([section], book_title="Digest")
    top = _nav_top_links(data)
    assert top[0][0] == "Solo Post"


def test_publication_appears_only_in_header_not_dumped_into_prose():
    """Publication shows once, in the SAT-550 article header — never dumped into prose.

    The combined "Sender — Title" TOC-label string must not be concatenated into
    the body content, and the publication name appears exactly once (in the
    article-kicker header), not duplicated across paragraphs.
    """
    import re

    section = JobSection(
        title="The Article",
        markdown="# The Article\n\nBody paragraph.",
        sender="Mission Local",
    )
    data = build_job_epub([section], book_title="Digest")
    xhtml = _section_xhtml(data)
    body = re.search(r"<body>(.*?)</body>", xhtml, re.DOTALL).group(1)
    # The publication is present exactly once, inside the article header.
    assert 'class="article-kicker"' in body
    assert body.count("Mission Local") == 1
    # The TOC-label format string is never spliced into body prose.
    assert "Mission Local — The Article" not in body
    assert "Body paragraph." in body


def test_section_body_shows_article_title_and_publication_header():
    """Every article body renders a visible header with its title + publication (SAT-550).

    Happy path for "I don't know the publication or title": opening an article
    shows both at the top of the body, non-truncated, even when the source body
    does not lead with its own title heading.
    """
    import re

    section = JobSection(
        title="Weekly Roundup",
        markdown="Straight into the body with no leading title heading.",
        sender="ByteByteGo",
    )
    data = build_job_epub([section], book_title="Digest")
    xhtml = _section_xhtml(data)
    body = re.search(r"<body>(.*?)</body>", xhtml, re.DOTALL).group(1)
    # Title rendered as a visible heading in the body.
    assert 'class="article-title"' in body
    assert "Weekly Roundup" in body
    # Publication rendered as a visible kicker in the body.
    assert 'class="article-kicker"' in body
    assert "ByteByteGo" in body
    # The header precedes the article prose.
    assert body.index("ByteByteGo") < body.index("Straight into the body")
    assert body.index("Weekly Roundup") < body.index("Straight into the body")


def test_article_header_shows_title_without_kicker_when_sender_empty():
    """With no publication, the header still shows the title but omits the kicker."""
    import re

    section = JobSection(title="Solo Post", markdown="Body only.")
    data = build_job_epub([section], book_title="Digest")
    xhtml = _section_xhtml(data)
    body = re.search(r"<body>(.*?)</body>", xhtml, re.DOTALL).group(1)
    assert 'class="article-title"' in body
    assert "Solo Post" in body
    assert 'class="article-kicker"' not in body


def test_h1_converted_to_h2_shows_as_child_nav_entry():
    """A # Title heading (downgraded to H2) must appear as a child nav entry, not top-level."""
    section = JobSection(
        title="Newsletter 0",
        markdown="# Newsletter 0\n\nBody text.",
    )
    data = build_job_epub([section], book_title="Digest")
    all_links = _nav_links(data)
    top = [text for text, href in all_links if "#" not in href]
    children = [text for text, href in all_links if "#" in href]
    assert top == ["Newsletter 0"], f"expected one top-level entry, got {top}"
    assert "Newsletter 0" in children, "downgraded H1 must appear as child nav entry"
