"""Tests for the per-send digest cover (#78): text only, no publisher logos."""

from __future__ import annotations

import io
from datetime import date

from PIL import Image

from substack_kindle.cover import COVER_SIZE, CoverPost, render_cover
from substack_kindle.job_epub import JobSection, build_job_epub


def _posts(n: int) -> list[CoverPost]:
    return [
        CoverPost(title=f"Post number {i} with a reasonably long headline", publication=f"Pub {i}")
        for i in range(n)
    ]


def test_cover_is_a_1600x2560_jpeg():
    data = render_cover(day=date(2026, 11, 17), posts=_posts(5), publication_count=4, minutes=48)
    assert data[:3] == b"\xff\xd8\xff"  # JPEG magic
    img = Image.open(io.BytesIO(data))
    assert img.size == COVER_SIZE == (1600, 2560)
    assert len(data) < 1_000_000


def test_cover_handles_one_post_and_very_long_titles():
    long = [CoverPost(title="word " * 80, publication="P" * 200)]
    data = render_cover(day=date(2026, 1, 1), posts=long, publication_count=1, minutes=3)
    assert Image.open(io.BytesIO(data)).size == COVER_SIZE


def test_cover_is_deterministic_for_the_same_input():
    args = dict(day=date(2026, 11, 17), posts=_posts(3), publication_count=2, minutes=10)
    assert render_cover(**args) == render_cover(**args)


def test_epub_carries_the_cover_image():
    cover = render_cover(day=date(2026, 11, 17), posts=_posts(1), publication_count=1, minutes=5)
    epub = build_job_epub(
        [JobSection(title="T", markdown="Body", sender="S")],
        book_title="Digest",
        cover_image=cover,
    )
    import zipfile

    names = zipfile.ZipFile(io.BytesIO(epub)).namelist()
    assert any(n.endswith("cover.jpg") for n in names)
    opf = next(n for n in names if n.endswith(".opf"))
    assert b'name="cover"' in zipfile.ZipFile(io.BytesIO(epub)).read(opf)


def test_epub_without_cover_is_unchanged():
    epub = build_job_epub([JobSection(title="T", markdown="Body", sender="S")], book_title="Digest")
    import zipfile

    assert not any(n.endswith("cover.jpg") for n in zipfile.ZipFile(io.BytesIO(epub)).namelist())
