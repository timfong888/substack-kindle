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
