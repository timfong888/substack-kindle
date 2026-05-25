"""Read-only Gmail OAuth connection (SAT-241 / Reqs 1, 9).

The service connects to a customer's Gmail with a single, read-only scope and
exposes a client that can ONLY read (list/get messages). There are no mutating
operations (no label add/remove, archive, trash, delete, send), so the mailbox
is never modified. The OAuth token is per customer, held as a runtime reference
(never the raw token, never committed) and passed to the transport on each call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"

# Scopes that grant any write/modify capability — never acceptable here.
_ALLOWED_SCOPES = frozenset({GMAIL_READONLY_SCOPE})


class ScopeError(Exception):
    """Raised when credentials carry anything beyond the read-only Gmail scope."""


def requested_scopes() -> list[str]:
    """The OAuth scopes the connect flow asks for: read-only Gmail, nothing else."""
    return [GMAIL_READONLY_SCOPE]


@dataclass(frozen=True)
class OAuthCredentials:
    """Per-customer OAuth credentials.

    ``token_ref`` is a reference to the token in a secrets manager (resolved at
    runtime), never the raw token. It is redacted from ``repr``.
    """

    token_ref: str
    scopes: tuple[str, ...]

    def __repr__(self) -> str:
        return f"OAuthCredentials(token_ref='***redacted***', scopes={self.scopes!r})"


class GmailTransport(Protocol):
    """Transport that performs Gmail REST calls. ``method`` is the HTTP verb."""

    def request(
        self, method: str, path: str, *, token_ref: str, params: dict | None = None
    ) -> dict: ...


class ReadOnlyGmailClient:
    """A Gmail client restricted to read operations.

    Construction fails unless the granted scopes are exactly the read-only scope,
    so a token that could modify the mailbox can never back this client.
    """

    def __init__(self, credentials: OAuthCredentials, transport: GmailTransport) -> None:
        if GMAIL_READONLY_SCOPE not in credentials.scopes:
            raise ScopeError("read-only Gmail scope is required")
        extra = set(credentials.scopes) - _ALLOWED_SCOPES
        if extra:
            raise ScopeError(f"non read-only scopes are not allowed: {sorted(extra)}")
        self._credentials = credentials
        self._transport = transport

    @property
    def granted_scopes(self) -> tuple[str, ...]:
        return self._credentials.scopes

    def list_message_ids(self, query: str | None = None) -> list[str]:
        """Return all matching message ids, following nextPageToken to the end."""
        ids: list[str] = []
        page_token: str | None = None
        while True:
            params: dict = {}
            if query:
                params["q"] = query
            if page_token:
                params["pageToken"] = page_token
            result = self._transport.request(
                "GET",
                "/users/me/messages",
                token_ref=self._credentials.token_ref,
                params=params or None,
            )
            ids.extend(m["id"] for m in result.get("messages", []))
            page_token = result.get("nextPageToken")
            if not page_token:
                return ids

    def get_message(self, message_id: str) -> dict:
        return self._transport.request(
            "GET",
            f"/users/me/messages/{message_id}",
            token_ref=self._credentials.token_ref,
        )
