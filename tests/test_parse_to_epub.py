"""Characterization test: a table-layout email must survive parse -> EPUB intact.

Regression guard for SAT-261. Real Substack newsletters lay content out in
nested HTML tables; a naive HTML->Markdown of that produces broken markdown
tables that the markdown->HTML render then collapses, dropping ~93% of the body.
These tests model a table-layout email and assert the full content reaches the
EPUB. They are deterministic and use no real (copyrighted) newsletter content.
"""

from __future__ import annotations

import io
import re
import zipfile

from substack_kindle.job_epub import JobSection, build_job_epub
from substack_kindle.parsing import html_to_markdown

# Substack-style layout: meaningful content nested several tables deep, the way
# real newsletter emails arrive.
TABLE_LAYOUT_EMAIL = """
<html><head><style>.x{color:red}</style></head><body>
<table><tbody><tr><td>
  <table><tr><td><h1>The Vault Report</h1></td></tr></table>
  <table><tr><td>
    <p>This section presents a quantitative analysis of the vault landscape.</p>
    <p>We define eight structural categories before diving into each one.</p>
    <h2>Categorising Vaults</h2>
    <p>Our definition is based on the deployment path and methodology thoroughly.</p>
    <p>Lending vaults and liquid staking are treated as distinct categories.</p>
  </td></tr></table>
</td></tr></tbody></table>
</body></html>
"""

_MARKERS = [
    "Categorising Vaults",
    "eight structural categories",
    "methodology thoroughly",
    "Lending vaults",
]


def _epub_text(epub_bytes: bytes) -> str:
    z = zipfile.ZipFile(io.BytesIO(epub_bytes))
    return "".join(
        re.sub("<[^>]+>", "", z.read(n).decode("utf-8", "ignore"))
        for n in z.namelist()
        if n.endswith(".xhtml")
    )


def test_table_layout_markdown_preserves_content():
    md = html_to_markdown(TABLE_LAYOUT_EMAIL)
    for marker in _MARKERS:
        assert marker in md, f"{marker!r} lost in markdown"
    # Heading structure is preserved (not flattened into a broken table cell).
    assert "# Categorising Vaults" in md or "## Categorising Vaults" in md


def test_table_layout_survives_into_epub():
    md = html_to_markdown(TABLE_LAYOUT_EMAIL)
    epub = build_job_epub([JobSection("The Vault Report", md)], book_title="Test")
    text = _epub_text(epub)
    for marker in _MARKERS:
        assert marker in text, f"{marker!r} lost in EPUB render"
