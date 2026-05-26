from __future__ import annotations

import json

from substack_kindle.cli import build_window, main
from substack_kindle.config_store import CustomerConfig


def test_build_window_yesterday():
    from datetime import UTC, datetime

    now = datetime(2026, 5, 25, 9, 0, tzinfo=UTC)
    start, end = build_window("yesterday", now)
    assert start.hour == 0 and end.hour == 23
    assert start.date().day == 24 and start.tzinfo is not None


def test_test_send_smoke(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("POSTMARK_SERVER_TOKEN", "tok")
    monkeypatch.setenv("WHITELIST_EMAIL", "kindle_whitelist@fong888.com")

    captured = {}

    def fake_post(url, json, headers):
        captured["url"] = url
        captured["payload"] = json

        class _Resp:
            status_code = 200

            def json(self):
                return {"ErrorCode": 0, "MessageID": "x"}

            text = ""

        return _Resp()

    import substack_kindle.cli as cli_mod

    monkeypatch.setattr(cli_mod, "_client_post", fake_post)
    rc = main(["test-send", "--to", "timfong888@kindle.com", "--store", str(tmp_path)])
    assert rc == 0
    assert captured["url"].endswith("/email")
    assert captured["payload"]["To"] == "timfong888@kindle.com"
    assert captured["payload"]["Attachments"][0]["ContentType"] == "application/epub+zip"


def test_fetch_template_writes_skeleton(tmp_path):
    out = tmp_path / "messages.json"
    rc = main(["fetch-template", "--out", str(out)])
    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data == {"messages": []}


def test_run_delivers_from_messages(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTMARK_SERVER_TOKEN", "tok")
    monkeypatch.setenv("WHITELIST_EMAIL", "kindle_whitelist@fong888.com")

    from substack_kindle.adapters.json_store import JsonConfigStore

    store_dir = tmp_path / "store"
    cfg_store = JsonConfigStore(store_dir / "config.json")
    cfg_store.put(
        CustomerConfig(
            "me",
            "timfong888@gmail.com",
            "timfong888@kindle.com",
            "Newsletters",
            "secretref://t",
            approved_sources=["a@x.com"],
        )
    )

    messages = tmp_path / "messages.json"
    messages.write_text(
        json.dumps(
            {
                "messages": [
                    {
                        "message_id": "m1",
                        "sender": "a@x.com",
                        "date_sent": "2026-05-24T12:00:00+00:00",
                        "subject": "Issue 1",
                        "html_body": "<p>hi</p>",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    def fake_post(url, json, headers):
        class _Resp:
            status_code = 200

            def json(self):
                return {"ErrorCode": 0, "MessageID": "x"}

            text = ""

        return _Resp()

    import substack_kindle.cli as cli_mod

    monkeypatch.setattr(cli_mod, "_client_post", fake_post)
    rc = main(
        [
            "run",
            "--window",
            "yesterday",
            "--messages",
            str(messages),
            "--customer",
            "me",
            "--store",
            str(store_dir),
        ]
    )
    assert rc == 0
