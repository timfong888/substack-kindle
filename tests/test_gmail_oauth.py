"""Tests for read-only Gmail OAuth connection (SAT-241 / #5, Reqs 1, 9).

Acceptance:
- OAuth uses read-only scope; the granted scope is asserted here.
- The tool never calls any mutating Gmail API (no label add/remove, archive, delete).
- Token is per-customer, supplied at runtime, never committed.
"""

import pytest

import substack_kindle.gmail_oauth as gmail_oauth
from substack_kindle.gmail_oauth import (
    GMAIL_READONLY_SCOPE,
    OAuthCredentials,
    ReadOnlyGmailClient,
    ScopeError,
    requested_scopes,
)


class SpyTransport:
    """Records every request so the test can assert the HTTP method and token used."""

    def __init__(self, messages=None, message=None):
        self.calls = []
        self._messages = messages or []
        self._message = message or {}

    def request(self, method, path, *, token_ref, params=None):
        self.calls.append((method, path, token_ref))
        if path.endswith("/messages"):
            return {"messages": self._messages}
        return self._message


def _creds(scopes=(GMAIL_READONLY_SCOPE,), token_ref="secretref://gmail/cust-1"):
    return OAuthCredentials(token_ref=token_ref, scopes=tuple(scopes))


def test_requested_scope_is_exactly_read_only():
    assert requested_scopes() == [GMAIL_READONLY_SCOPE]
    assert GMAIL_READONLY_SCOPE == "https://www.googleapis.com/auth/gmail.readonly"


def test_client_accepts_read_only_credentials():
    client = ReadOnlyGmailClient(_creds(), SpyTransport())
    assert client.granted_scopes == (GMAIL_READONLY_SCOPE,)


@pytest.mark.parametrize(
    "scope",
    [
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/gmail.compose",
        "https://www.googleapis.com/auth/gmail.labels",
        "https://mail.google.com/",
    ],
)
def test_client_rejects_non_read_only_scopes(scope):
    with pytest.raises(ScopeError):
        ReadOnlyGmailClient(_creds(scopes=(GMAIL_READONLY_SCOPE, scope)), SpyTransport())


def test_client_rejects_missing_read_only_scope():
    with pytest.raises(ScopeError):
        ReadOnlyGmailClient(_creds(scopes=()), SpyTransport())


def test_reads_issue_only_get_requests():
    transport = SpyTransport(messages=[{"id": "m1"}, {"id": "m2"}], message={"id": "m1"})
    client = ReadOnlyGmailClient(_creds(), transport)
    client.list_message_ids(query="label:Newsletters")
    client.get_message("m1")
    assert transport.calls  # something happened
    assert {method for method, _path, _tok in transport.calls} == {"GET"}


def test_list_and_get_return_expected_shapes():
    transport = SpyTransport(messages=[{"id": "m1"}, {"id": "m2"}], message={"id": "m1", "x": 1})
    client = ReadOnlyGmailClient(_creds(), transport)
    assert client.list_message_ids() == ["m1", "m2"]
    assert client.get_message("m1") == {"id": "m1", "x": 1}


def test_client_exposes_no_mutating_methods():
    client = ReadOnlyGmailClient(_creds(), SpyTransport())
    for forbidden in ("add_label", "remove_label", "modify", "archive", "trash", "delete", "send"):
        assert not hasattr(client, forbidden)


def test_token_is_per_customer_and_passed_at_runtime():
    t1 = SpyTransport(messages=[{"id": "a"}])
    t2 = SpyTransport(messages=[{"id": "b"}])
    ReadOnlyGmailClient(_creds(token_ref="secretref://gmail/alice"), t1).list_message_ids()
    ReadOnlyGmailClient(_creds(token_ref="secretref://gmail/bob"), t2).list_message_ids()
    assert all(tok == "secretref://gmail/alice" for _m, _p, tok in t1.calls)
    assert all(tok == "secretref://gmail/bob" for _m, _p, tok in t2.calls)


def test_credentials_repr_redacts_token_ref():
    creds = _creds(token_ref="secretref://super/secret")
    assert "super/secret" not in repr(creds)
    assert "redacted" in repr(creds).lower()


def test_module_has_no_hardcoded_credentials():
    with open(gmail_oauth.__file__) as fh:
        source = fh.read()
    # No real OAuth token/client-secret literals committed.
    assert "ya29." not in source
    assert "client_secret" not in source.lower()
