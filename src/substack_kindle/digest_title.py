"""Human-readable digest title for the EPUB delivered to Kindle (SAT-264).

Format: ``Substacks · {Mon DD}–{DD, YYYY}`` (en-dash). The fixed prefix and
ISO-ordered range keep digests grouped and chronologically sorted on the Kindle
library row. The compact range only repeats month/year when the boundary
crosses them, which keeps the common (same-month) case short.
"""

from __future__ import annotations

from datetime import date

_PREFIX = "Substacks"
_SEP = "·"  # U+00B7 middle dot — readable separator without visual weight.
_DASH = "–"  # U+2013 en-dash for ranges, by convention.


def _fmt(d: date, *, with_year: bool) -> str:
    # %b is locale-dependent; tests pin the C locale via short month names like
    # "May"/"Jun"/"Dec", which match POSIX. If we later localise, this is the
    # single point to change. Day-of-month uses `d.day` (an int) so the format
    # stays portable — strftime("%-d") is POSIX-only and ValueErrors on Windows.
    base = f"{d.strftime('%b')} {d.day}"
    return f"{base}, {d.year}" if with_year else base


def format_digest_title(start: date, end: date) -> str:
    """Return the EPUB title for a digest covering [start, end] inclusive."""
    if end < start:
        raise ValueError(f"end ({end.isoformat()}) is before start ({start.isoformat()})")

    if start == end:
        return f"{_PREFIX} {_SEP} {_fmt(start, with_year=True)}"

    if start.year != end.year:
        # Cross-year: keep both years explicit so the range is unambiguous.
        return (
            f"{_PREFIX} {_SEP} "
            f"{_fmt(start, with_year=True)}{_DASH}{_fmt(end, with_year=True)}"
        )

    if start.month != end.month:
        # Cross-month, same year: repeat the month name on the end side.
        return (
            f"{_PREFIX} {_SEP} "
            f"{_fmt(start, with_year=False)}{_DASH}{_fmt(end, with_year=True)}"
        )

    # Same month: collapse to "May 19–26, 2026". Reuse _fmt so any future
    # format change happens in exactly one place.
    return (
        f"{_PREFIX} {_SEP} "
        f"{_fmt(start, with_year=False)}{_DASH}{end.day}, {end.year}"
    )
