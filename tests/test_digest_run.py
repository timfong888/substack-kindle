"""Tests for the per-user RSS digest runner.

The runner asks Convex which users are due a digest, reads each user's public
feeds, drops posts already delivered to them, builds one EPUB, sends it from the
user's own sender address, and records post → revision → delivery in Convex.
Every collaborator is injected; fixtures use example.com feeds.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from substack_kindle.digest_run import (
    ConvexHttp,
    UnsafeUrlError,
    check_public_https_url,
    run_digests,
    sign,
)

SECRET = "pipeline-test-secret-not-real"
NOW = datetime(2026, 11, 17, 6, 0, tzinfo=UTC)

FEED_A = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>Example A</title>
    <item>
      <title>Fresh post</title>
      <link>https://a.example.com/p/fresh</link>
      <guid>https://a.example.com/p/fresh</guid>
      <pubDate>Mon, 16 Nov 2026 12:00:00 GMT</pubDate>
      <content:encoded><![CDATA[<p>Fresh body.</p>]]></content:encoded>
    </item>
    <item>
      <title>Already sent</title>
      <guid>https://a.example.com/p/old</guid>
      <pubDate>Sun, 15 Nov 2026 12:00:00 GMT</pubDate>
      <content:encoded><![CDATA[<p>Old body.</p>]]></content:encoded>
    </item>
    <item>
      <title>Too old</title>
      <guid>https://a.example.com/p/ancient</guid>
      <pubDate>Mon, 02 Nov 2026 12:00:00 GMT</pubDate>
      <content:encoded><![CDATA[<p>Ancient.</p>]]></content:encoded>
    </item>
  </channel>
</rss>"""


def _target(**overrides):
    base = {
        "userId": "user1",
        "kindleEmail": "reader@kindle.com",
        "sender": "kindle+abcdefghijklmnopqrstuvwxyz@mail.example.com",
        "feedUrls": ["https://a.example.com/feed"],
        "deliveredKeys": ["https://a.example.com/p/old"],
        "lastDigestAt": None,
    }
    base.update(overrides)
    return base


class FakeConvex:
    def __init__(self, targets):
        self.targets = targets
        self.queries: list[tuple[str, dict]] = []
        self.mutations: list[tuple[str, dict]] = []

    def query(self, path, args):
        self.queries.append((path, args))
        return self.targets

    def mutation(self, path, args):
        self.mutations.append((path, args))
        return None


class FakeSender:
    def __init__(self, fail=False):
        self.calls: list[dict] = []
        self.fail = fail

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("Resend returned HTTP 500")
        return "em_123"


def _run(convex, send, feeds=None):
    feeds = feeds or {"https://a.example.com/feed": FEED_A}
    return run_digests(
        convex=convex,
        secret=SECRET,
        http_get=lambda url: feeds[url],
        send=send,
        now=NOW,
    )


# ---------------------------------------------------------------- signing


def test_sign_matches_the_shared_cross_language_vector():
    # Same vector as web/convex/digest.test.ts.
    assert (
        sign(SECRET, "digest-targets:1794900000000")
        == "06551f9c89e38106dcbf0eb1af9cb5ff8b1b5d7b75db5f842fedf98e1f998595"
    )


def test_targets_query_is_signed_with_issued_at():
    convex = FakeConvex([])
    _run(convex, FakeSender())
    path, args = convex.queries[0]
    assert path == "digest:targets"
    issued = int(args["issuedAt"])
    assert args["signature"] == sign(SECRET, f"digest-targets:{issued}")
    assert issued == int(NOW.timestamp() * 1000)


# ---------------------------------------------------------------- digest


def test_sends_only_new_posts_inside_the_window_from_the_users_sender():
    convex = FakeConvex([_target()])
    send = FakeSender()
    summary = _run(convex, send)

    assert summary.sent == 1 and summary.empty == 0 and summary.failed == 0
    call = send.calls[0]
    assert call["to"] == "reader@kindle.com"
    assert call["from_"] == "kindle+abcdefghijklmnopqrstuvwxyz@mail.example.com"
    assert call["epub_bytes"][:2] == b"PK"  # an EPUB (zip)
    assert call["idempotency_key"].startswith("digest-user1-")

    path, args = convex.mutations[0]
    assert path == "digest:recordDigest"
    assert [p["key"] for p in args["posts"]] == ["https://a.example.com/p/fresh"]
    post = args["posts"][0]
    assert post["publication"] == "Example A"
    assert post["url"] == "https://a.example.com/p/fresh"
    assert len(post["hash"]) == 64
    issued = int(args["issuedAt"])
    expected = sign(SECRET, f"record-digest:user1:{args['digestKey']}:{issued}")
    assert args["signature"] == expected
    assert args["digestKey"] == call["idempotency_key"]


def test_nothing_new_marks_the_user_without_sending():
    convex = FakeConvex(
        [_target(deliveredKeys=["https://a.example.com/p/fresh", "https://a.example.com/p/old"])]
    )
    send = FakeSender()
    summary = _run(convex, send)
    assert summary.empty == 1 and send.calls == []
    path, args = convex.mutations[0]
    assert path == "digest:markNoDigest"
    assert args["signature"] == sign(SECRET, f"no-digest:user1:{int(args['issuedAt'])}")


def test_one_users_failure_does_not_stop_the_others_and_is_not_recorded():
    convex = FakeConvex([_target(), _target(userId="user2")])
    send = FakeSender(fail=True)
    summary = _run(convex, send)
    assert summary.failed == 2 and summary.sent == 0
    assert convex.mutations == []  # a failed send must stay undelivered for the next run


def test_digest_key_changes_when_the_post_set_changes():
    send = FakeSender()
    _run(FakeConvex([_target()]), send)
    _run(FakeConvex([_target(deliveredKeys=[])]), send)
    assert send.calls[0]["idempotency_key"] != send.calls[1]["idempotency_key"]


def test_refuses_a_sender_whose_local_part_matches_the_kindle_address():
    convex = FakeConvex([_target(sender="reader@mail.example.com")])
    summary = _run(convex, FakeSender())
    assert summary.failed == 1


def test_unreadable_feed_is_skipped_not_fatal():
    convex = FakeConvex(
        [_target(feedUrls=["https://a.example.com/feed", "https://b.example.com/feed"])]
    )
    send = FakeSender()
    feeds = {
        "https://a.example.com/feed": FEED_A,
        "https://b.example.com/feed": b"<html>not a feed",
    }
    summary = _run(convex, send, feeds)
    assert summary.sent == 1


# ---------------------------------------------------------------- URL safety


@pytest.mark.parametrize(
    "url",
    [
        "http://a.example.com/feed",
        "https://localhost/feed",
        "https://127.0.0.1/feed",
        "https://10.1.2.3/feed",
        "https://169.254.169.254/latest",
        "https://[::1]/feed",
        "file:///etc/passwd",
    ],
)
def test_unsafe_urls_are_refused(url):
    with pytest.raises(UnsafeUrlError):
        check_public_https_url(url, resolve=lambda host: ["93.184.216.34"])


def test_public_https_url_passes():
    check_public_https_url(
        "https://blog.bytebytego.com/feed", resolve=lambda host: ["93.184.216.34"]
    )


@pytest.mark.parametrize("address", ["169.254.169.254", "10.0.0.7", "127.0.0.1", "::1", "fd00::1"])
def test_a_public_name_that_resolves_to_a_private_address_is_refused(address):
    with pytest.raises(UnsafeUrlError, match="non-public"):
        check_public_https_url(
            "https://metadata.example.com/feed", resolve=lambda host: ["93.184.216.34", address]
        )


def test_an_unresolvable_host_is_refused():
    def fail(host):
        raise OSError("Name or service not known")

    with pytest.raises(UnsafeUrlError, match="resolve"):
        check_public_https_url("https://nope.example.com/feed", resolve=fail)


# ---------------------------------------------------------------- Convex HTTP


class _Resp:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


def test_convex_http_posts_to_the_query_endpoint_and_unwraps_the_value():
    seen = {}

    def post(url, json, timeout):
        seen.update(url=url, json=json)
        return _Resp(200, {"status": "success", "value": [1, 2]})

    client = ConvexHttp("https://happy-otter-123.convex.cloud/", post)
    assert client.query("digest:targets", {"a": 1}) == [1, 2]
    assert seen["url"] == "https://happy-otter-123.convex.cloud/api/query"
    assert seen["json"] == {"path": "digest:targets", "args": {"a": 1}, "format": "json"}


def test_convex_http_raises_on_an_error_status():
    client = ConvexHttp(
        "https://x.convex.cloud",
        lambda url, json, timeout: _Resp(200, {"status": "error", "errorMessage": "Unauthorized"}),
    )
    with pytest.raises(RuntimeError, match="Unauthorized"):
        client.mutation("digest:recordDigest", {})
