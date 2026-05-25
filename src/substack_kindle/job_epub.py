"""One-EPUB-per-job assembly with a navigable TOC (SAT-246 / Reqs 14, 18).

A job that collects N deduped newsletters produces exactly one EPUB whose table
of contents has N entries, each linking to that newsletter's section. A single
newsletter yields a valid one-entry EPUB. Built with libraries only (markdown ->
XHTML, ebooklib); no LLM/network in this path.
"""

from __future__ import annotations

import html as _html
import os
import tempfile
from dataclasses import dataclass
from urllib.parse import quote

import markdown as _markdown
from ebooklib import epub


@dataclass
class JobSection:
    """One newsletter rendered as a section of the job's EPUB."""

    title: str
    markdown: str


def _chapter_xhtml(title: str, body_html: str) -> str:
    # Escape the title and declare the XHTML namespace so a title containing
    # &, <, or > can't produce a malformed content document.
    return (
        f'<html xmlns="http://www.w3.org/1999/xhtml">'
        f"<head><title>{_html.escape(title)}</title></head>"
        f"<body>{body_html}</body></html>"
    )


def _to_bytes(book: epub.EpubBook) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "job.epub")
        epub.write_epub(path, book)
        with open(path, "rb") as fh:
            return fh.read()


def build_job_epub(
    sections: list[JobSection], *, book_title: str, identifier: str | None = None
) -> bytes:
    """Build a single EPUB for a job from its (already deduped) sections."""
    if not sections:
        raise ValueError("a job EPUB needs at least one newsletter section")

    book = epub.EpubBook()
    book.set_identifier(identifier or f"urn:job:{quote(book_title, safe='')}")
    book.set_title(book_title)
    book.set_language("en")

    chapters = []
    for index, section in enumerate(sections):
        body_html = _markdown.markdown(section.markdown, extensions=["extra"])
        chapter = epub.EpubHtml(
            title=section.title, file_name=f"section_{index}.xhtml", lang="en"
        )
        chapter.content = _chapter_xhtml(section.title, body_html)
        book.add_item(chapter)
        chapters.append(chapter)

    # One TOC entry per section, each linking to its chapter; NCX + Nav give both
    # EPUB2 (Kindle) and EPUB3 readers a working table of contents.
    book.toc = tuple(chapters)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", *chapters]
    return _to_bytes(book)
