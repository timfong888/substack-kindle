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
    # Neutral fixture values — the real production values live in env at run
    # time. See .env.example for the documented WHITELIST_EMAIL constraint.
    base = {
        "POSTMARK_SERVER_TOKEN": "postmark-token",
        "WHITELIST_EMAIL": "digest@example.com",
        "KINDLE_EMAIL": "reader@kindle.com",
    }
    base.update(overrides)
    return base


def test_main_wires_fetch_to_postmark_with_correct_metadata(monkeypatch, tmp_path):
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
        state_path=tmp_path / "state.json",
    )

    assert rc == 0
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
    in the OPF and as the H4 line on the front-matter chapter. Without this the
    wiring could silently regress (e.g. a missing kwarg) and the unit tests on
    the title helper and the builder would still pass.
    """
    import base64
    import zipfile
    from io import BytesIO

    from substack_kindle.job_epub import JobSection
    from substack_kindle.service_version import service_subheader

    monkeypatch.setattr(
        "substack_kindle.cli.fetch_newsletters",
        lambda client, **kwargs: [JobSection(title="x", markdown="# x")],
    )
    recorder = _RecordingHttpxPost()

    rc = main(
        argv=["--start", "2026-05-03", "--end", "2026-05-09"],
        env=_env(),
        build_client=lambda env: _StubGmailClient([]),
        approved_sources=["lenny@substack.com"],
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
            argv=["--start", "2026-05-03", "--end", "2026-05-09"],
            env=_env(WHITELIST_EMAIL="reader@anything.com"),
            build_client=lambda env: _StubGmailClient([]),
            approved_sources=[],
            http_post=lambda *a, **k: None,
        )


def test_main_rejects_inverted_date_range():
    # --start after --end is nonsense; argparse should exit with a clear error
    # rather than silently issue an empty Gmail query and "succeed".
    with pytest.raises(SystemExit):
        main(
            argv=["--start", "2026-05-09", "--end", "2026-05-03"],
            env=_env(),
            build_client=lambda env: _StubGmailClient([]),
            approved_sources=["lenny@substack.com"],
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


def test_main_uses_substacks_title_format_in_attachment_name(monkeypatch, tmp_path):
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
        state_path=tmp_path / "state.json",
    )
    # Filename embeds the date range so it's easy to spot in Postmark dashboards.
    assert "2026-05-03" in recorder.calls[0]["json"]["Attachments"][0]["Name"]
    assert "2026-05-09" in recorder.calls[0]["json"]["Attachments"][0]["Name"]


def test_main_skips_already_delivered_on_second_run(monkeypatch, tmp_path):
    """Running the same window twice must deliver 0 newsletters on the second run."""
    from substack_kindle.job_epub import JobSection

    sections = [JobSection(title="Lenny #1", markdown="# Lenny\n\nBody.")]
    monkeypatch.setattr(
        "substack_kindle.cli.fetch_newsletters",
        lambda client, **kw: sections,
    )
    state_path = tmp_path / "state.json"

    recorder1 = _RecordingHttpxPost()
    rc1 = main(
        argv=["--start", "2026-05-03", "--end", "2026-05-09"],
        env=_env(),
        build_client=lambda env: _StubGmailClient([]),
        approved_sources=["lenny@substack.com"],
        http_post=recorder1,
        state_path=state_path,
    )
    assert rc1 == 0
    assert len(recorder1.calls) == 1  # first run delivers

    recorder2 = _RecordingHttpxPost()
    rc2 = main(
        argv=["--start", "2026-05-03", "--end", "2026-05-09"],
        env=_env(),
        build_client=lambda env: _StubGmailClient([]),
        approved_sources=["lenny@substack.com"],
        http_post=recorder2,
        state_path=state_path,
    )
    assert rc2 == 0
    assert recorder2.calls == []  # second run: already delivered → nothing sent
