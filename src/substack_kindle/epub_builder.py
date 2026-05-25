"""Readable-EPUB builder (SAT-245 / Reqs 2, 14, 18).

Renders a newsletter's stored Markdown to a valid, cleanly formatted EPUB with
libraries only (markdown -> XHTML, ebooklib for assembly). Semantic structure
(headings, paragraphs) is preserved. Image policy follows the documented default
for the PRD open item: **inline images that fit under a byte budget, otherwise
strip them** (images with no provided bytes are also stripped — Kindle does not
fetch remote images).
"""

from __future__ import annotations

import mimetypes
import os
import tempfile
from dataclasses import dataclass, field

import markdown as _markdown
from bs4 import BeautifulSoup
from ebooklib import epub

# Per-EPUB image budget. Kept well under the Postmark 10 MB message cap (E2/SAT-251
# enforces the overall send-size limit separately).
DEFAULT_IMAGE_BUDGET_BYTES = 5 * 1024 * 1024


@dataclass
class NewsletterContent:
    """One newsletter to render into an EPUB.

    ``images`` maps a source reference as it appears in the Markdown (a URL or
    ``cid:`` token) to the already-fetched image bytes. Fetching is upstream/out
    of scope here — the builder never makes network calls.
    """

    title: str
    markdown: str
    images: dict[str, bytes] = field(default_factory=dict)
    identifier: str | None = None
    language: str = "en"


def _apply_image_policy(
    body_html: str, images: dict[str, bytes], budget_bytes: int
) -> tuple[str, dict[str, bytes]]:
    soup = BeautifulSoup(body_html, "html.parser")
    embedded: dict[str, bytes] = {}
    used = 0
    for index, img in enumerate(soup.find_all("img")):
        src = img.get("src")
        data = images.get(src) if src else None
        if data is not None and used + len(data) <= budget_bytes:
            ext = os.path.splitext(src)[1] or ".img"
            local_name = f"images/img_{index}{ext}"
            img["src"] = local_name
            embedded[local_name] = data
            used += len(data)
        else:
            img.decompose()  # strip: over budget, or no bytes available
    return str(soup), embedded


def _chapter_xhtml(title: str, body_html: str) -> str:
    return (
        f"<html><head><title>{title}</title></head>"
        f"<body>{body_html}</body></html>"
    )


def _to_bytes(book: epub.EpubBook) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "out.epub")
        epub.write_epub(path, book)
        with open(path, "rb") as fh:
            return fh.read()


def build_epub(
    content: NewsletterContent, *, image_budget_bytes: int = DEFAULT_IMAGE_BUDGET_BYTES
) -> bytes:
    """Render ``content`` to EPUB bytes (valid, readable, image policy applied)."""
    body_html = _markdown.markdown(content.markdown, extensions=["extra"])
    body_html, embedded = _apply_image_policy(body_html, content.images, image_budget_bytes)

    book = epub.EpubBook()
    book.set_identifier(content.identifier or f"urn:newsletter:{content.title}")
    book.set_title(content.title)
    book.set_language(content.language)

    chapter = epub.EpubHtml(title=content.title, file_name="chapter.xhtml", lang=content.language)
    chapter.content = _chapter_xhtml(content.title, body_html)
    book.add_item(chapter)

    for local_name, data in embedded.items():
        media_type = mimetypes.guess_type(local_name)[0] or "application/octet-stream"
        book.add_item(
            epub.EpubItem(
                uid=local_name,
                file_name=local_name,
                media_type=media_type,
                content=data,
            )
        )

    book.toc = (chapter,)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", chapter]
    return _to_bytes(book)
