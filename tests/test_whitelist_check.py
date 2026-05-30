"""Tests for the WHITELIST_EMAIL ↔ KINDLE_EMAIL local-part collision check (SAT-269).

Amazon documents an approved-sender list that bypasses per-document verification,
but a long-standing bug fires verification anyway when the sender's local-part
equals the Kindle address's local-part (see SAT-270 research). The pipeline
must refuse to run in that configuration so we don't silently spam the customer
with verification emails.
"""

import pytest

from substack_kindle.whitelist_check import (
    LocalPartCollision,
    ensure_distinct_local_parts,
)


def test_distinct_local_parts_passes():
    # Different local-parts → no exact-match collision.
    ensure_distinct_local_parts(
        whitelist_email="digest@example.com",
        kindle_email="reader@kindle.com",
    )


def test_exact_local_part_collision_raises():
    with pytest.raises(LocalPartCollision, match="local-part"):
        ensure_distinct_local_parts(
            whitelist_email="user@example.com",
            kindle_email="user@kindle.com",
        )


def test_collision_check_is_case_insensitive():
    # Amazon's verification heuristic should be normalised to lower; treat case
    # collisions as collisions so we don't get bitten by a casing mismatch.
    with pytest.raises(LocalPartCollision):
        ensure_distinct_local_parts(
            whitelist_email="User@example.com",
            kindle_email="user@kindle.com",
        )


def test_missing_at_sign_raises_value_error():
    with pytest.raises(ValueError, match="not a valid email"):
        ensure_distinct_local_parts(
            whitelist_email="not-an-email",
            kindle_email="reader@kindle.com",
        )
    with pytest.raises(ValueError, match="not a valid email"):
        ensure_distinct_local_parts(
            whitelist_email="digest@example.com",
            kindle_email="not-an-email",
        )


def test_empty_local_part_raises_value_error():
    # `@example.com` has an empty local-part — invalid input, must be caught.
    with pytest.raises(ValueError, match="not a valid email"):
        ensure_distinct_local_parts(
            whitelist_email="@example.com",
            kindle_email="reader@kindle.com",
        )
    with pytest.raises(ValueError, match="not a valid email"):
        ensure_distinct_local_parts(
            whitelist_email="digest@example.com",
            kindle_email="@kindle.com",
        )


def test_empty_domain_raises_value_error():
    # `digest@` has an empty domain — invalid input, must be caught.
    with pytest.raises(ValueError, match="not a valid email"):
        ensure_distinct_local_parts(
            whitelist_email="digest@",
            kindle_email="reader@kindle.com",
        )
    with pytest.raises(ValueError, match="not a valid email"):
        ensure_distinct_local_parts(
            whitelist_email="digest@example.com",
            kindle_email="reader@",
        )
