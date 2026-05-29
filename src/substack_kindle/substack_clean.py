"""Substack template cleaner (SAT-265).

Substack newsletters share a fixed HTML template: a tracking pixel, invisible
preview-pane padding, a "Forwarded this email? Subscribe here" banner, a
title-as-link duplicate of the article title, an author+date+icon row (heart /
comment / share / restack / "READ IN APP"), and footer chrome (unsubscribe,
"© Substack Inc.").

The icons themselves are images, so when html2text/markdownify converts them
the link has no visible text — pandoc dumps the raw URL into the output, which
reads as a wall of "badly formatted email addresses". Killing the metadata
block is what makes the digest readable.

All rules are structural — no LLM. They activate only on Substack-shaped input
(any substack.com / substackcdn.com URL anywhere in the body), so non-Substack
emails pass through untouched.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag

# Invisible / preview-pane characters Substack uses to pad the email preview
# snippet. The cleaner drops paragraphs that contain only these (plus whitespace).
_INVISIBLE_CHARS_RE = re.compile(
    r"[­͏؜᠎​-‏  ⁠﻿]"
)

# Substack icon image paths (in the icon URL's path component).
_ICON_PATH_PATTERNS = (
    "LucideHeart",
    "LucideComments",
    "LucideShare",
    "LucideArrowUpRight",
    "notes__NoteRestackIcon",
)

# Query/path hints on an anchor's href that mark it as a reaction/share/restack
# icon link — i.e. chrome, not content.
_ICON_LINK_HREF_HINTS = (
    "submitLike=true",
    "comments=true",
    "action=share",
    "utm_campaign=email-reaction",
    "utm_campaign=email-share",
    "utm_campaign=email-restack",
    "utm_campaign=email-read-in-app",
)

_SUBSTACK_HOST_SUFFIXES = ("substack.com", "substackcdn.com")
_FOOTER_TEXT_HINTS = ("Substack Inc", "Unsubscribe", "Get the app")


def _host(url: str | None) -> str:
    if not url:
        return ""
    return (urlparse(url).hostname or "").lower()


def _is_substack_host(url: str | None) -> bool:
    h = _host(url)
    return any(h == s or h.endswith("." + s) for s in _SUBSTACK_HOST_SUFFIXES)


def looks_like_substack(soup: BeautifulSoup) -> bool:
    """True iff the body carries any Substack-ecosystem URL.

    Detection by URL host so we activate the cleaner on the actual Substack
    template, not on every newsletter that happens to mention Substack.
    """
    for a in soup.find_all("a", href=True):
        if _is_substack_host(a["href"]):
            return True
    for img in soup.find_all("img", src=True):
        if _is_substack_host(img["src"]):
            return True
    return False


def _is_tracking_pixel(img: Tag) -> bool:
    # Substack opens every email with a pixel on eotrx.substackcdn.com (the
    # "engagement" subdomain). The path is typically ``/o/<id>/p.gif``.
    return _host(img.get("src")) == "eotrx.substackcdn.com"


def _is_invisible_only(tag: Tag) -> bool:
    text = tag.get_text()
    if not text:
        return False
    stripped = _INVISIBLE_CHARS_RE.sub("", text).strip()
    return stripped == ""


def _href_is_icon_link(href: str) -> bool:
    return any(hint in href for hint in _ICON_LINK_HREF_HINTS)


def _src_is_icon_image(src: str) -> bool:
    return any(p in src for p in _ICON_PATH_PATTERNS)


def _is_icon_anchor(a: Tag) -> bool:
    href = a.get("href", "")
    if _href_is_icon_link(href):
        return True
    img = a.find("img")
    if img is not None and _src_is_icon_image(img.get("src", "")):
        return True
    return False


def _only_child_is_image(a: Tag) -> Tag | None:
    """Return the sole `<img>` child of `a`, or None if `a` wraps anything else."""
    children = [c for c in a.children if not (isinstance(c, str) and not c.strip())]
    if len(children) == 1 and getattr(children[0], "name", None) == "img":
        return children[0]  # type: ignore[return-value]
    return None


def _is_redirect_wrapping_image(a: Tag) -> bool:
    href = a.get("href", "")
    # Only Substack redirect / app-link anchors qualify; we won't strip random
    # third-party links that happen to wrap an image.
    if "substack.com/redirect/" not in href and "substack.com/app-link/post" not in href:
        return False
    return _only_child_is_image(a) is not None


def _is_link_wrapped_title(h: Tag) -> bool:
    a = h.find("a", href=True)
    if a is None:
        return False
    if "substack.com/app-link/post" not in a["href"]:
        return False
    # The heading is the link-wrapped title iff its full text equals the anchor's.
    return h.get_text(strip=True) == a.get_text(strip=True)


def clean_substack(soup: BeautifulSoup) -> None:
    """Strip Substack chrome from `soup` in place. Idempotent."""
    # Rule 1 — tracking pixels.
    for img in list(soup.find_all("img")):
        if _is_tracking_pixel(img):
            img.decompose()

    # Rule 7 — footer chrome (unsubscribe, "© Substack Inc.", "Get the app").
    # Done early so its text doesn't leak into other rules' detection.
    for el in list(soup.find_all(["table", "div"])):
        text = el.get_text()
        if any(hint in text for hint in _FOOTER_TEXT_HINTS):
            el.decompose()

    # Rule 3 — "Forwarded this email? Subscribe here" banner.
    for el in list(soup.find_all(["table", "div"])):
        if "Forwarded this email" in el.get_text():
            el.decompose()

    # Rule 2 — invisible preview-pane padding paragraphs.
    for tag in list(soup.find_all(["p", "div", "span"])):
        if _is_invisible_only(tag):
            tag.decompose()

    # Rule 4 — title-as-link duplicate heading.
    for h in list(soup.find_all(["h1", "h2"])):
        if _is_link_wrapped_title(h):
            h.decompose()

    # Rule 5 — author + icon metadata table.
    for table in list(soup.find_all("table")):
        if "READ IN APP" in table.get_text():
            table.decompose()
            continue
        anchors = table.find_all("a", href=True)
        if anchors and any(_is_icon_anchor(a) for a in anchors):
            table.decompose()
            continue
        imgs = table.find_all("img", src=True)
        if imgs and any(_src_is_icon_image(img.get("src", "")) for img in imgs):
            table.decompose()

    # Rule 6 — unwrap redirect-wrapping anchors around content images. The
    # image is the content; the wrapping redirect URL is what was leaking as
    # giant link text in the converted markdown.
    for a in list(soup.find_all("a", href=True)):
        if _is_redirect_wrapping_image(a):
            img = _only_child_is_image(a)
            if img is not None:
                a.replace_with(img)
