"""Tests for the GoogleApiGmailTransport path-routing (SAT-269).

Exercises the dispatch from the protocol's ``(method, path, params)`` shape
onto the googleapiclient service methods, with a fake service standing in
for the real Gmail client. ``build_gmail_client`` itself is not tested here
— it does the OAuth dance and requires a live consent screen.
"""

import pytest

from substack_kindle.gmail_api import GoogleApiGmailTransport


class _FakeRequest:
    def __init__(self, response):
        self._response = response

    def execute(self):
        return self._response


class _FakeMessages:
    def __init__(self):
        self.list_calls = []
        self.get_calls = []

    def list(self, *, userId, q=None, pageToken=None):
        self.list_calls.append({"userId": userId, "q": q, "pageToken": pageToken})
        return _FakeRequest({"messages": [{"id": "abc"}]})

    def get(self, *, userId, id, format=None):
        self.get_calls.append({"userId": userId, "id": id, "format": format})
        return _FakeRequest({"id": id, "payload": {}})


class _FakeUsers:
    def __init__(self, messages):
        self._messages = messages

    def messages(self):
        return self._messages


class _FakeService:
    def __init__(self):
        self.messages_obj = _FakeMessages()

    def users(self):
        return _FakeUsers(self.messages_obj)


def test_list_path_dispatches_to_messages_list_with_query():
    svc = _FakeService()
    transport = GoogleApiGmailTransport(svc)
    out = transport.request(
        "GET", "/users/me/messages",
        token_ref="unused", params={"q": "from:lenny@substack.com", "pageToken": "p2"},
    )
    assert out == {"messages": [{"id": "abc"}]}
    assert svc.messages_obj.list_calls == [
        {"userId": "me", "q": "from:lenny@substack.com", "pageToken": "p2"}
    ]


def test_get_path_dispatches_to_messages_get_with_full_format():
    svc = _FakeService()
    transport = GoogleApiGmailTransport(svc)
    out = transport.request("GET", "/users/me/messages/abc123", token_ref="unused")
    assert out == {"id": "abc123", "payload": {}}
    assert svc.messages_obj.get_calls == [
        {"userId": "me", "id": "abc123", "format": "full"}
    ]


def test_non_get_method_is_rejected():
    svc = _FakeService()
    transport = GoogleApiGmailTransport(svc)
    with pytest.raises(NotImplementedError, match="GET only"):
        transport.request("POST", "/users/me/messages", token_ref="unused")


def test_unknown_path_is_rejected():
    svc = _FakeService()
    transport = GoogleApiGmailTransport(svc)
    with pytest.raises(NotImplementedError, match="unsupported"):
        transport.request("GET", "/users/me/labels", token_ref="unused")


def test_nested_message_path_is_rejected():
    # /users/me/messages/abc/attachments/xyz must not silently route to get().
    svc = _FakeService()
    transport = GoogleApiGmailTransport(svc)
    with pytest.raises(NotImplementedError, match="unsupported"):
        transport.request(
            "GET", "/users/me/messages/abc/attachments/xyz", token_ref="unused"
        )


# --- build_gmail_client / OAuth bundle behaviour -----------------------------


def test_build_gmail_client_raises_when_client_secret_missing(tmp_path):
    # Bundle dir exists but has no client_secret.json AND no cached credentials
    # → the builder must fail loudly rather than start an OAuth flow against a
    # missing config.
    from substack_kindle.gmail_api import build_gmail_client

    with pytest.raises(FileNotFoundError, match="client secret missing"):
        build_gmail_client(tmp_path)


def test_persist_and_load_round_trip_preserves_expiry(tmp_path):
    """Persisted credentials must restore expiry so google-auth's refresh fires.

    google-auth treats ``expiry=None`` as "never expires" — both ``valid`` and
    ``expired`` short-circuit, which would bypass the refresh path forever
    once the in-memory token had actually expired.
    """
    from datetime import UTC, datetime, timedelta

    from substack_kindle.gmail_api import _load_cached, _persist

    creds_file = tmp_path / "credentials.json"
    fixed_expiry = datetime(2026, 6, 1, 12, 0, 0)

    class _StubCreds:
        token = "t"
        refresh_token = "r"
        client_id = "cid"
        client_secret = "csec"
        token_uri = "https://oauth2.googleapis.com/token"
        expiry = fixed_expiry

    _persist(creds_file, _StubCreds())
    loaded = _load_cached(creds_file)
    assert loaded is not None
    assert loaded.expiry == fixed_expiry  # round-trips losslessly
    # An expired token (relative to now) should be flagged as expired so the
    # refresh path can fire.
    # google-auth uses naive UTC datetimes for expiry; mirror that here.
    past = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
    _StubCreds.expiry = past
    _persist(creds_file, _StubCreds())
    reloaded = _load_cached(creds_file)
    assert reloaded.expired is True


def test_load_cached_returns_none_when_file_missing(tmp_path):
    from substack_kindle.gmail_api import _load_cached

    assert _load_cached(tmp_path / "nope.json") is None
