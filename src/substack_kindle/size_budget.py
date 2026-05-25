"""Keep sends under Postmark's 10 MB message cap (SAT-251 / PRD §Constraints).

Postmark caps a whole message at 10 MB, and attachments are base64-encoded
(~33% larger). ``plan_send`` makes an oversized EPUB deliverable instead of
silently dropped: it first tries ZIP compression, and if a single message still
won't fit it splits the payload into numbered parts that each fit under the cap
post-base64. Splitting never loses bytes — concatenating the parts reconstructs
the (optionally zipped) payload.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from io import BytesIO

POSTMARK_MAX_MESSAGE_BYTES = 10 * 1024 * 1024
EPUB_CONTENT_TYPE = "application/epub+zip"
ZIP_CONTENT_TYPE = "application/zip"


def base64_encoded_size(raw_bytes: int) -> int:
    """Length of the base64 encoding of ``raw_bytes`` bytes (with padding)."""
    return ((raw_bytes + 2) // 3) * 4


def _max_raw_bytes(max_message_bytes: int) -> int:
    """Largest raw byte count whose base64 encoding still fits under the cap."""
    return (max_message_bytes // 4) * 3


def _zip_bytes(payload: bytes, inner_name: str) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(inner_name, payload)
    return buffer.getvalue()


@dataclass
class SendPlan:
    parts: list[bytes]
    filenames: list[str]
    content_type: str
    compressed: bool

    @property
    def total_parts(self) -> int:
        return len(self.parts)


def plan_send(
    payload: bytes,
    *,
    max_message_bytes: int = POSTMARK_MAX_MESSAGE_BYTES,
    filename: str = "job.epub",
) -> SendPlan:
    """Return a SendPlan whose every part fits under ``max_message_bytes`` post-base64."""
    # A cap below one base64 block can't hold any data and would make the split
    # chunk size zero (range() step of 0 raises); fail fast with a clear error.
    if max_message_bytes < 4:
        raise ValueError("max_message_bytes must be at least 4 (one base64 block)")

    # 1. Fits as-is.
    if base64_encoded_size(len(payload)) <= max_message_bytes:
        return SendPlan([payload], [filename], EPUB_CONTENT_TYPE, compressed=False)

    # 2. Try ZIP; keep it only if it actually shrinks the payload.
    zipped = _zip_bytes(payload, filename)
    if len(zipped) < len(payload):
        candidate, content_type, compressed, base_name = (
            zipped,
            ZIP_CONTENT_TYPE,
            True,
            filename + ".zip",
        )
    else:
        candidate, content_type, compressed, base_name = (
            payload,
            EPUB_CONTENT_TYPE,
            False,
            filename,
        )

    # 3. Single (possibly zipped) message fits.
    if base64_encoded_size(len(candidate)) <= max_message_bytes:
        return SendPlan([candidate], [base_name], content_type, compressed)

    # 4. Still too big: split into numbered parts that each fit under the cap.
    chunk = _max_raw_bytes(max_message_bytes)
    parts = [candidate[i : i + chunk] for i in range(0, len(candidate), chunk)]
    total = len(parts)
    filenames = [f"{base_name}.{index:03d}of{total:03d}" for index in range(1, total + 1)]
    return SendPlan(parts, filenames, content_type, compressed)
