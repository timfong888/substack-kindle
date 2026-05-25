"""Tests for the Postmark 10 MB size budget (SAT-251 / #15, PRD §Constraints).

Acceptance:
- A compiled EPUB approaching the cap (post-base64) is split or ZIPped before send.
- A fixture exceeding the cap does not produce a silently-dropped send.
"""

import base64
import os
import zipfile
from io import BytesIO

import pytest

from substack_kindle.size_budget import (
    POSTMARK_MAX_MESSAGE_BYTES,
    base64_encoded_size,
    plan_send,
)


def _b64_len(part: bytes) -> int:
    return len(base64.b64encode(part))


def test_default_cap_is_10mb():
    assert POSTMARK_MAX_MESSAGE_BYTES == 10 * 1024 * 1024


def test_base64_encoded_size_matches_stdlib():
    for n in (0, 1, 2, 3, 4, 100, 999):
        assert base64_encoded_size(n) == _b64_len(b"x" * n)


def test_small_payload_is_a_single_uncompressed_part():
    payload = b"tiny epub bytes"
    plan = plan_send(payload, max_message_bytes=1000, filename="job.epub")
    assert plan.compressed is False
    assert plan.content_type == "application/epub+zip"
    assert plan.parts == [payload]
    assert plan.total_parts == 1


def test_compressible_payload_over_cap_is_zipped():
    # Highly repetitive bytes compress well; over the raw cap but under it once zipped.
    payload = b"A" * 5000
    plan = plan_send(payload, max_message_bytes=400, filename="job.epub")
    assert plan.compressed is True
    assert plan.content_type == "application/zip"
    assert plan.total_parts == 1
    # The single zip part fits under the cap and reconstructs the original.
    assert _b64_len(plan.parts[0]) <= 400
    with zipfile.ZipFile(BytesIO(plan.parts[0])) as zf:
        assert zf.read("job.epub") == payload


def test_incompressible_payload_over_cap_is_split_not_dropped():
    payload = os.urandom(4000)  # random => not meaningfully compressible
    cap = 400
    plan = plan_send(payload, max_message_bytes=cap, filename="job.epub")
    assert plan.total_parts > 1  # split into multiple sends, never silently dropped
    assert len(plan.parts) == plan.total_parts
    # Every part fits under the cap after base64.
    for part in plan.parts:
        assert _b64_len(part) <= cap
    # Reassembling the parts reconstructs the original payload.
    assert b"".join(plan.parts) == payload


def test_every_part_is_within_cap_for_various_sizes():
    cap = 512
    for size in (10, 600, 5000, 20000):
        payload = os.urandom(size)  # random => incompressible, so never zipped
        plan = plan_send(payload, max_message_bytes=cap)
        for part in plan.parts:
            assert _b64_len(part) <= cap
        # No bytes lost: parts reassemble to the original payload.
        assert b"".join(plan.parts) == payload


def test_part_filenames_are_numbered_when_split():
    payload = os.urandom(4000)
    plan = plan_send(payload, max_message_bytes=400, filename="job.epub")
    assert len(plan.filenames) == plan.total_parts
    assert len(set(plan.filenames)) == plan.total_parts  # unique names per part


def test_cap_below_one_base64_block_raises():
    # A cap < 4 can't hold a base64 block; it must fail fast, not blow up with a
    # range() step-of-zero ValueError deep in the split path.
    with pytest.raises(ValueError, match="at least 4"):
        plan_send(b"anything", max_message_bytes=2)
