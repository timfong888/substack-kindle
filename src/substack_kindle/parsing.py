"""Deterministic newsletter body parsing (SAT-244 / Reqs 8, 15).

HTML is converted to Markdown with libraries only (BeautifulSoup + markdownify) —
the LLM is never invoked on body text, so a run's processing cost stays library-
bound and does not grow with newsletter length. Converted Markdown is persisted
keyed by newsletter ID and retrievable at any later time.
"""

import re

from bs4 import BeautifulSoup
from markdownify import markdownify as _markdownify

# Non-content tags whose text must never leak into the Markdown body.
_NOISE_TAGS = ("script", "style", "head", "meta", "link")

# Layout tags. Newsletter emails (Substack, beehiiv, …) lay their whole body out
# in nested HTML tables for positioning, not as data grids. Converting those to
# Markdown tables produces broken/nested table syntax that the downstream
# Markdown->HTML render then collapses, silently dropping most of the body
# (SAT-261). Flattening them to <div> first makes markdownify emit normal block
# content (headings/paragraphs), so the full text survives the round-trip.
_LAYOUT_TAGS = ("table", "thead", "tbody", "tfoot", "tr", "td", "th")

# href substrings that mark click-tracking / redirect links. Their URLs are
# base64 redirect noise on a Kindle, so we keep the link text but drop the href.
_TRACKING_HREF_MARKERS = (
    "/redirect/",
    "substack.com/redirect",
    "mg2.substack.com",
    "eotrx.",
    "beehiiv.com",
    "list-manage.com",
    "mailchi.mp",
    "/c/eJ",
    "/o/eJ",
)


def _is_tracking_href(href: str) -> bool:
    """True if ``href`` is a click-tracking/redirect URL (noise, not a real link)."""
    low = href.lower()
    if any(marker in low for marker in _TRACKING_HREF_MARKERS):
        return True
    # Very long opaque hrefs are almost always encoded redirect tokens.
    return len(href) > 150


def html_to_markdown(html: str) -> str:
    """Convert an HTML body to Markdown deterministically.

    Layout tables are flattened to blocks first so table-based newsletter emails
    keep their full content (SAT-261). Images are dropped (tracking pixels), and
    tracking/redirect links are unwrapped to their text (the base64 redirect URLs
    are noise on a Kindle) while genuine links are preserved.
    Same input always yields the same output; no model/network call is made.
    """
    if not html or not html.strip():
        return ""
    soup = BeautifulSoup(html, "html.parser")
    # Remove one tag type at a time, re-querying each time: decomposing a parent
    # (e.g. <head>) also removes its noise children (<meta>/<link>), so collecting
    # everything up front and then decomposing could touch already-removed nodes.
    for tag_name in _NOISE_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()
    # Flatten layout tables into block-level divs (keeps cell content, drops grid).
    for tag in soup.find_all(_LAYOUT_TAGS):
        tag.name = "div"
    # Unwrap tracking/redirect anchors (keep their text, drop the noisy URL);
    # genuine links are left for markdownify to render normally.
    for anchor in soup.find_all("a", href=True):
        if _is_tracking_href(anchor["href"]):
            anchor.unwrap()
    markdown = _markdownify(str(soup), heading_style="ATX", strip=["img"])
    # Collapse trailing whitespace and runs of blank lines left by the flattening.
    markdown = re.sub(r"[ \t]+\n", "\n", markdown)
    markdown = re.sub(r"\n{3,}", "\n\n", markdown)
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
