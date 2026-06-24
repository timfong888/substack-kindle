"""Free-Substack RSS → JobSection fetch pipeline (SAT-330).

Replaces the Gmail fetch path for free Substacks. Each approved source is a
canonical RSS feed URL (e.g. ``https://<pub>.substack.com/feed``). We fetch the
feed, parse its items, filter to the ``[window_start, window_end]`` window by
each item's ``pubDate``, and convert the item's content HTML to Markdown via the
existing deterministic parser (``parsing.html_to_markdown`` — no LLM on body).

The dedup key is the RSS ``<guid>`` (a stable per-post id), carried on
``FetchedPost`` so the pipeline can dedup on it independently of the post title.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from .job_epub import JobSection
from .parsing import html_to_markdown

# The RSS 1.0 "content" module namespace Substack uses for the full post body.
_CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}encoded"


class RssFetchError(Exception):
    """Raised when a feed lacks the structure the RSS fetch layer requires."""


@dataclass(frozen=True)
class FeedItem:
    """One parsed RSS ``<item>`` before body conversion."""

    guid: str
    title: str
    published: datetime
    content_html: str
    publication: str  # the channel <title>, used as the section's publication name


@dataclass(frozen=True)
class FetchedPost:
    """A windowed, body-converted post ready for the EPUB pipeline."""

    guid: str
    title: str
    markdown: str
    sender: str  # human-readable publication name
    published: datetime

    def to_section(self) -> JobSection:
        return JobSection(title=self.title, markdown=self.markdown, sender=self.sender)


def parse_feed(xml: bytes | str) -> list[FeedItem]:
    """Parse RSS bytes into ``FeedItem`` records (no network, no body conversion).

    The publication name is the channel ``<title>``. Each item's body is taken
    from ``content:encoded`` (Substack's full body), falling back to
    ``<description>``. ``pubDate`` is parsed as RFC 822; a date without a
    timezone is treated as UTC so window comparisons stay correct.
    """
    root = ET.fromstring(xml)
    channel = root.find("channel")
    if channel is None:
        raise RssFetchError("feed has no <channel> element")
    publication = (channel.findtext("title") or "").strip()

    items: list[FeedItem] = []
    for item in channel.findall("item"):
        raw_date = item.findtext("pubDate")
        try:
            published = parsedate_to_datetime(raw_date)
        except (TypeError, ValueError) as exc:
            raise RssFetchError(f"could not parse pubDate {raw_date!r}: {exc}") from exc
        if published.tzinfo is None:
            published = published.replace(tzinfo=UTC)
        body = item.findtext(_CONTENT_NS)
        if body is None:
            body = item.findtext("description") or ""
        # The guid is the dedup key. An empty key would collide across every
        # id-less item, so refuse rather than silently corrupt dedup.
        guid = (item.findtext("guid") or item.findtext("link") or "").strip()
        if not guid:
            raise RssFetchError("item has no <guid> or <link> to use as a dedup key")
        items.append(FeedItem(
            guid=guid,
            title=(item.findtext("title") or "").strip(),
            published=published,
            content_html=body,
            publication=publication,
        ))
    return items


def fetch_posts(
    http_get: Callable[[str], bytes | str],
    *,
    feed_urls: list[str],
    window_start: datetime,
    window_end: datetime,
) -> list[FetchedPost]:
    """Return windowed, body-converted posts across all approved feeds.

    Output is sorted by published date ascending so the EPUB's TOC reads
    chronologically. Posts outside ``[window_start, window_end]`` (by
    ``pubDate``) are dropped.

    An empty ``feed_urls`` is treated as a misconfiguration: we refuse rather
    than silently producing an empty digest (mirrors the Gmail fetch guard).
    """
    if not feed_urls:
        raise ValueError("feed_urls must not be empty")

    posts: list[FetchedPost] = []
    for url in feed_urls:
        for item in parse_feed(http_get(url)):
            if not (window_start <= item.published <= window_end):
                continue
            posts.append(FetchedPost(
                guid=item.guid,
                title=item.title,
                markdown=html_to_markdown(item.content_html),
                sender=item.publication,
                published=item.published,
            ))
    posts.sort(key=lambda p: p.published)
    return posts


__all__ = [
    "RssFetchError",
    "FeedItem",
    "FetchedPost",
    "parse_feed",
    "fetch_posts",
]
