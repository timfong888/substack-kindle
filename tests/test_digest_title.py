"""Tests for the digest title format (SAT-264).

Format: ``Substacks · {Mon DD}–{DD, YYYY}`` with an en-dash. The fixed
prefix and ISO-ordered en-dash range mean every digest groups and sorts
chronologically on a Kindle library row.
"""

from datetime import date

from substack_kindle.digest_title import format_digest_title


def test_same_month_uses_compact_range():
    assert (
        format_digest_title(date(2026, 5, 19), date(2026, 5, 26))
        == "Substacks · May 19–26, 2026"
    )


def test_cross_month_repeats_month_name():
    assert (
        format_digest_title(date(2026, 5, 28), date(2026, 6, 3))
        == "Substacks · May 28–Jun 3, 2026"
    )


def test_cross_year_includes_both_years():
    assert (
        format_digest_title(date(2026, 12, 30), date(2027, 1, 5))
        == "Substacks · Dec 30, 2026–Jan 5, 2027"
    )


def test_single_day_range_collapses_to_one_date():
    # A one-day window is degenerate but should not produce "May 19–19".
    assert (
        format_digest_title(date(2026, 5, 19), date(2026, 5, 19))
        == "Substacks · May 19, 2026"
    )


def test_end_before_start_is_rejected():
    import pytest

    with pytest.raises(ValueError, match="end .* before start"):
        format_digest_title(date(2026, 5, 26), date(2026, 5, 19))
