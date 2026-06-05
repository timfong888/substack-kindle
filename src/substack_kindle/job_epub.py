"""One-EPUB-per-job assembly with a navigable TOC (SAT-246 / Reqs 14, 18).

A job that collects N deduped newsletters produces exactly one EPUB whose table
of contents has N entries, each linking to that newsletter's section. A single
newsletter yields a valid one-entry EPUB. Built with libraries only (markdown ->
XHTML, ebooklib); no LLM/network in this path.
"""

from __future__ import annotations

import html as _html
import os
import re
import tempfile
from dataclasses import dataclass
from urllib.parse import quote

import markdown as _markdown
from bs4 import BeautifulSoup
from ebooklib import epub


@dataclass
class JobSection:
    """One newsletter rendered as a section of the job's EPUB."""

    title: str
    markdown: str


# CSS injected into every section so data tables render on Kindle.
# Kindle supports basic CSS; border-collapse + padding is enough for readability.
_NEWSLETTER_CSS = (
    b"table{border-collapse:collapse;width:100%;margin:1em 0}"
    b"th,td{border:1px solid #ccc;padding:.4em .6em;text-align:left;vertical-align:top}"
    b"th{background-color:#f2f2f2;font-weight:bold}"
)
_CSS_FILE = "style/newsletter.css"

_NON_ALPHANUM_RE = re.compile(r"[^\w\s-]")
_WHITESPACE_RE = re.compile(r"[\s_]+")


def _slug(text: str) -> str:
    """URL-safe anchor id from heading text."""
    text = _NON_ALPHANUM_RE.sub("", text.lower())
    return _WHITESPACE_RE.sub("-", text).strip("-") or "section"


def _post_process_html(html: str) -> tuple[str, list[tuple[str, str]]]:
    """Downgrade H1→H2; add IDs to H2/H3; return (modified_html, [(id, text)]).

    H1 headings are the newsletter title — already represented as the NCX/nav
    top-level label. Rendering them as H1 inside the body creates a visual
    duplicate and an ambiguous heading hierarchy on Kindle. Downgrading to H2
    makes the title appear as the first expandable child nav entry (the
    "caret"), consistent with the user's desired TOC UX.

    Only H2 headings are collected for child nav entries; H3+ are structural
    within sections and would over-inflate the TOC.
    """
    soup = BeautifulSoup(html, "html.parser")
    for h1 in soup.find_all("h1"):
        h1.name = "h2"
    seen: dict[str, int] = {}
    sub_headings: list[tuple[str, str]] = []
    for tag in soup.find_all(["h2", "h3"]):
        text = tag.get_text(strip=True)
        base = _slug(text)
        n = seen.get(base, 0)
        seen[base] = n + 1
        uid = base if n == 0 else f"{base}-{n}"
        if not tag.get("id"):
            tag["id"] = uid
        else:
            uid = tag["id"]
        if tag.name == "h2":
            sub_headings.append((uid, text))
    return str(soup), sub_headings


def _chapter_xhtml(title: str, body_html: str) -> str:
    # Escape the title and declare the XHTML namespace so a title containing
    # &, <, or > can't produce a malformed content document.
    return (
        f'<html xmlns="http://www.w3.org/1999/xhtml">'
        f'<head><title>{_html.escape(title)}</title></head>'
        f"<body>{body_html}</body></html>"
    )


def _to_bytes(book: epub.EpubBook) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "job.epub")
        epub.write_epub(path, book)
        with open(path, "rb") as fh:
            return fh.read()


_DEFAULT_AUTHOR = "Substack Digest"


def _frontmatter_xhtml(book_title: str, subtitle: str) -> str:
    """Title page shown as the first chapter so the subtitle is visible
    inside the book (the EPUB cover area doesn't render reliably on Kindle)."""
    return (
        '<html xmlns="http://www.w3.org/1999/xhtml">'
        f"<head><title>{_html.escape(book_title)}</title></head>"
        "<body>"
        f"<h1>{_html.escape(book_title)}</h1>"
        f"<h4>{_html.escape(subtitle)}</h4>"
        "</body></html>"
    )


def build_job_epub(
    sections: list[JobSection],
    *,
    book_title: str,
    identifier: str | None = None,
    author: str = _DEFAULT_AUTHOR,
    subtitle: str | None = None,
) -> bytes:
    """Build a single EPUB for a job from its (already deduped) sections.

    A constant ``author`` (``dc:creator``) groups every issue under one entry
    on the Kindle library — see SAT-264.

    ``subtitle`` (SAT-272), when provided, is written both to the OPF
    ``dc:description`` (visible in readers' info panel) and as an H4 line on
    a front-matter page rendered as the first chapter. The front-matter page
    does NOT appear in the navigable TOC — it's spine-only — so the TOC stays
    one entry per newsletter section.

    Each section XHTML embeds a CSS stylesheet for table rendering (SAT-281).
    H1 headings in the body are downgraded to H2 so the newsletter title
    appears as the first child nav entry rather than a duplicate top-level
    heading (SAT-281 TOC fix).
    """
    if not sections:
        raise ValueError("a job EPUB needs at least one newsletter section")

    book = epub.EpubBook()
    book.set_identifier(identifier or f"urn:job:{quote(book_title, safe='')}")
    book.set_title(book_title)
    book.add_author(author)
    book.set_language("en")
    if subtitle:
        book.add_metadata("DC", "description", subtitle)

    # Shared CSS item — referenced by every section chapter.
    css_item = epub.EpubItem(
        uid="newsletter-styles",
        file_name=_CSS_FILE,
        media_type="text/css",
        content=_NEWSLETTER_CSS,
    )
    book.add_item(css_item)

    frontmatter = None
    if subtitle:
        frontmatter = epub.EpubHtml(
            title=book_title, file_name="frontmatter.xhtml", lang="en"
        )
        frontmatter.content = _frontmatter_xhtml(book_title, subtitle)
        book.add_item(frontmatter)

    chapters: list[epub.EpubHtml] = []
    toc_entries: list = []

    for index, section in enumerate(sections):
        raw_html = _markdown.markdown(section.markdown, extensions=["extra"])
        processed_html, sub_headings = _post_process_html(raw_html)

        chapter = epub.EpubHtml(
            title=section.title, file_name=f"section_{index}.xhtml", lang="en"
        )
        chapter.content = _chapter_xhtml(section.title, processed_html)
        chapter.add_link(href=_CSS_FILE, rel="stylesheet", type="text/css")
        book.add_item(chapter)
        chapters.append(chapter)

        # Build hierarchical TOC entry: top-level chapter link + H2 child links.
        # Sections with no H2 headings get a flat entry (no caret on Kindle).
        if sub_headings:
            children = tuple(
                epub.Link(
                    f"section_{index}.xhtml#{anchor_id}",
                    text,
                    uid=f"nav-{index}-{anchor_id}",
                )
                for anchor_id, text in sub_headings
            )
            toc_entries.append((chapter, children))
        else:
            toc_entries.append(chapter)

    # NCX + Nav give both EPUB2 (Kindle) and EPUB3 readers a working TOC.
    # Frontmatter is intentionally absent from the TOC — it's a title page.
    book.toc = tuple(toc_entries)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    spine_head = [frontmatter, "nav"] if frontmatter is not None else ["nav"]
    book.spine = [*spine_head, *chapters]
    return _to_bytes(book)
