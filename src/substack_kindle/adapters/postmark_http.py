"""Real Postmark HTTP transport + a notify ``send_email`` adapter.

``make_http_post`` produces the ``http_post`` callable that ``postmark.send_epub``
expects (defaulting to a live ``httpx`` POST). ``make_send_email`` produces the
plain-text notify sender used by ``notify.send_delivery_notification``, posting to
Postmark's ``/email`` endpoint and raising ``PostmarkError`` on any failure —
reusing the exact success checks from ``postmark.send_epub``.
"""

from __future__ import annotations

from collections.abc import Callable

from substack_kindle.postmark import POSTMARK_EMAIL_URL, PostmarkError


def make_http_post(client_post: Callable[..., object] | None = None) -> Callable[..., object]:
    """Return an ``http_post(url, *, json, headers) -> response`` callable.

    The default transport issues a live ``httpx.post`` with a 30s timeout. A
    ``client_post(url, json, headers)`` may be injected for tests.
    """
    if client_post is None:

        def client_post(url, json, headers):  # type: ignore[misc]
            import httpx

            return httpx.post(url, json=json, headers=headers, timeout=30)

    def http_post(url: str, *, json: dict, headers: dict) -> object:
        return client_post(url, json=json, headers=headers)

    return http_post


def make_send_email(
    *,
    server_token: str,
    from_: str,
    client_post: Callable[..., object] | None = None,
    message_stream: str = "outbound",
) -> Callable[..., None]:
    """Return a ``send_email(*, to, subject, body)`` that posts a plain-text email.

    Raises ``PostmarkError`` on a non-2xx HTTP status or a non-zero ErrorCode,
    matching ``postmark.send_epub``'s failure contract (the project's send
    convention is that a send raises on failure).
    """
    http_post = make_http_post(client_post=client_post)

    def send_email(*, to: str, subject: str, body: str) -> None:
        payload = {
            "From": from_,
            "To": to,
            "Subject": subject,
            "TextBody": body,
            "MessageStream": message_stream,
        }
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Postmark-Server-Token": server_token,
        }
        response = http_post(POSTMARK_EMAIL_URL, json=payload, headers=headers)
        if not (200 <= response.status_code < 300):
            raise PostmarkError(f"Postmark returned HTTP {response.status_code}")
        result = response.json()
        if result.get("ErrorCode", 0) != 0:
            raise PostmarkError(
                f"Postmark error {result['ErrorCode']}: {result.get('Message', '')}"
            )

    return send_email
