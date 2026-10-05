"""Read proxy-inbox newsletters from Convex for a job window.

Inbound mail arrives at a per-user proxy address (Resend -> Convex webhook,
see ``web/convex``). This module asks Convex for the stored newsletter emails
in a window and maps them to provider-neutral ``handler.InboundMessage``s, so
the existing parse -> EPUB -> send pipeline runs unchanged.

The Convex client is injected (anything with ``.query(name, args)``); use
``make_client(url)`` for the real one. ``secret`` is the deployment's
``PIPELINE_SHARED_SECRET``. It is never sent: each call carries an HMAC over
the address, window and issue time instead, so the raw secret stays out of
Convex's logs (see ``web/convex/pipeline.ts``).
"""

from __future__ import annotations

import hashlib
import hmac
import html
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from .handler import InboundMessage

LIST_INBOUND_FOR_WINDOW = "pipeline:listInboundForWindow"

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MS = timedelta(milliseconds=1)


class ConvexQueryClient(Protocol):
    def query(self, name: str, args: dict[str, Any] | None = None) -> Any: ...


def _epoch_ms(dt: datetime) -> int:
    return (dt - _EPOCH) // _MS


def pipeline_signature(
    secret: str, proxy_address: str, start_ms: int, end_ms: int, issued_at_ms: int
) -> str:
    """hex(HMAC-SHA256(secret, "address:start:end:issuedAt")); must match pipeline.ts."""
    message = f"{proxy_address}:{start_ms}:{end_ms}:{issued_at_ms}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def _row_to_message(row: dict[str, Any]) -> InboundMessage:
    body = row.get("html")
    if body is None:
        # html is dropped when oversize (htmlTruncated); fall back to plain text.
        text = row.get("text")
        if text:
            body = f"<pre>{html.escape(text, quote=False)}</pre>"
        elif row.get("htmlTruncated") or row.get("textTruncated"):
            # Both bodies dropped for size: keep the issue visible in the digest
            # rather than an empty body the handler would silently skip.
            body = "<p>This issue was too large to include. Read it in your email or online.</p>"
        else:
            # No body at all (e.g. image-only email): same reason, stay visible.
            body = "<p>This issue has no readable text. Read it in your email or online.</p>"
    return InboundMessage(
        sender=row["from"],
        subject=row["subject"],
        date_sent=_EPOCH + timedelta(milliseconds=row["receivedAt"]),
        html_body=body,
        message_id=row.get("messageId") or row["resendEmailId"],
    )


def fetch_inbound(
    client: ConvexQueryClient,
    *,
    proxy_address: str,
    window_start: datetime,
    window_end: datetime,
    secret: str,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> list[InboundMessage]:
    """Newsletters received at ``proxy_address`` within the window, oldest first.

    The window is inclusive of both bounds (matching ``collection``); datetimes
    must be timezone-aware. Errors from the client (e.g. a wrong secret)
    propagate.
    """
    if window_start.tzinfo is None or window_end.tzinfo is None:
        raise ValueError("window_start and window_end must be timezone-aware")
    if window_start > window_end:
        raise ValueError("window_start must not be after window_end")
    if not secret:
        raise ValueError("pipeline secret is empty")

    start_ms = _epoch_ms(window_start)
    end_ms = _epoch_ms(window_end) + 1  # Convex end bound is exclusive; ours is inclusive.
    issued_at_ms = _epoch_ms(now())
    rows = client.query(
        LIST_INBOUND_FOR_WINDOW,
        {
            "proxyAddress": proxy_address,
            # Convex v.number() is float64; Python ints would encode as int64.
            "start": float(start_ms),
            "end": float(end_ms),
            "issuedAt": float(issued_at_ms),
            "signature": pipeline_signature(secret, proxy_address, start_ms, end_ms, issued_at_ms),
        },
    )
    return [_row_to_message(row) for row in rows]


def make_client(url: str) -> ConvexQueryClient:
    """Real Convex client. Imported lazily so tests and CLI paths need no package."""
    try:
        from convex import ConvexClient
    except ImportError as exc:
        raise ImportError(
            "The 'convex' package is required for the proxy inbox; run `uv add convex`."
        ) from exc
    return ConvexClient(url)
