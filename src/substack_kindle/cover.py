"""Per-send digest cover (#78): a text-only JPEG, so each digest is easy to spot
in the Kindle library. No publisher logos (rights); names appear as text only.

Rendered with Pillow's built-in scalable font, so it needs no browser or system
fonts and runs on a bare GitHub Actions runner. Deterministic for the same input.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import date

from PIL import Image, ImageDraw, ImageFont

COVER_SIZE = (1600, 2560)  # 1:1.6, Kindle's recommended cover ratio
_MARGIN = 130
_BG = (244, 239, 230)
_INK = (27, 27, 27)
_ACCENT = (194, 65, 12)
_MUTED = (110, 110, 110)
_RULE = (207, 198, 182)
MAX_LISTED = 3


@dataclass(frozen=True)
class CoverPost:
    title: str
    publication: str


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.load_default(size=size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int, max_lines: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    line = ""
    for word in words:
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= width:
            line = trial
            continue
        if line:
            lines.append(line)
        line = word
        if len(lines) == max_lines:
            break
    if line and len(lines) < max_lines:
        lines.append(line)
    if len(lines) == max_lines and " ".join(lines) != " ".join(words):
        last = lines[-1]
        while last and draw.textlength(last + "…", font=font) > width:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"
    return lines


def render_cover(
    *, day: date, posts: list[CoverPost], publication_count: int, minutes: int
) -> bytes:
    """JPEG bytes: date, lead headline + publication, up to three more posts, counts."""
    if not posts:
        raise ValueError("a cover needs at least one post")
    img = Image.new("RGB", COVER_SIZE, _BG)
    draw = ImageDraw.Draw(img)
    width = COVER_SIZE[0] - 2 * _MARGIN
    y = 150

    draw.text((_MARGIN, y), "SUBSTACK DIGEST", font=_font(56), fill=_ACCENT)
    y += 130
    for line in (f"{day:%A}", f"{day.day} {day:%B %Y}"):
        draw.text((_MARGIN, y), line, font=_font(140), fill=_INK)
        y += 165
    y += 60
    draw.rectangle((_MARGIN, y, COVER_SIZE[0] - _MARGIN, y + 10), fill=_INK)
    y += 100

    lead, rest = posts[0], posts[1 : 1 + MAX_LISTED]
    for line in _wrap(draw, lead.title, _font(104), width, 4):
        draw.text((_MARGIN, y), line, font=_font(104), fill=_INK)
        y += 125
    y += 20
    for line in _wrap(draw, lead.publication, _font(56), width, 1):
        draw.text((_MARGIN, y), line, font=_font(56), fill=_MUTED)
    y += 140

    for post in rest:
        draw.rectangle((_MARGIN, y, COVER_SIZE[0] - _MARGIN, y + 3), fill=_RULE)
        y += 30
        for line in _wrap(draw, post.publication.upper(), _font(40), width, 1):
            draw.text((_MARGIN, y), line, font=_font(40), fill=_MUTED)
        y += 60
        for line in _wrap(draw, post.title, _font(60), width, 2):
            draw.text((_MARGIN, y), line, font=_font(60), fill=_INK)
            y += 75
        y += 25

    footer_y = COVER_SIZE[1] - 200
    n = len(posts)
    posts_label = f"{n} post{'s' if n != 1 else ''}"
    pubs_label = f"{publication_count} publication{'s' if publication_count != 1 else ''}"
    left = f"{posts_label} · {pubs_label}"
    draw.text((_MARGIN, footer_y), left, font=_font(48), fill=_MUTED)
    right = f"~{minutes} min"
    draw.text(
        (COVER_SIZE[0] - _MARGIN - draw.textlength(right, font=_font(48)), footer_y),
        right,
        font=_font(48),
        fill=_MUTED,
    )

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=85, optimize=True)
    return out.getvalue()
