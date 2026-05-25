"""Tests for sending the EPUB to Kindle via Postmark REST (SAT-250 / #14).

Acceptance:
- Send uses the Postmark /email REST API with an Attachments entry (base64,
  ContentType application/epub+zip) — not the MCP sendEmail.
- FROM is the verified whitelist_email sender signature.
- On a Postmark API error the call fails loudly (no silent success).
"""

import base64

import pytest

from substack_kindle.postmark import POSTMARK_EMAIL_URL, PostmarkError, send_epub

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


def _send(http, **overrides):
    kwargs = dict(
        epub_bytes=EPUB,
        to="reader@kindle.com",
        from_="whitelist@system.example",
        filename="job.epub",
        server_token="POSTMARK-TOKEN",
        http_post=http,
    )
    kwargs.update(overrides)
    return send_epub(**kwargs)


def test_posts_to_postmark_rest_email_endpoint():
    http = FakeHttp(FakeResponse(200, {"MessageID": "abc", "ErrorCode": 0}))
    _send(http)
    url, _json, headers = http.calls[0]
    assert url == POSTMARK_EMAIL_URL == "https://api.postmarkapp.com/email"
    assert headers["X-Postmark-Server-Token"] == "POSTMARK-TOKEN"
    assert headers["Accept"] == "application/json"


def test_from_is_the_whitelist_sender():
    http = FakeHttp(FakeResponse(200, {"MessageID": "abc", "ErrorCode": 0}))
    _send(http)
    _url, payload, _headers = http.calls[0]
    assert payload["From"] == "whitelist@system.example"
    assert payload["To"] == "reader@kindle.com"


def test_attachment_is_base64_epub_with_correct_content_type():
    http = FakeHttp(FakeResponse(200, {"MessageID": "abc", "ErrorCode": 0}))
    _send(http)
    _url, payload, _headers = http.calls[0]
    assert "Attachments" in payload  # REST attachment path, not MCP sendEmail
    [attachment] = payload["Attachments"]
    assert attachment["Name"] == "job.epub"
    assert attachment["ContentType"] == "application/epub+zip"
    assert base64.b64decode(attachment["Content"]) == EPUB


def test_payload_includes_text_body_required_by_postmark():
    http = FakeHttp(FakeResponse(200, {"MessageID": "abc", "ErrorCode": 0}))
    _send(http)
    _url, payload, _headers = http.calls[0]
    # Postmark rejects a message with neither TextBody nor HtmlBody.
    assert payload.get("TextBody") or payload.get("HtmlBody")


def test_non_json_5xx_body_raises_postmark_error_not_decode_error():
    http = FakeHttp(FakeResponse(503, json_raises=True, text="<html>gateway down</html>"))
    with pytest.raises(PostmarkError) as exc:
        _send(http)
    assert "503" in str(exc.value)


def test_uses_configured_message_stream():
    http = FakeHttp(FakeResponse(200, {"MessageID": "abc", "ErrorCode": 0}))
    _send(http, message_stream="broadcast")
    _url, payload, _headers = http.calls[0]
    assert payload["MessageStream"] == "broadcast"


def test_returns_message_id_on_success():
    http = FakeHttp(FakeResponse(200, {"MessageID": "msg-123", "ErrorCode": 0}))
    result = _send(http)
    assert result.message_id == "msg-123"
    assert result.to == "reader@kindle.com"


def test_non_2xx_response_raises():
    http = FakeHttp(FakeResponse(422, {"ErrorCode": 300, "Message": "Invalid 'To' address"}))
    with pytest.raises(PostmarkError) as exc:
        _send(http)
    assert "Invalid 'To' address" in str(exc.value)


def test_postmark_error_code_in_2xx_still_raises():
    # Postmark can return HTTP 200 with a non-zero ErrorCode; that is still a failure.
    http = FakeHttp(FakeResponse(200, {"ErrorCode": 10, "Message": "Inactive recipient"}))
    with pytest.raises(PostmarkError):
        _send(http)


def test_transport_exception_propagates_as_failure():
    def boom(url, *, json, headers):
        raise ConnectionError("network down")

    with pytest.raises(ConnectionError):
        _send(boom)


def test_no_mcp_send_email_reference_in_module():
    import substack_kindle.postmark as mod

    with open(mod.__file__, encoding="utf-8") as fh:
        source = fh.read().lower()
    assert "sendemail" not in source  # we use the REST /email endpoint, not MCP sendEmail
