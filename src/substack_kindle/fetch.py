"""Gmail → JobSection fetch pipeline (SAT-269).

Bridges a ``ReadOnlyGmailClient`` (SAT-241) and the EPUB builder (SAT-246):

1. Build a Gmail ``q=`` query that narrows to approved senders + date window.
2. List message IDs, fetch each full message.
3. Extract From/Subject/Date headers and the HTML (or text/plain) body.
4. Run the body through ``parsing.html_to_markdown`` so SAT-265's Substack
   chrome cleaner applies before the EPUB is built.
5. Return ``JobSection`` records in chronological order, ready for
   ``pipeline.run_job``'s ``build_epub`` collaborator.

No LLM is invoked on body text (Req 8 holds — body parsing is library-only).
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any, Protocol

from .job_epub import JobSection
from .parsing import html_to_markdown


class GmailFetchError(Exception):
    """Raised when a Gmail message lacks the structure the fetch layer requires."""


@dataclass(frozen=True)
class MessageHeaders:
    from_address: str
    subject: str
    date: datetime


class _ListAndGetClient(Protocol):
    """The slice of ``ReadOnlyGmailClient`` the fetch layer uses."""

    def list_message_ids(self, query: str | None = None) -> list[str]: ...
    def get_message(self, message_id: str) -> dict: ...


def _header_value(payload: dict, name: str) -> str:
    for header in payload.get("headers", []):
        if header.get("name", "").lower() == name.lower():
            return header.get("value", "")
    return ""


def extract_headers(message: dict) -> MessageHeaders:
    """Return the parsed From / Subject / Date headers."""
    payload = message.get("payload") or {}
    raw_from = _header_value(payload, "From")
    _, address = parseaddr(raw_from)
    subject = _header_value(payload, "Subject")
    raw_date = _header_value(payload, "Date")
    try:
        date = parsedate_to_datetime(raw_date)
    except (TypeError, ValueError) as exc:
        raise GmailFetchError(f"could not parse Date header {raw_date!r}: {exc}") from exc
    if date.tzinfo is None:
        # RFC 5322 dates without a TZ are extremely rare; treat as UTC so
        # downstream window comparisons keep working.
        date = date.replace(tzinfo=UTC)
    return MessageHeaders(from_address=address.lower(), subject=subject, date=date)


def _decode_b64url(data: str) -> str:
    # Gmail's API returns body bytes as base64url. Add padding so the decoder
    # does not raise on stripped trailing '='.
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding).decode("utf-8", errors="replace")


def _walk_parts(payload: dict):
    yield payload
    for part in payload.get("parts", []) or []:
        yield from _walk_parts(part)


def extract_body_html(message: dict) -> str:
    """Return the message body as HTML when available, else text/plain.

    Prefers the ``text/html`` part in a multipart message; falls back to
    ``text/plain``. Raises ``GmailFetchError`` if neither is present.
    """
    payload = message.get("payload") or {}
    html_data: str | None = None
    plain_data: str | None = None
    for part in _walk_parts(payload):
        mime = (part.get("mimeType") or "").lower()
        data = (part.get("body") or {}).get("data")
        if not data:
            continue
        if mime == "text/html" and html_data is None:
            html_data = data
        elif mime == "text/plain" and plain_data is None:
            plain_data = data
    if html_data is not None:
        return _decode_b64url(html_data)
    if plain_data is not None:
        return _decode_b64url(plain_data)
    raise GmailFetchError(f"message {message.get('id')!r} has no body")


def _gmail_query(approved_sources: list[str], window_start: datetime, window_end: datetime) -> str:
    """Build a Gmail search query that narrows to approved senders + window.

    The window is inclusive of both bounds, but Gmail's ``before:`` operator is
    exclusive of its day, so we add a day to the end bound to keep the inclusive
    contract that ``collection.collect_newsletters`` also enforces.
    """
    from_clause = " OR ".join(f"from:{src}" for src in approved_sources)
    after = window_start.strftime("%Y/%m/%d")
    before = (window_end + timedelta(days=1)).strftime("%Y/%m/%d")
    return f"({from_clause}) after:{after} before:{before}"


def fetch_newsletters(
    client: _ListAndGetClient,
    *,
    approved_sources: list[str],
    window_start: datetime,
    window_end: datetime,
) -> list[JobSection]:
    """Return ``JobSection`` records for approved-sender messages in the window.

    Output is sorted by message date ascending so the EPUB's TOC reads
    chronologically. Senders are matched case-insensitively against
    ``approved_sources``; messages outside the window or from un-approved
    senders are dropped (a belt-and-suspenders filter, since the Gmail query
    already narrows server-side).

    An empty ``approved_sources`` is treated as a misconfiguration: we refuse
    to issue a mailbox-wide Gmail query, since the resulting filter would drop
    every message anyway and the read is wasted (and the contract is "fetch
    from approved senders" — an empty allowlist is nonsense, not an empty
    result).
    """
    if not approved_sources:
        raise ValueError("approved_sources must not be empty")
    approved = {s.casefold() for s in approved_sources}
    query = _gmail_query(approved_sources, window_start, window_end)
    ids = client.list_message_ids(query=query)
    enriched: list[tuple[datetime, JobSection]] = []
    for message_id in ids:
        message = client.get_message(message_id)
        headers = extract_headers(message)
        if headers.from_address.casefold() not in approved:
            continue
        if not (window_start <= headers.date <= window_end):
            continue
        body_html = extract_body_html(message)
        markdown = html_to_markdown(body_html)
        enriched.append(
            (headers.date, JobSection(title=headers.subject, markdown=markdown))
        )
    enriched.sort(key=lambda pair: pair[0])
    return [section for _, section in enriched]


__all__ = [
    "GmailFetchError",
    "MessageHeaders",
    "extract_headers",
    "extract_body_html",
    "fetch_newsletters",
]


# Suppress unused-import warning for `Any` (kept for future typing of the
# message dict if we ever swap dict[str, Any] for a TypedDict).
_ = Any
