"""Per-user RSS digest runner.

    uv run substack-kindle-digest

Asks Convex (``digest:targets``) which users are due a digest. For each one it
reads their public feeds, keeps posts from the last ``LOOKBACK_DAYS`` that were
not already delivered to them, builds one EPUB, sends it from the user's own
sender address (``kindle+<token>@…``) to their Kindle, and records
post → content revision → delivery (``digest:recordDigest``). One user's
failure never stops the others, and a failed send is not recorded, so the next
run retries it.

Convex calls are public functions guarded by an HMAC over
``PIPELINE_SHARED_SECRET`` (see ``web/convex/digest.ts``); this process holds no
Convex admin key. Logs carry counts and Convex user ids only (the repo and its
Actions logs are public): never addresses, titles or bodies.

Required env: ``CONVEX_URL``, ``PIPELINE_SHARED_SECRET``, ``RESEND_API_KEY``.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit

from . import http_transport, resend
from .job_epub import build_job_epub
from .rss_fetch import fetch_posts
from .whitelist_check import ensure_distinct_local_parts

LOOKBACK_DAYS = 7
MAX_POSTS = 25
MAX_REDIRECTS = 3
REQUIRED_ENV = ("CONVEX_URL", "PIPELINE_SHARED_SECRET", "RESEND_API_KEY")


class UnsafeUrlError(ValueError):
    """A feed URL is not a public https URL."""


class Convex(Protocol):
    def query(self, path: str, args: dict[str, Any]) -> Any: ...
    def mutation(self, path: str, args: dict[str, Any]) -> Any: ...


SendFn = Callable[..., str]


def sign(secret: str, message: str) -> str:
    """hex(HMAC-SHA256(secret, message)); must match ``hmacHex`` in digest.ts."""
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def check_public_https_url(url: str) -> None:
    """Refuse anything but https to a public host (feeds are user-supplied: SSRF)."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host:
        raise UnsafeUrlError(f"not an https URL: {url!r}")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise UnsafeUrlError(f"local host: {url!r}")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        if "." not in host:
            raise UnsafeUrlError(f"not a public host: {url!r}") from None
        return
    if not ip.is_global:
        raise UnsafeUrlError(f"non-public address: {url!r}")


def safe_http_get(url: str, *, get: Callable[..., Any] | None = None) -> bytes:
    """GET a feed, following at most ``MAX_REDIRECTS`` redirects, re-checking each hop."""
    if get is None:
        import httpx

        get = httpx.get
    for _ in range(MAX_REDIRECTS + 1):
        check_public_https_url(url)
        resp = get(url, timeout=20.0, follow_redirects=False)
        if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
            url = urljoin(url, resp.headers["location"])
            continue
        resp.raise_for_status()
        return resp.content
    raise UnsafeUrlError(f"too many redirects: {url!r}")


class ConvexHttp:
    """Minimal Convex HTTP API client (``POST /api/query`` and ``/api/mutation``)."""

    def __init__(self, url: str, post: Callable[..., Any]) -> None:
        self._base = url.rstrip("/")
        self._post = post

    def _call(self, kind: str, path: str, args: dict[str, Any]) -> Any:
        resp = self._post(
            f"{self._base}/api/{kind}",
            json={"path": path, "args": args, "format": "json"},
            timeout=30.0,
        )
        body = resp.json()
        if resp.status_code != 200 or body.get("status") != "success":
            raise RuntimeError(
                f"Convex {kind} {path} failed: {body.get('errorMessage', resp.status_code)}"
            )
        return body.get("value")

    def query(self, path: str, args: dict[str, Any]) -> Any:
        return self._call("query", path, args)

    def mutation(self, path: str, args: dict[str, Any]) -> Any:
        return self._call("mutation", path, args)


@dataclass
class DigestRunSummary:
    sent: int = 0
    empty: int = 0
    failed: int = 0


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _digest_one(
    target: dict[str, Any],
    *,
    convex: Convex,
    secret: str,
    http_get: Callable[[str], bytes | str],
    send: SendFn,
    now: datetime,
) -> str:
    user_id = str(target["userId"])
    ensure_distinct_local_parts(
        whitelist_email=target["sender"], kindle_email=target["kindleEmail"]
    )
    delivered = set(target.get("deliveredKeys") or [])
    posts = fetch_posts(
        http_get,
        feed_urls=list(target["feedUrls"]),
        window_start=now - timedelta(days=LOOKBACK_DAYS),
        window_end=now,
    )
    seen: set[str] = set()
    fresh = []
    for p in posts:
        if p.guid in delivered or p.guid in seen:
            continue
        seen.add(p.guid)
        fresh.append(p)
    fresh = sorted(fresh, key=lambda p: p.published)[-MAX_POSTS:]

    issued = _ms(now)
    if not fresh:
        convex.mutation(
            "digest:markNoDigest",
            {
                "userId": user_id,
                "issuedAt": float(issued),
                "signature": sign(secret, f"no-digest:{user_id}:{issued}"),
            },
        )
        return "empty"

    keys = "\n".join(p.guid for p in fresh)
    digest_key = f"digest-{user_id}-{hashlib.sha256(keys.encode()).hexdigest()[:16]}"
    title = f"Substack Digest · {now:%a %d %b %Y}"
    epub_bytes = build_job_epub(
        [p.to_section() for p in fresh],
        book_title=title,
        identifier=f"urn:digest:{digest_key}",
        subtitle=f"{len(fresh)} posts from {len({p.sender for p in fresh})} publications",
    )
    send(
        epub_bytes=epub_bytes,
        to=target["kindleEmail"],
        from_=target["sender"],
        filename=f"substack-digest-{now:%Y-%m-%d}.epub",
        idempotency_key=digest_key,
    )
    convex.mutation(
        "digest:recordDigest",
        {
            "userId": user_id,
            "digestKey": digest_key,
            "posts": [
                {
                    "key": p.guid,
                    **({"url": p.guid} if p.guid.startswith("https://") else {}),
                    "title": p.title,
                    "publication": p.sender,
                    "hash": hashlib.sha256(p.markdown.encode()).hexdigest(),
                }
                for p in fresh
            ],
            "issuedAt": float(issued),
            "signature": sign(secret, f"record-digest:{user_id}:{digest_key}:{issued}"),
        },
    )
    return "sent"


def run_digests(
    *,
    convex: Convex,
    secret: str,
    http_get: Callable[[str], bytes | str],
    send: SendFn,
    now: datetime | None = None,
) -> DigestRunSummary:
    now = now or datetime.now(UTC)
    issued = _ms(now)
    targets = convex.query(
        "digest:targets",
        {"issuedAt": float(issued), "signature": sign(secret, f"digest-targets:{issued}")},
    )
    summary = DigestRunSummary()
    for target in targets:
        try:
            outcome = _digest_one(
                target, convex=convex, secret=secret, http_get=http_get, send=send, now=now
            )
        except Exception as exc:  # one user must not stop the rest
            summary.failed += 1
            print(
                f"digest: user {target.get('userId')} failed: {type(exc).__name__}",
                file=sys.stderr,
            )
            continue
        if outcome == "sent":
            summary.sent += 1
        else:
            summary.empty += 1
    return summary


def main(env: Mapping[str, str] | None = None) -> int:
    env = env if env is not None else os.environ
    missing = [k for k in REQUIRED_ENV if not env.get(k)]
    if missing:
        print(f"digest: missing env: {', '.join(missing)}", file=sys.stderr)
        return 2
    import httpx

    api_key = env["RESEND_API_KEY"]

    def send(**kwargs: Any) -> str:
        return resend.send_epub(api_key=api_key, http_post=http_transport.post, **kwargs).message_id

    summary = run_digests(
        convex=ConvexHttp(env["CONVEX_URL"], httpx.post),
        secret=env["PIPELINE_SHARED_SECRET"],
        http_get=safe_http_get,
        send=send,
    )
    print(f"digest: sent={summary.sent} empty={summary.empty} failed={summary.failed}")
    return 1 if summary.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
