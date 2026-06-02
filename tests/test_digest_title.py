"""Tests for the digest book-title format (SAT-272).

Format: ``Newsletter Digest: {Month D} – {Month D} {YYYY}`` — full month name,
en-dash with surrounding spaces, year once at the end. Replaces the SAT-264
shorthand ("Substacks · May 19–26, 2026") so the title reads like a real book
in the Kindle library row, not a header chip.
"""

from datetime import date

from substack_kindle.digest_title import format_digest_title


def test_same_month_uses_compact_range():
    assert (
        format_digest_title(date(2026, 5, 19), date(2026, 5, 26))
        == "Newsletter Digest: May 19 – May 26 2026"
    )


def test_cross_month_repeats_month_name():
    assert (
        format_digest_title(date(2026, 5, 28), date(2026, 6, 3))
        == "Newsletter Digest: May 28 – June 3 2026"
    )


def test_cross_year_includes_both_years():
    assert (
        format_digest_title(date(2026, 12, 30), date(2027, 1, 5))
        == "Newsletter Digest: December 30 2026 – January 5 2027"
    )


def test_single_day_range_collapses_to_one_date():
    # A one-day window is degenerate but should not produce "May 19 – May 19".
    assert (
        format_digest_title(date(2026, 5, 19), date(2026, 5, 19))
        == "Newsletter Digest: May 19 2026"
    )


def test_end_before_start_is_rejected():
    import pytest

    with pytest.raises(ValueError, match="end .* before start"):
        format_digest_title(date(2026, 5, 26), date(2026, 5, 19))
