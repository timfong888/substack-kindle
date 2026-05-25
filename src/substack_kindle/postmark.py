"""Send the compiled EPUB to Kindle via the Postmark REST API (SAT-250 / Req §Provider).

The EPUB is delivered as a base64 attachment (ContentType ``application/epub+zip``)
through Postmark's ``/email`` REST endpoint — the MCP send-email tool cannot
carry attachments, so it is deliberately not used here. The FROM address is the
shared, verified whitelist sender signature. Any Postmark API error (non-2xx, or a
non-zero ErrorCode on a 2xx) raises ``PostmarkError`` so the job records a failure
outcome rather than a silent success. The HTTP transport is injected — no live
network calls and no committed credentials.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass

POSTMARK_EMAIL_URL = "https://api.postmarkapp.com/email"
EPUB_CONTENT_TYPE = "application/epub+zip"
DEFAULT_SUBJECT = "Your newsletters"


class PostmarkError(Exception):
    """Raised when Postmark reports a delivery failure."""


@dataclass
class SendResult:
    message_id: str
    to: str


def send_epub(
    *,
    epub_bytes: bytes,
    to: str,
    from_: str,
    filename: str,
    server_token: str,
    http_post: Callable[..., object],
    subject: str = DEFAULT_SUBJECT,
    message_stream: str = "outbound",
) -> SendResult:
    """Email ``epub_bytes`` to ``to`` from the whitelist sender ``from_`` via Postmark."""
    payload = {
        "From": from_,
        "To": to,
        "Subject": subject,
        "MessageStream": message_stream,
        "Attachments": [
            {
                "Name": filename,
                "Content": base64.b64encode(epub_bytes).decode("ascii"),
                "ContentType": EPUB_CONTENT_TYPE,
            }
        ],
    }
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Postmark-Server-Token": server_token,
    }

    response = http_post(POSTMARK_EMAIL_URL, json=payload, headers=headers)
    body = response.json()
    if not (200 <= response.status_code < 300):
        raise PostmarkError(
            f"Postmark returned HTTP {response.status_code}: {body.get('Message', body)}"
        )
    # Postmark can return 200 with a non-zero ErrorCode — still a failure.
    if body.get("ErrorCode", 0) != 0:
        raise PostmarkError(f"Postmark error {body['ErrorCode']}: {body.get('Message', '')}")
    return SendResult(message_id=body.get("MessageID", ""), to=to)
