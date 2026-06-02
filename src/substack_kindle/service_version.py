"""Service version + EPUB subheader (SAT-272).

The "Newsletters to Kindle v{N}" subheader appears in two places on each
digest (OPF ``dc:description`` and the front-matter H4), so we centralise the
string here. The version itself comes from the installed package metadata so
a release-please-style action bumping ``pyproject.toml`` flips the subheader
without any code change.

The ``version_source`` parameter is the seam tests use to lock in a specific
version without touching the package metadata.
"""

from __future__ import annotations

from collections.abc import Callable
from importlib.metadata import version as _installed_version

BRAND_NAME = "Newsletters to Kindle"
PACKAGE_NAME = "substack-kindle"


def _default_version_source() -> str:
    return _installed_version(PACKAGE_NAME)


def service_version(*, version_source: Callable[[], str] | None = None) -> str:
    """Return the current service version (e.g. ``"0.2.0"``).

    Reads ``importlib.metadata.version("substack-kindle")`` by default; tests
    inject a fake to lock in a specific string.
    """
    source = version_source or _default_version_source
    return source()


def service_subheader(*, version_source: Callable[[], str] | None = None) -> str:
    """Return the subheader line shown in digest metadata + front matter."""
    return f"{BRAND_NAME} v{service_version(version_source=version_source)}"
