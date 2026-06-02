"""Human-readable digest book-title for the EPUB delivered to Kindle (SAT-272).

Format: ``Newsletter Digest: {Month D} – {Month D} {YYYY}`` — full month name,
en-dash with single spaces around it, year once at the end. Replaces the
SAT-264 shorthand so the title reads like a real book title on the Kindle
library row, not a header chip. The same month name is repeated on both sides
even within a single month (e.g. "May 19 – May 26 2026"), which trades a few
characters for clarity at a glance and avoids the "May 19–26" ambiguity that
some readers parse as a sub-issue number.
"""

from __future__ import annotations

from datetime import date

_PREFIX = "Newsletter Digest"
_DASH = "–"  # U+2013 en-dash for ranges, by convention.

# Full month names match Python's calendar.month_name but are spelled out here
# so the code stays locale-independent (the C locale's %B varies by platform).
_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


def _month_day(d: date) -> str:
    """Return the ``Month D`` half of a date (no year)."""
    return f"{_MONTHS[d.month - 1]} {d.day}"


def _month_day_year(d: date) -> str:
    """Return the ``Month D YYYY`` triple of a date."""
    return f"{_month_day(d)} {d.year}"


def format_digest_title(start: date, end: date) -> str:
    """Return the EPUB title for a digest covering ``[start, end]`` inclusive."""
    if end < start:
        raise ValueError(f"end ({end.isoformat()}) is before start ({start.isoformat()})")

    if start == end:
        # Single-day window: no en-dash, just the one date.
        return f"{_PREFIX}: {_month_day_year(start)}"

    if start.year != end.year:
        # Cross-year: both years are explicit so the range is unambiguous on
        # December → January boundaries.
        return f"{_PREFIX}: {_month_day_year(start)} {_DASH} {_month_day_year(end)}"

    # Same year (whether same month or cross-month): the year is shown once,
    # at the end. The month name is always repeated on both sides for clarity.
    return f"{_PREFIX}: {_month_day(start)} {_DASH} {_month_day(end)} {start.year}"
