"""Tests for the free-Substack RSS → JobSection fetch pipeline (SAT-330).

The RSS fetch layer replaces the Gmail path for free Substacks: each approved
source is a canonical RSS feed URL. We fetch the feed, parse its items, filter
to the [window_start, window_end] window by each item's ``pubDate``, convert the
item's content HTML to Markdown via the existing deterministic parser, and
return ``FetchedPost`` records carrying the RSS ``<guid>`` as the dedup key.

Fixtures use synthetic ``example.com`` domains — this is a public repo, so no
real per-customer feed config lives in tests.
"""

from datetime import UTC, datetime

import pytest

from substack_kindle.job_epub import JobSection
from substack_kindle.rss_fetch import RssFetchError, fetch_posts, parse_feed

SAMPLE_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>Example Publication</title>
    <item>
      <title>Newer Post</title>
      <link>https://example.com/p/newer</link>
      <guid>https://example.com/p/newer</guid>
      <pubDate>Wed, 24 Jun 2026 14:02:16 GMT</pubDate>
      <content:encoded><![CDATA[<h1>Newer</h1><p>Body one.</p>]]></content:encoded>
      <description>short</description>
    </item>
    <item>
      <title>Older Post</title>
      <link>https://example.com/p/older</link>
      <guid>https://example.com/p/older</guid>
      <pubDate>Tue, 02 Jun 2026 10:00:00 GMT</pubDate>
      <content:encoded><![CDATA[<h1>Older</h1><p>Body two.</p>]]></content:encoded>
    </item>
  </channel>
</rss>"""

_FEED_URL = "https://example.com/feed"


def _get_sample(url):
    return SAMPLE_FEED


# --- parse_feed (pure, no network) ------------------------------------------


def test_parse_feed_extracts_guid_title_pubdate_and_publication():
    items = parse_feed(SAMPLE_FEED)
    assert [i.title for i in items] == ["Newer Post", "Older Post"]
    first = items[0]
    assert first.guid == "https://example.com/p/newer"
    assert first.publication == "Example Publication"
    assert first.published == datetime(2026, 6, 24, 14, 2, 16, tzinfo=UTC)


def test_parse_feed_uses_content_encoded_for_body():
    items = parse_feed(SAMPLE_FEED)
    # The full body lives in content:encoded, not the short <description>.
    assert "<h1>Newer</h1>" in items[0].content_html


def test_parse_feed_raises_when_item_has_no_guid_or_link():
    # The guid is the dedup key; an item without one (and without a link to fall
    # back to) would produce an empty key that collides with every other such
    # item. Refuse rather than silently corrupt dedup.
    feed = b"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
      <channel>
        <title>Example Publication</title>
        <item>
          <title>No identifier</title>
          <pubDate>Wed, 24 Jun 2026 14:02:16 GMT</pubDate>
          <content:encoded><![CDATA[<p>Body.</p>]]></content:encoded>
        </item>
      </channel>
    </rss>"""
    with pytest.raises(RssFetchError, match="guid"):
        parse_feed(feed)


# --- fetch_posts (window filter + parser + ordering) ------------------------


def test_fetch_posts_filters_to_window_by_pubdate():
    # Window covers only 2026-06-24, so the June 2 post is excluded.
    posts = fetch_posts(
        _get_sample,
        feed_urls=[_FEED_URL],
        window_start=datetime(2026, 6, 24, tzinfo=UTC),
        window_end=datetime(2026, 6, 24, 23, 59, 59, tzinfo=UTC),
    )
    assert [p.title for p in posts] == ["Newer Post"]


def test_fetch_posts_converts_body_to_markdown_and_carries_publication():
    posts = fetch_posts(
        _get_sample,
        feed_urls=[_FEED_URL],
        window_start=datetime(2026, 6, 1, tzinfo=UTC),
        window_end=datetime(2026, 6, 30, tzinfo=UTC),
    )
    newer = next(p for p in posts if p.title == "Newer Post")
    # Body went through parsing.html_to_markdown — the H1 survives as "# Newer".
    assert "Newer" in newer.markdown
    assert "<h1>" not in newer.markdown
    # Publication name flows into the section sender.
    assert newer.sender == "Example Publication"


def test_fetch_posts_orders_by_pubdate_ascending():
    posts = fetch_posts(
        _get_sample,
        feed_urls=[_FEED_URL],
        window_start=datetime(2026, 6, 1, tzinfo=UTC),
        window_end=datetime(2026, 6, 30, tzinfo=UTC),
    )
    assert [p.title for p in posts] == ["Older Post", "Newer Post"]


def test_fetch_posts_dedup_key_is_guid_and_post_maps_to_section():
    posts = fetch_posts(
        _get_sample,
        feed_urls=[_FEED_URL],
        window_start=datetime(2026, 6, 24, tzinfo=UTC),
        window_end=datetime(2026, 6, 24, 23, 59, 59, tzinfo=UTC),
    )
    post = posts[0]
    assert post.guid == "https://example.com/p/newer"
    section = post.to_section()
    assert isinstance(section, JobSection)
    assert section.title == "Newer Post"
    assert section.sender == "Example Publication"
    assert "Newer" in section.markdown


def test_fetch_posts_calls_http_get_once_per_feed_url():
    calls = []

    def _recording_get(url):
        calls.append(url)
        return SAMPLE_FEED

    fetch_posts(
        _recording_get,
        feed_urls=["https://example.com/feed", "https://example.org/feed"],
        window_start=datetime(2026, 6, 1, tzinfo=UTC),
        window_end=datetime(2026, 6, 30, tzinfo=UTC),
    )
    assert calls == ["https://example.com/feed", "https://example.org/feed"]


def test_fetch_posts_rejects_empty_feed_urls():
    # An empty feed list is a misconfiguration, mirroring the Gmail fetch guard:
    # refuse rather than silently producing an empty digest.
    with pytest.raises(ValueError, match="feed_urls"):
        fetch_posts(
            _get_sample,
            feed_urls=[],
            window_start=datetime(2026, 6, 1, tzinfo=UTC),
            window_end=datetime(2026, 6, 30, tzinfo=UTC),
        )
