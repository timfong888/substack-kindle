"""End-to-end wiring test for the production CLI (SAT-269).

Proves ``cli.main`` threads env config + dates through the pipeline with the
real composition of collaborators — fetch → dedup → build_epub → postmark
send — using injected fakes for the Gmail client and the HTTP transport.
No live network calls, no OAuth.
"""


import pytest

from substack_kindle.cli import main


class _StubGmailClient:
    def __init__(self, sections_per_call):
        self._sections_per_call = sections_per_call
        self.calls = 0

    def list_message_ids(self, query=None):
        return []  # unused — the fetch is short-circuited by the patched fetcher

    def get_message(self, message_id):
        raise AssertionError("should not be called when fetch is patched")


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
    base = {
        "POSTMARK_SERVER_TOKEN": "tok",
        "WHITELIST_EMAIL": "kindle_whitelist@fong888.com",
        "KINDLE_EMAIL": "timfong888@kindle.com",
    }
    base.update(overrides)
    return base


def test_main_wires_fetch_to_postmark_with_correct_metadata(monkeypatch):
    from substack_kindle.job_epub import JobSection

    sections = [
        JobSection(title="Lenny issue 12", markdown="# Lenny\n\nBody 1."),
        JobSection(title="TD weekly", markdown="# TD\n\nBody 2."),
    ]
    monkeypatch.setattr(
        "substack_kindle.cli.fetch_newsletters",
        lambda client, **kwargs: sections,
    )
    recorder = _RecordingHttpxPost()

    rc = main(
        argv=["--start", "2026-05-03", "--end", "2026-05-09"],
        env=_env(),
        build_client=lambda env: _StubGmailClient([]),
        approved_sources=["lenny@substack.com", "thetokendispatch@substack.com"],
        http_post=recorder,
    )

    assert rc == 0
    # Postmark received the call: correct URL, From (whitelist), To (kindle),
    # EPUB attachment, server token in headers.
    assert len(recorder.calls) == 1
    call = recorder.calls[0]
    assert call["url"] == "https://api.postmarkapp.com/email"
    assert call["json"]["From"] == "kindle_whitelist@fong888.com"
    assert call["json"]["To"] == "timfong888@kindle.com"
    assert call["headers"]["X-Postmark-Server-Token"] == "tok"
    assert len(call["json"]["Attachments"]) == 1
    attachment = call["json"]["Attachments"][0]
    assert attachment["ContentType"] == "application/epub+zip"
    assert attachment["Name"].endswith(".epub")


def test_main_refuses_to_run_when_local_parts_collide():
    from substack_kindle.whitelist_check import LocalPartCollision

    with pytest.raises(LocalPartCollision):
        main(
            argv=["--start", "2026-05-03", "--end", "2026-05-09"],
            env=_env(WHITELIST_EMAIL="timfong888@anything.com"),
            build_client=lambda env: _StubGmailClient([]),
            approved_sources=[],
            http_post=lambda *a, **k: None,
        )


def test_main_returns_zero_with_no_send_on_empty_window(monkeypatch):
    monkeypatch.setattr(
        "substack_kindle.cli.fetch_newsletters",
        lambda client, **kwargs: [],
    )
    recorder = _RecordingHttpxPost()
    rc = main(
        argv=["--start", "2026-05-03", "--end", "2026-05-09"],
        env=_env(),
        build_client=lambda env: _StubGmailClient([]),
        approved_sources=["lenny@substack.com"],
        http_post=recorder,
    )
    # Empty job → succeeded/empty outcome → exit 0, no Postmark call.
    assert rc == 0
    assert recorder.calls == []


def test_main_rejects_missing_required_env():
    with pytest.raises(RuntimeError, match="missing"):
        main(
            argv=["--start", "2026-05-03", "--end", "2026-05-09"],
            env={"POSTMARK_SERVER_TOKEN": "tok"},  # WHITELIST_EMAIL, KINDLE_EMAIL missing
            build_client=lambda env: _StubGmailClient([]),
            approved_sources=[],
            http_post=lambda *a, **k: None,
        )


def test_main_rejects_invalid_date_format():
    with pytest.raises(SystemExit):  # argparse exits on bad input
        main(
            argv=["--start", "not-a-date", "--end", "2026-05-09"],
            env=_env(),
            build_client=lambda env: _StubGmailClient([]),
            approved_sources=[],
            http_post=lambda *a, **k: None,
        )


def test_main_uses_substacks_title_format_in_attachment_name(monkeypatch):
    from substack_kindle.job_epub import JobSection

    monkeypatch.setattr(
        "substack_kindle.cli.fetch_newsletters",
        lambda client, **kwargs: [JobSection(title="x", markdown="# x")],
    )
    recorder = _RecordingHttpxPost()
    main(
        argv=["--start", "2026-05-03", "--end", "2026-05-09"],
        env=_env(),
        build_client=lambda env: _StubGmailClient([]),
        approved_sources=["lenny@substack.com"],
        http_post=recorder,
    )
    # Filename embeds the date range so it's easy to spot in Postmark dashboards.
    assert "2026-05-03" in recorder.calls[0]["json"]["Attachments"][0]["Name"]
    assert "2026-05-09" in recorder.calls[0]["json"]["Attachments"][0]["Name"]
