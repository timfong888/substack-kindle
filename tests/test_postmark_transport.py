"""Tests for the httpx-backed Postmark transport (SAT-269).

The transport is a thin shim that adapts ``httpx.post`` (or any compatible
callable) to the ``http_post`` contract that ``postmark.send_epub`` expects:
a callable returning an object with ``status_code``, ``json()`` and ``text``.
The shim does not interpret responses — that is the sender's job — so the
adapter can be swapped (urllib, requests, a fake) without touching postmark.py.
"""

from substack_kindle.postmark_transport import post


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text

    def json(self):
        return self._json


class _RecordingHttpxPost:
    """Captures the call args; returns a canned response."""

    def __init__(self, response):
        self.calls = []
        self._response = response

    def __call__(self, url, *, json, headers, timeout):
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return self._response


def test_post_forwards_url_json_and_headers_to_underlying_caller():
    fake = _RecordingHttpxPost(_FakeResponse(status_code=200, json_data={"MessageID": "m-1"}))
    response = post(
        "https://api.postmarkapp.com/email",
        json={"From": "tim@fong888.com"},
        headers={"X-Postmark-Server-Token": "tok"},
        http_post=fake,
    )
    assert response.status_code == 200
    assert response.json() == {"MessageID": "m-1"}
    assert fake.calls == [
        {
            "url": "https://api.postmarkapp.com/email",
            "json": {"From": "tim@fong888.com"},
            "headers": {"X-Postmark-Server-Token": "tok"},
            "timeout": 30.0,
        }
    ]


def test_post_uses_default_timeout_when_caller_does_not_override():
    fake = _RecordingHttpxPost(_FakeResponse())
    post("https://api.postmarkapp.com/email", json={}, headers={}, http_post=fake)
    assert fake.calls[0]["timeout"] == 30.0


def test_post_respects_explicit_timeout():
    fake = _RecordingHttpxPost(_FakeResponse())
    post(
        "https://api.postmarkapp.com/email",
        json={},
        headers={},
        timeout=5.0,
        http_post=fake,
    )
    assert fake.calls[0]["timeout"] == 5.0
