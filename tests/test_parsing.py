"""Tests for deterministic HTML->Markdown body conversion (SAT-244 / #8, Reqs 8, 15).

Acceptance:
- Conversion uses libraries only; the LLM is NOT invoked on body text.
- Markdown is persisted and retrievable by newsletter ID at any later time.
- Processing cost does not grow super-linearly / call any model regardless of size.
"""

import sys

import substack_kindle.parsing as parsing
from substack_kindle.parsing import InMemoryMarkdownStore, html_to_markdown


def test_converts_basic_structure_to_markdown():
    html = "<h1>Title</h1><p>Hello <strong>world</strong>.</p>"
    md = html_to_markdown(html)
    assert "# Title" in md
    assert "**world**" in md


def test_preserves_links_and_lists():
    html = '<ul><li>one</li><li><a href="https://e.com">two</a></li></ul>'
    md = html_to_markdown(html)
    assert "one" in md
    assert "[two](https://e.com)" in md


def test_conversion_is_deterministic():
    html = "<h2>Edition 5</h2><p>Body text with <em>emphasis</em>.</p>"
    assert html_to_markdown(html) == html_to_markdown(html)


def test_strips_script_and_style_noise():
    html = (
        "<style>.x{color:red}</style>"
        "<script>tracker()</script>"
        "<p>Real content.</p>"
    )
    md = html_to_markdown(html)
    assert "Real content." in md
    assert "tracker()" not in md
    assert "color:red" not in md


def test_empty_html_yields_empty_markdown():
    assert html_to_markdown("").strip() == ""


def test_noise_tags_nested_in_head_do_not_crash():
    # Real HTML emails wrap <meta>/<link> inside <head>; removing the parent and
    # the children must not touch an already-removed node.
    html = (
        "<html><head><meta charset='utf-8'>"
        "<link rel='stylesheet' href='x.css'/></head>"
        "<body><p>content</p></body></html>"
    )
    md = html_to_markdown(html)
    assert "content" in md
    assert "stylesheet" not in md
    assert "charset" not in md


def test_no_llm_or_network_import_in_parse_path():
    # Req 8: deterministic libraries only. No model/agent/network clients in this module.
    with open(parsing.__file__) as fh:
        source = fh.read().lower()
    for forbidden in ("anthropic", "openai", "requests", "httpx", "urllib.request", "claude"):
        assert forbidden not in source


def test_parse_does_not_import_an_llm_sdk_at_runtime():
    # No LLM SDK should be pulled in just by importing/using the parse path.
    html_to_markdown("<p>x</p>")
    assert "anthropic" not in sys.modules
    assert "openai" not in sys.modules


def test_store_persists_and_retrieves_by_newsletter_id():
    store = InMemoryMarkdownStore()
    store.put("nl-1", "# Title\n\nbody")
    assert store.get("nl-1") == "# Title\n\nbody"
    assert store.get("nl-missing") is None


def test_store_round_trip_via_parse_helper():
    store = InMemoryMarkdownStore()
    md = store.parse_and_store("nl-7", "<h1>Hi</h1><p>there</p>")
    assert "# Hi" in md
    # Retrievable "at any later time" — same value comes back from the store.
    assert store.get("nl-7") == md


def test_cost_is_independent_of_length_no_model_calls():
    # A large body must not trigger any per-length model call; cost stays library-only.
    small = "<p>hi</p>"
    big = "<p>" + ("word " * 200_000) + "</p>"
    md_small = html_to_markdown(small)
    md_big = html_to_markdown(big)
    assert "hi" in md_small
    assert len(md_big) > len(md_small)
    # No LLM SDK loaded regardless of input size.
    assert "anthropic" not in sys.modules and "openai" not in sys.modules


# ---------------------------------------------------------------------------
# Table handling (SAT-57 / issue #57)
# ---------------------------------------------------------------------------

def test_data_table_preserved_as_raw_html():
    # A table with <th> is a data table — it must survive as an HTML block so
    # Kindle can render it with the existing CSS stylesheet.
    html = (
        "<p>Intro.</p>"
        "<table><thead><tr><th>Plan</th><th>Price</th></tr></thead>"
        "<tbody><tr><td>Pro</td><td>$10</td></tr></tbody></table>"
        "<p>Outro.</p>"
    )
    md = html_to_markdown(html)
    assert "<table>" in md
    assert "<th>Plan</th>" in md
    assert "<td>Pro</td>" in md
    # Must NOT produce pipe-table syntax (which loses content on round-trip).
    assert "|" not in md.split("<table>")[0]


def test_layout_table_flattened_to_prose():
    # A table with only <td> (no <th>) is a layout wrapper — its cell text must
    # appear as readable prose; no HTML table or pipe-table must remain.
    html = (
        "<table><tr>"
        "<td><p>Left column content.</p></td>"
        "<td><p>Right column content.</p></td>"
        "</tr></table>"
    )
    md = html_to_markdown(html)
    assert "Left column content." in md
    assert "Right column content." in md
    assert "<table>" not in md
    # No pipe-table lines (markdownify's broken table output).
    assert not any(line.startswith("|") for line in md.splitlines())


def test_layout_table_with_empty_cells_removed_cleanly():
    # &nbsp;-only cells produce no visible content — the table should disappear
    # entirely rather than leaving behind blank lines or empty tags.
    html = "<p>Before.</p><table><tr><td>\xa0</td><td>\xa0</td></tr></table><p>After.</p>"
    md = html_to_markdown(html)
    assert "Before." in md
    assert "After." in md
    assert "<table>" not in md
    assert not any(line.startswith("|") for line in md.splitlines())


def test_th_table_with_empty_cells_treated_as_layout():
    # GH #57: email layout templates ([AINews], DeFi Daily) wrap content in
    # tables that contain <th> but whose data cells are empty / whitespace /
    # image-only. The <th> presence alone must NOT mark them as data tables —
    # preserving them as raw HTML renders an empty table on Kindle. They must be
    # flattened/dropped like any other layout table, and surrounding prose kept.
    html = (
        "<table>"
        "<tr><th></th><th></th></tr>"
        "<tr><td>\xa0</td><td><img src='spacer.png'></td></tr>"
        "</table>"
        "<p>Real article text.</p>"
    )
    md = html_to_markdown(html)
    assert "Real article text." in md
    assert "<table>" not in md
    assert not any(line.startswith("|") for line in md.splitlines())


def test_data_table_nested_in_layout_wrapper_preserved():
    # Real newsletters wrap a data table inside an outer layout <table>.
    # The inner data table must be saved; the outer layout wrapper flattened.
    html = (
        "<table>"  # outer layout — no <th>
        "<tr><td>"
        "<table>"  # inner data table — has <th>
        "<tr><th>Metric</th><th>Value</th></tr>"
        "<tr><td>Revenue</td><td>$1M</td></tr>"
        "</table>"
        "</td></tr>"
        "</table>"
    )
    md = html_to_markdown(html)
    assert "<table>" in md
    assert "<th>Metric</th>" in md
    assert "Revenue" in md
    # The outer layout wrapper must not produce a pipe table.
    assert not any(line.startswith("|") for line in md.splitlines())


def test_multiple_data_tables_all_preserved():
    html = (
        "<table><tr><th>A</th></tr><tr><td>1</td></tr></table>"
        "<p>Gap.</p>"
        "<table><tr><th>B</th></tr><tr><td>2</td></tr></table>"
    )
    md = html_to_markdown(html)
    assert md.count("<table>") == 2
    assert "<th>A</th>" in md
    assert "<th>B</th>" in md


def test_layout_table_preserves_heading_structure():
    # Cells containing semantic headings must produce Markdown headings, not
    # flat text.  This was the root cause of the unreadable Kindle output: the
    # old get_text() path stripped <h2> tags and collapsed everything into one
    # paragraph.
    html = (
        "<table><tr><td>"
        "<h2>Section Title</h2>"
        "<p>Body text.</p>"
        "</td></tr></table>"
    )
    md = html_to_markdown(html)
    assert "## Section Title" in md
    assert "Body text." in md
    assert "<table>" not in md


def test_layout_table_preserves_inline_formatting():
    # Bold, italic, and links inside layout-table cells must survive the
    # unwrap so the reader sees formatted prose, not stripped plain text.
    html = (
        "<table><tr><td>"
        "<p>Normal and <strong>bold</strong> text.</p>"
        "</td></tr></table>"
    )
    md = html_to_markdown(html)
    assert "**bold**" in md
    assert "<table>" not in md


def test_prose_survives_alongside_data_table():
    # Content before and after the table must not be lost.
    html = (
        "<h2>Section</h2>"
        "<p>Here is the data:</p>"
        "<table><tr><th>Col</th></tr><tr><td>Val</td></tr></table>"
        "<p>End of section.</p>"
    )
    md = html_to_markdown(html)
    assert "## Section" in md
    assert "Here is the data:" in md
    assert "<table>" in md
    assert "End of section." in md
