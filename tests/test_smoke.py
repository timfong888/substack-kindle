"""Smoke test proving the package imports and CI runs end-to-end.

Real feature tests arrive with their stories (TDD: tests written before code).
"""

import substack_kindle


def test_package_imports_and_has_version():
    assert substack_kindle.__version__
