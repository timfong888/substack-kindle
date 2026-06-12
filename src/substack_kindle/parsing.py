"""Deterministic newsletter body parsing (SAT-244 / Reqs 8, 15).

HTML is converted to Markdown with libraries only (BeautifulSoup + markdownify) —
the LLM is never invoked on body text, so a run's processing cost stays library-
bound and does not grow with newsletter length. Converted Markdown is persisted
keyed by newsletter ID and retrievable at any later time.
"""

from __future__ import annotations

from bs4 import BeautifulSoup
from markdownify import markdownify as _markdownify

from .substack_clean import clean_substack, looks_like_substack

# Non-content tags whose text must never leak into the Markdown body.
_NOISE_TAGS = ("script", "style", "head", "meta", "link")

# Sentinel used to hold a data-table's position during markdownify.
# Alphanumeric-only so markdownify never escapes the characters.
# Distinctive enough that it cannot appear in real newsletter prose.
_TABLE_SENTINEL_FMT = "NLTABLEZEN{}ZZ"


def _sentinel(n: int) -> str:
    return _TABLE_SENTINEL_FMT.format(n)


def _preprocess_tables(soup: BeautifulSoup) -> dict[str, str]:
    """Classify tables and prepare them for the markdownify pass.

    Two classes:

    * **Data table** — has at least one ``<th>`` element. Saved verbatim as HTML
      and replaced with a sentinel ``<p>`` so markdownify never sees the table.
      After markdownify the sentinel is swapped back for the raw HTML block, which
      Python Markdown passes through unchanged into the final EPUB HTML.

    * **Layout table** — no ``<th>``. Email senders use these as multi-column
      wrappers, not to display data. Their cell text is extracted and emitted as
      plain ``<p>`` elements so the prose survives the round-trip.

    Tables are processed innermost-first so a data table nested inside a layout
    wrapper is saved before the wrapper is flattened. The sentinel left in the
    wrapper's ``<td>`` survives as plain text and is later restored to its raw HTML
    block, which Python Markdown promotes to a block-level element.

    Returns a ``{sentinel: original_html}`` mapping for the restoration step.
    """
    saved: dict[str, str] = {}
    counter = 0

    # Collect tables once, ordered deepest-first so children are processed before parents.
    tables = sorted(soup.find_all("table"), key=lambda t: -len(list(t.parents)))

    for table in tables:
        # Skip tables already removed as part of processing an ancestor.
        if table.parent is None:
            continue

        if table.find("th") is not None:
            # Data table: save and replace with a sentinel paragraph.
            key = _sentinel(counter)
            counter += 1
            saved[key] = str(table)
            p = soup.new_tag("p")
            p.string = key
            table.replace_with(p)
        else:
            # Layout table: unwrap each non-empty cell's children into a div,
            # preserving inner HTML structure (headings, bold, paragraph breaks)
            # so markdownify renders them with proper formatting instead of flat
            # text.
            cells = [td for td in table.find_all("td") if td.get_text(strip=True)]
            if cells:
                wrapper = soup.new_tag("div")
                for td in cells:
                    inner = soup.new_tag("div")
                    for child in list(td.children):
                        inner.append(child.extract())
                    wrapper.append(inner)
                table.replace_with(wrapper)
            else:
                table.decompose()

    return saved


def html_to_markdown(html: str) -> str:
    """Convert an HTML body to Markdown deterministically.

    Same input always yields the same output; no model/network call is made.
    Substack-shaped input is run through ``clean_substack`` first to strip the
    fixed template chrome (tracking pixel, icon row, footer); other inputs
    pass through untouched.

    Tables are handled before markdownify runs (see ``_preprocess_tables``):
    data tables (those with ``<th>``) are preserved as raw HTML blocks so they
    render on Kindle with the existing CSS; layout tables are flattened to prose.
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
    if looks_like_substack(soup):
        clean_substack(soup)
    saved_tables = _preprocess_tables(soup)
    markdown = _markdownify(str(soup), heading_style="ATX")
    # Restore data tables as raw HTML blocks. The surrounding blank lines tell
    # Python Markdown to treat the tag as a block-level element and pass it through
    # unchanged rather than wrapping it in a paragraph.
    for key, table_html in saved_tables.items():
        markdown = markdown.replace(key, f"\n\n{table_html}\n\n")
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
