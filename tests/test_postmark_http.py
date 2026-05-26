from __future__ import annotations

import pytest

from substack_kindle.adapters.postmark_http import make_http_post, make_send_email
from substack_kindle.postmark import PostmarkError


class _Resp:
    status_code = 200

    def __init__(self, body):
        self._b = body

    def json(self):
        return self._b

    text = ""


def test_http_post_calls_client_and_returns_response():
    calls = {}

    def fake_post(url, json, headers):
        calls["url"] = url
        calls["json"] = json
        return _Resp({"ErrorCode": 0, "MessageID": "x"})

    http_post = make_http_post(client_post=fake_post)
    resp = http_post("https://api.postmarkapp.com/email", json={"a": 1}, headers={"h": "v"})
    assert resp.status_code == 200 and calls["url"].endswith("/email")


def test_send_email_posts_textbody(monkeypatch):
    sent = {}

    def fake_post(url, json, headers):
        sent.update(json)
        return _Resp({"ErrorCode": 0, "MessageID": "x"})

    send_email = make_send_email(
        server_token="t", from_="kindle_whitelist@fong888.com", client_post=fake_post
    )
    send_email(to="timfong888@gmail.com", subject="S", body="B")
    assert (
        sent["To"] == "timfong888@gmail.com"
        and sent["TextBody"] == "B"
        and sent["From"].endswith("fong888.com")
    )


def test_send_email_raises_on_error_code():
    def fake_post(url, json, headers):
        return _Resp({"ErrorCode": 10, "Message": "bad token"})

    send_email = make_send_email(server_token="t", from_="x@fong888.com", client_post=fake_post)
    with pytest.raises(PostmarkError):
        send_email(to="a@b.com", subject="S", body="B")
