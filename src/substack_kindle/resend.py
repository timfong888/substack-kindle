"""Send the compiled EPUB to Kindle via the Resend REST API (Req §Provider).

The EPUB is delivered as a base64 attachment (``content_type``
``application/epub+zip``) through Resend's ``POST /emails`` endpoint. The FROM
address is the shared whitelist sender on our Resend-verified domain. Any Resend
API error (non-2xx, or a 2xx without an email ``id``) raises ``ResendError`` so
the job records a failure outcome rather than a silent success. The HTTP
transport is injected — no live network calls and no committed credentials.

``idempotency_key`` is optional and not set by the RSS CLI (its JSON state store
already dedups, and the live e2e test deliberately re-sends a window). The
scheduled job (SAT-1104/SAT-1105) passes a per-window key so a delayed or
duplicated run cannot double-send: Resend drops a repeat with the same key for
24 hours, and answers 409 if the same key is reused with a different payload.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass

from .size_budget import EPUB_CONTENT_TYPE

RESEND_EMAILS_URL = "https://api.resend.com/emails"
DEFAULT_SUBJECT = "Your newsletters"
DEFAULT_TEXT_BODY = "Your newsletters are attached as an EPUB."


class ResendError(Exception):
    """Raised when Resend reports a delivery failure."""


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
    api_key: str,
    http_post: Callable[..., object],
    subject: str = DEFAULT_SUBJECT,
    text_body: str = DEFAULT_TEXT_BODY,
    idempotency_key: str | None = None,
) -> SendResult:
    """Email ``epub_bytes`` to ``to`` from the whitelist sender ``from_`` via Resend."""
    payload = {
        "from": from_,
        "to": [to],
        "subject": subject,
        # Resend requires html or text; Send-to-Kindle only uses the attachment.
        "text": text_body,
        "attachments": [
            {
                "filename": filename,
                "content": base64.b64encode(epub_bytes).decode("ascii"),
                "content_type": EPUB_CONTENT_TYPE,
            }
        ],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key

    response = http_post(RESEND_EMAILS_URL, json=payload, headers=headers)
    # Check the HTTP status before parsing JSON: a 5xx gateway may return a
    # non-JSON (HTML) body, which must still surface as a ResendError.
    if not (200 <= response.status_code < 300):
        raise ResendError(f"Resend returned HTTP {response.status_code}: {_message(response)}")
    body = response.json()
    message_id = body.get("id") if isinstance(body, dict) else None
    if not message_id:
        raise ResendError(f"Resend returned no email id: {body!r}")
    return SendResult(message_id=message_id, to=to)


def _message(response: object) -> str:
    """Best-effort error text from a (possibly non-JSON) error response.

    Keeps Resend's stable ``name`` code (``validation_error``,
    ``rate_limit_exceeded``, ...) ahead of the free-text ``message`` so a caller
    can tell a retryable failure from a permanent one.
    """
    try:
        body = response.json()
        name, message = body.get("name", ""), body.get("message", "")
        text = f"{name}: {message}" if name else message
        return text or getattr(response, "text", "")
    except Exception:
        return getattr(response, "text", "") or "unknown error"
