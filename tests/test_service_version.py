"""Tests for the service version + EPUB subheader (SAT-272).

The "Newsletters to Kindle v{N}" subheader needs a single source of truth so a
change to the underlying version flips both the OPF metadata and the front-
matter heading at once. The number itself comes from the installed package
version (auto-bumped per merged PR via a release-please-style action), so
tests inject a fake version-source rather than freezing a literal in code.
"""

from substack_kindle.service_version import (
    BRAND_NAME,
    service_subheader,
    service_version,
)


def test_service_version_returns_installed_package_version():
    # Whatever pyproject.toml ships, ``service_version`` reflects it. We assert
    # the shape (dotted digits) rather than a literal so a version bump in a
    # follow-up commit doesn't break this test.
    v = service_version()
    assert v
    parts = v.split(".")
    assert all(p.isdigit() for p in parts), f"expected dotted-int version, got {v!r}"


def test_service_subheader_combines_brand_and_version():
    assert service_subheader() == f"{BRAND_NAME} v{service_version()}"


def test_service_subheader_uses_injected_version_source():
    # Tests can flip the source to lock in a specific subheader without touching
    # the installed package version.
    out = service_subheader(version_source=lambda: "1.2.3")
    assert out == f"{BRAND_NAME} v1.2.3"


def test_service_version_uses_injected_source():
    assert service_version(version_source=lambda: "9.9.9") == "9.9.9"
