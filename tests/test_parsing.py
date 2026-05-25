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
