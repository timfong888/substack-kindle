"""End-to-end wiring test for the production CLI (SAT-330).

Proves ``cli.main`` threads env config + dates through the pipeline with the
real composition of collaborators — RSS fetch → dedup → build_epub → postmark
send — using injected fakes for the RSS fetch and the HTTP transport. No live
network calls, no Gmail/OAuth (the free-Substack path ingests via RSS feeds).
"""


import pytest

from substack_kindle.cli import main
from substack_kindle.rss_fetch import FetchedPost

_FEEDS = ["https://example.com/feed"]


def _post(guid, title, markdown, *, sender="Example Publication"):
    from datetime import UTC, datetime

    return FetchedPost(
        guid=guid,
        title=title,
        markdown=markdown,
        sender=sender,
        published=datetime(2026, 6, 24, 12, 0, 0, tzinfo=UTC),
    )


class _RecordingHttpxPost:
    """Captures the Postmark request; returns a canned success response."""

    def __init__(self):
        self.calls = []

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"MessageID": "msg-1", "ErrorCode": 0, "Message": "OK"}

        text = ""

    def __call__(self, url, *, json, headers, timeout):
        self.calls.append(
            {"url": url, "json": json, "headers": headers, "timeout": timeout}
        )
        return self._Resp()


def _env(**overrides):
    # Neutral fixture values — the real production values live in env at run
    # time. See .env.example for the documented WHITELIST_EMAIL constraint.
    base = {
        "POSTMARK_SERVER_TOKEN": "postmark-token",
        "WHITELIST_EMAIL": "digest@example.com",
        "KINDLE_EMAIL": "reader@kindle.com",
    }
    base.update(overrides)
    return base


def test_main_wires_rss_fetch_to_postmark_with_correct_metadata(monkeypatch, tmp_path):
    posts = [
        _post("guid-1", "Newer Post", "# Newer\n\nBody 1."),
        _post("guid-2", "Older Post", "# Older\n\nBody 2."),
    ]
    seen = {}

    def _fake_fetch_posts(http_get, **kwargs):
        seen["http_get"] = http_get
        seen["kwargs"] = kwargs
        return posts

    def _sentinel_http_get(url):
        raise AssertionError("http_get must not be invoked when fetch is faked")

    monkeypatch.setattr("substack_kindle.cli.fetch_posts", _fake_fetch_posts)
    recorder = _RecordingHttpxPost()

    rc = main(
        argv=["--start", "2026-06-14", "--end", "2026-06-24"],
        env=_env(),
        feeds=_FEEDS,
        http_get=_sentinel_http_get,
        http_post=recorder,
        state_path=tmp_path / "state.json",
    )

    assert rc == 0
    # The RSS seam received the injected getter, the feed URLs, and the window
    # bounds — a regression that dropped feed_urls or mangled the window would
    # otherwise still pass this test.
    assert seen["http_get"] is _sentinel_http_get
    assert seen["kwargs"]["feed_urls"] == _FEEDS
    assert seen["kwargs"]["window_start"].date().isoformat() == "2026-06-14"
    assert seen["kwargs"]["window_end"].date().isoformat() == "2026-06-24"
    # Postmark received the call: correct URL, From (whitelist), To (kindle),
    # EPUB attachment, server token in headers.
    assert len(recorder.calls) == 1
    call = recorder.calls[0]
    assert call["url"] == "https://api.postmarkapp.com/email"
    assert call["json"]["From"] == "digest@example.com"
    assert call["json"]["To"] == "reader@kindle.com"
    assert call["headers"]["X-Postmark-Server-Token"] == "postmark-token"
    assert len(call["json"]["Attachments"]) == 1
    attachment = call["json"]["Attachments"][0]
    assert attachment["ContentType"] == "application/epub+zip"
    assert attachment["Name"].endswith(".epub")


def test_main_embeds_subheader_in_produced_epub(monkeypatch, tmp_path):
    """End-to-end guard: the SAT-272 subheader must flow from cli.main all the
    way through to the EPUB bytes Postmark receives — both as ``dc:description``
    in the OPF and as the H4 line on the front-matter chapter.
    """
    import base64
    import zipfile
    from io import BytesIO

    from substack_kindle.service_version import service_subheader

    monkeypatch.setattr(
        "substack_kindle.cli.fetch_posts",
        lambda http_get, **kwargs: [_post("g", "x", "# x")],
    )
    recorder = _RecordingHttpxPost()

    rc = main(
        argv=["--start", "2026-06-14", "--end", "2026-06-24"],
        env=_env(),
        feeds=_FEEDS,
        http_post=recorder,
        state_path=tmp_path / "state.json",
    )
    assert rc == 0

    # Decode the EPUB bytes that Postmark would have sent.
    epub_bytes = base64.b64decode(recorder.calls[0]["json"]["Attachments"][0]["Content"])
    subheader = service_subheader()

    with zipfile.ZipFile(BytesIO(epub_bytes)) as zf:
        names = zf.namelist()
        opf_name = next(n for n in names if n.endswith(".opf"))
        opf = zf.read(opf_name).decode("utf-8", errors="replace")
        assert "<dc:description" in opf
        assert subheader in opf

        # Front-matter chapter present and carries the H4 subtitle.
        fm_name = next(n for n in names if n.endswith("frontmatter.xhtml"))
        fm = zf.read(fm_name).decode("utf-8", errors="replace")
        assert f"<h4>{subheader}</h4>" in fm


def test_main_refuses_to_run_when_local_parts_collide():
    from substack_kindle.whitelist_check import LocalPartCollision

    with pytest.raises(LocalPartCollision):
        main(
            argv=["--start", "2026-06-14", "--end", "2026-06-24"],
            env=_env(WHITELIST_EMAIL="reader@anything.com"),
            feeds=_FEEDS,
            http_post=lambda *a, **k: None,
        )


def test_main_rejects_inverted_date_range():
    # --start after --end is nonsense; argparse should exit with a clear error
    # rather than silently issue an empty query and "succeed".
    with pytest.raises(SystemExit):
        main(
            argv=["--start", "2026-06-24", "--end", "2026-06-14"],
            env=_env(),
            feeds=_FEEDS,
            http_post=lambda *a, **k: None,
        )


def test_main_returns_zero_with_no_send_on_empty_window(monkeypatch):
    monkeypatch.setattr(
        "substack_kindle.cli.fetch_posts",
        lambda http_get, **kwargs: [],
    )
    recorder = _RecordingHttpxPost()
    rc = main(
        argv=["--start", "2026-06-14", "--end", "2026-06-24"],
        env=_env(),
        feeds=_FEEDS,
        http_post=recorder,
    )
    # Empty job → succeeded/empty outcome → exit 0, no Postmark call.
    assert rc == 0
    assert recorder.calls == []


def test_main_rejects_missing_required_env():
    with pytest.raises(RuntimeError, match="missing"):
        main(
            argv=["--start", "2026-06-14", "--end", "2026-06-24"],
            env={"POSTMARK_SERVER_TOKEN": "tok"},  # WHITELIST_EMAIL, KINDLE_EMAIL missing
            feeds=_FEEDS,
            http_post=lambda *a, **k: None,
        )


def test_main_rejects_invalid_date_format():
    with pytest.raises(SystemExit):  # argparse exits on bad input
        main(
            argv=["--start", "not-a-date", "--end", "2026-06-24"],
            env=_env(),
            feeds=_FEEDS,
            http_post=lambda *a, **k: None,
        )


def test_main_uses_substacks_title_format_in_attachment_name(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "substack_kindle.cli.fetch_posts",
        lambda http_get, **kwargs: [_post("g", "x", "# x")],
    )
    recorder = _RecordingHttpxPost()
    main(
        argv=["--start", "2026-06-14", "--end", "2026-06-24"],
        env=_env(),
        feeds=_FEEDS,
        http_post=recorder,
        state_path=tmp_path / "state.json",
    )
    # Filename embeds the date range so it's easy to spot in Postmark dashboards.
    assert "2026-06-14" in recorder.calls[0]["json"]["Attachments"][0]["Name"]
    assert "2026-06-24" in recorder.calls[0]["json"]["Attachments"][0]["Name"]


def test_main_dedups_on_guid_across_runs(monkeypatch, tmp_path):
    """Dedup is keyed on the RSS guid: re-running with the same guid sends 0."""
    posts = [_post("https://conceptbureau.substack.com/p/warm", "Warm", "# Warm\n\nBody.")]
    monkeypatch.setattr(
        "substack_kindle.cli.fetch_posts",
        lambda http_get, **kw: posts,
    )
    state_path = tmp_path / "state.json"

    recorder1 = _RecordingHttpxPost()
    rc1 = main(
        argv=["--start", "2026-06-14", "--end", "2026-06-24"],
        env=_env(),
        feeds=_FEEDS,
        http_post=recorder1,
        state_path=state_path,
    )
    assert rc1 == 0
    assert len(recorder1.calls) == 1  # first run delivers

    recorder2 = _RecordingHttpxPost()
    rc2 = main(
        argv=["--start", "2026-06-14", "--end", "2026-06-24"],
        env=_env(),
        feeds=_FEEDS,
        http_post=recorder2,
        state_path=state_path,
    )
    assert rc2 == 0
    assert recorder2.calls == []  # second run: same guid already delivered → nothing sent


def test_main_loads_feeds_from_feeds_path_when_not_injected(monkeypatch, tmp_path):
    """With no ``feeds=`` injected, the feed URLs come from the FEEDS_PATH registry."""
    import json

    registry = tmp_path / "feeds.json"
    registry.write_text(json.dumps({"feeds": ["https://example.com/a/feed",
                                              "https://example.org/b/feed"]}))
    seen = {}

    def _fake_fetch_posts(http_get, **kwargs):
        seen["feed_urls"] = kwargs["feed_urls"]
        return []  # empty digest → no send, keeps the test network-free

    monkeypatch.setattr("substack_kindle.cli.fetch_posts", _fake_fetch_posts)

    rc = main(
        argv=["--start", "2026-06-14", "--end", "2026-06-24"],
        env=_env(FEEDS_PATH=str(registry)),
        http_post=lambda *a, **k: None,
        state_path=tmp_path / "state.json",
    )
    assert rc == 0
    assert seen["feed_urls"] == ["https://example.com/a/feed", "https://example.org/b/feed"]
