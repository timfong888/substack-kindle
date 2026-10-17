"""Tests for sending the EPUB to Kindle via the Resend REST API (Req §Provider).

Acceptance:
- Send uses Resend's ``POST /emails`` with an ``attachments`` entry (base64
  ``content``, ``content_type`` application/epub+zip).
- FROM is the verified whitelist_email sender on our Resend-verified domain.
- An ``Idempotency-Key`` can be supplied so a retried run cannot double-send.
- On a Resend API error the call fails loudly (no silent success).
"""

import base64

import pytest

from substack_kindle.resend import RESEND_EMAILS_URL, ResendError, send_epub

EPUB = b"PK\x03\x04 epub bytes"


class FakeResponse:
    def __init__(self, status_code, payload=None, *, text="", json_raises=False):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self._json_raises = json_raises

    def json(self):
        if self._json_raises:
            raise ValueError("not JSON")
        return self._payload


class FakeHttp:
    def __init__(self, response):
        self._response = response
        self.calls = []

    def __call__(self, url, *, json, headers):
        self.calls.append((url, json, headers))
        return self._response


def _ok():
    return FakeResponse(200, {"id": "49a3999c-0ce1-4ea6-ab68-afcd6dc2e794"})


def _send(http, **overrides):
    kwargs = dict(
        epub_bytes=EPUB,
        to="reader@kindle.com",
        from_="whitelist@system.example",
        filename="job.epub",
        api_key="re_TEST_KEY",
        http_post=http,
    )
    kwargs.update(overrides)
    return send_epub(**kwargs)


def test_posts_to_resend_emails_endpoint_with_bearer_auth():
    http = FakeHttp(_ok())
    _send(http)
    url, _json, headers = http.calls[0]
    assert url == RESEND_EMAILS_URL == "https://api.resend.com/emails"
    assert headers["Authorization"] == "Bearer re_TEST_KEY"
    assert headers["Content-Type"] == "application/json"


def test_from_is_the_whitelist_sender_and_to_is_a_list():
    http = FakeHttp(_ok())
    _send(http)
    _url, payload, _headers = http.calls[0]
    assert payload["from"] == "whitelist@system.example"
    # Resend takes recipients as an array of strings.
    assert payload["to"] == ["reader@kindle.com"]


def test_attachment_is_base64_epub_with_correct_content_type():
    http = FakeHttp(_ok())
    _send(http)
    _url, payload, _headers = http.calls[0]
    [attachment] = payload["attachments"]
    assert attachment["filename"] == "job.epub"
    assert attachment["content_type"] == "application/epub+zip"
    assert base64.b64decode(attachment["content"]) == EPUB


def test_payload_includes_text_body_required_by_resend():
    http = FakeHttp(_ok())
    _send(http)
    _url, payload, _headers = http.calls[0]
    # Resend requires html or text; Send-to-Kindle only reads the attachment.
    assert payload.get("text") or payload.get("html")


def test_idempotency_key_header_is_sent_when_given():
    http = FakeHttp(_ok())
    _send(http, idempotency_key="digest-2026-06-14-2026-06-24")
    _url, _payload, headers = http.calls[0]
    assert headers["Idempotency-Key"] == "digest-2026-06-14-2026-06-24"


def test_no_idempotency_header_by_default():
    http = FakeHttp(_ok())
    _send(http)
    _url, _payload, headers = http.calls[0]
    assert "Idempotency-Key" not in headers


def test_returns_message_id_on_success():
    http = FakeHttp(_ok())
    result = _send(http)
    assert result.message_id == "49a3999c-0ce1-4ea6-ab68-afcd6dc2e794"
    assert result.to == "reader@kindle.com"


def test_non_2xx_response_raises_with_resend_name_and_message():
    http = FakeHttp(
        FakeResponse(
            422,
            {"statusCode": 422, "name": "validation_error", "message": "Invalid `to` field"},
        )
    )
    with pytest.raises(ResendError) as exc:
        _send(http)
    assert "422" in str(exc.value)
    # The stable error code lets a caller tell retryable from permanent failures.
    assert "validation_error" in str(exc.value)
    assert "Invalid `to` field" in str(exc.value)


def test_non_json_5xx_body_raises_resend_error_not_decode_error():
    http = FakeHttp(FakeResponse(503, json_raises=True, text="<html>gateway down</html>"))
    with pytest.raises(ResendError) as exc:
        _send(http)
    assert "503" in str(exc.value)


def test_2xx_without_id_still_raises():
    # A 2xx that carries no email id is not proof of acceptance.
    http = FakeHttp(FakeResponse(200, {"message": "queued?"}))
    with pytest.raises(ResendError):
        _send(http)


def test_transport_exception_propagates_as_failure():
    def boom(url, *, json, headers):
        raise ConnectionError("network down")

    with pytest.raises(ConnectionError):
        _send(boom)


def test_no_mcp_send_email_reference_in_module():
    # Resend publishes an MCP server; the EPUB send stays on the REST endpoint
    # through the injected transport (docs/requirements.md, Transactional Email).
    import substack_kindle.resend as mod

    with open(mod.__file__, encoding="utf-8") as fh:
        source = fh.read().lower()
    assert "sendemail" not in source
    assert "mcp" not in source


def test_api_key_never_appears_in_error_text():
    http = FakeHttp(FakeResponse(401, {"message": "Missing API key"}))
    with pytest.raises(ResendError) as exc:
        _send(http)
    assert "re_TEST_KEY" not in str(exc.value)
