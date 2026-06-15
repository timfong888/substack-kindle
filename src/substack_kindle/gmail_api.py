"""Concrete Gmail transport using ``google-api-python-client`` (SAT-269).

Translates the abstract :class:`gmail_oauth.GmailTransport` protocol onto the
real Gmail REST API surface. This module is the only place that imports the
heavy Google client libraries, so unit tests and dry-runs can stay free of
network dependencies by injecting a fake transport via the protocol.

The OAuth bundle layout matches the standalone tool's convention to ease
migration:

    {bundle_dir}/
        client_secret.json   # downloaded OAuth client (gitignored — never commit)
        credentials.json     # cached user token; refreshed in place

Only the read-only Gmail scope is requested (Req 1, SAT-241). The
``ReadOnlyGmailClient`` constructor enforces that scope at runtime.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .gmail_oauth import (
    GMAIL_READONLY_SCOPE,
    OAuthCredentials,
    ReadOnlyGmailClient,
)

TOKEN_URI = "https://oauth2.googleapis.com/token"


class GoogleApiGmailTransport:
    """Maps the ``GmailTransport`` protocol onto a googleapiclient Gmail service.

    The protocol talks REST paths; this adapter dispatches each path to the
    equivalent ``service.users().messages().*`` call. Only the two operations
    ``ReadOnlyGmailClient`` exercises (``list``, ``get``) are supported — any
    other path is rejected so a future caller cannot accidentally grow the
    surface.
    """

    def __init__(self, service):
        self._service = service

    def request(
        self,
        method: str,
        path: str,
        *,
        token_ref: str,  # noqa: ARG002 — kept to satisfy the protocol
        params: dict | None = None,
    ) -> dict:
        if method.upper() != "GET":
            raise NotImplementedError(
                f"GoogleApiGmailTransport supports GET only (got {method!r})"
            )
        messages = self._service.users().messages()
        if path == "/users/me/messages":
            params = params or {}
            return messages.list(
                userId="me",
                q=params.get("q"),
                pageToken=params.get("pageToken"),
            ).execute()
        prefix = "/users/me/messages/"
        if path.startswith(prefix):
            message_id = path[len(prefix):]
            if not message_id or "/" in message_id:
                raise NotImplementedError(f"unsupported message path: {path!r}")
            return messages.get(userId="me", id=message_id, format="full").execute()
        raise NotImplementedError(f"unsupported Gmail path: {path!r}")


def build_gmail_client(bundle_dir: Path) -> ReadOnlyGmailClient:
    """Build a read-only Gmail client from an OAuth bundle directory.

    Reads ``credentials.json``; refreshes when expired; runs the installed-app
    OAuth flow when no credentials are cached. The resulting service is wrapped
    in a :class:`ReadOnlyGmailClient` so callers cannot mutate the mailbox.
    """
    # Lazy imports: the heavy Google libs aren't needed for tests that inject
    # a fake transport via the ``GmailTransport`` protocol.
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    bundle_dir = Path(bundle_dir).expanduser()
    client_secret = bundle_dir / "client_secret.json"
    creds_file = bundle_dir / "credentials.json"

    creds = _load_cached(creds_file)
    if creds is not None and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _persist(creds_file, creds)
        except Exception:  # noqa: BLE001 — fall through to a fresh flow
            creds = None
    if creds is None or not creds.valid:
        if not client_secret.exists():
            raise FileNotFoundError(
                f"OAuth client secret missing at {client_secret}. "
                "See vault Credentials Registry."
            )
        flow = InstalledAppFlow.from_client_secrets_file(
            str(client_secret), [GMAIL_READONLY_SCOPE]
        )
        creds = flow.run_local_server(port=0)
        _persist(creds_file, creds)

    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    transport = GoogleApiGmailTransport(service)
    return ReadOnlyGmailClient(
        OAuthCredentials(token_ref=str(creds_file), scopes=(GMAIL_READONLY_SCOPE,)),
        transport,
    )


def _load_cached(creds_file: Path):
    """Rebuild ``Credentials`` from disk, preserving ``expiry``.

    google-auth treats ``expiry=None`` as "never expires" — both the ``valid``
    and ``expired`` properties short-circuit, so a cached token would never be
    refreshed and ``build_gmail_client`` would always fall through to the
    interactive flow once the in-memory token actually expired. Persisting and
    restoring the expiry keeps the refresh path live.
    """
    if not creds_file.exists():
        return None
    from datetime import datetime as _dt

    from google.oauth2.credentials import Credentials

    data = json.loads(creds_file.read_text())
    expiry_raw = data.get("expiry")
    expiry = _dt.fromisoformat(expiry_raw) if expiry_raw else None
    return Credentials(
        token=data.get("token"),
        refresh_token=data.get("refresh_token"),
        client_id=data.get("client_id"),
        client_secret=data.get("client_secret"),
        token_uri=data.get("token_uri", TOKEN_URI),
        scopes=[GMAIL_READONLY_SCOPE],
        expiry=expiry,
    )


def _persist(creds_file: Path, creds) -> None:
    creds_file.parent.mkdir(parents=True, exist_ok=True)
    expiry = getattr(creds, "expiry", None)
    payload = {
        "type": "authorized_user",
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "refresh_token": creds.refresh_token,
        "token": creds.token,
        "token_uri": creds.token_uri,
        # Stored as ISO-8601; google-auth uses naive UTC datetimes for expiry,
        # so isoformat() is lossless here.
        "expiry": expiry.isoformat() if expiry else None,
    }
    creds_file.write_text(json.dumps(payload, indent=2))
    os.chmod(creds_file, 0o600)
