"""HTTP transport adapter for ``postmark.send_epub`` (SAT-269).

``postmark.send_epub`` takes its HTTP caller as a parameter so it can stay free
of any concrete client. This module provides the production adapter — a thin
shim over ``httpx.post`` — and a way to inject any callable (``httpx.post``,
``requests.post``, or a fake) for tests. The shim does NOT interpret the
response; the sender already handles non-2xx and Postmark error codes.

The adapter exists as its own module (rather than a single line in cli.py) so
the dependency on httpx is contained in one place and easy to swap.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

DEFAULT_TIMEOUT_SECONDS = 30.0


def post(
    url: str,
    *,
    json: dict,
    headers: dict,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    http_post: Callable[..., Any] | None = None,
) -> Any:
    """POST ``json`` to ``url`` with ``headers`` and return the response unchanged.

    When ``http_post`` is omitted, ``httpx.post`` is used in production. Tests
    inject a fake to avoid network calls.
    """
    caller = http_post if http_post is not None else _default_caller()
    return caller(url, json=json, headers=headers, timeout=timeout)


def _default_caller() -> Callable[..., Any]:
    # Import lazily so non-network callers (tests, dry-runs) do not require httpx.
    import httpx

    return httpx.post
