"""Deterministic newsletter body parsing (SAT-244 / Reqs 8, 15).

HTML is converted to Markdown with libraries only (BeautifulSoup + markdownify) —
the LLM is never invoked on body text, so a run's processing cost stays library-
bound and does not grow with newsletter length. Converted Markdown is persisted
keyed by newsletter ID and retrievable at any later time.
"""

from __future__ import annotations

from bs4 import BeautifulSoup
from markdownify import markdownify as _markdownify

# Non-content tags whose text must never leak into the Markdown body.
_NOISE_TAGS = ("script", "style", "head", "meta", "link")


def html_to_markdown(html: str) -> str:
    """Convert an HTML body to Markdown deterministically.

    Same input always yields the same output; no model/network call is made.
    """
    if not html or not html.strip():
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(list(_NOISE_TAGS)):
        tag.decompose()
    markdown = _markdownify(str(soup), heading_style="ATX")
    return markdown.strip()


class InMemoryMarkdownStore:
    """Stores converted Markdown keyed by newsletter ID, retrievable at any time."""

    def __init__(self) -> None:
        self._by_id: dict[str, str] = {}

    def put(self, newsletter_id: str, markdown: str) -> None:
        self._by_id[newsletter_id] = markdown

    def get(self, newsletter_id: str) -> str | None:
        return self._by_id.get(newsletter_id)

    def parse_and_store(self, newsletter_id: str, html: str) -> str:
        markdown = html_to_markdown(html)
        self._by_id[newsletter_id] = markdown
        return markdown
