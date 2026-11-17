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
from .cover import CoverPost, render_cover
from .job_epub import build_job_epub
from .rss_fetch import fetch_posts, parse_feed
from .whitelist_check import ensure_distinct_local_parts

LOOKBACK_DAYS = 7
MAX_POSTS = 25
MAX_REDIRECTS = 3
REQUIRED_ENV = ("CONVEX_URL", "PIPELINE_SHARED_SECRET", "RESEND_API_KEY")


class UnsafeUrlError(ValueError):
    """A feed URL is not a public https URL."""


class AlreadySent(Exception):
    """Resend already holds a send for this idempotency key (HTTP 409 on retry).

    Raised by the send function when a previous run's email went out but its
    delivery was not recorded; the runner then records it instead of failing.
    """


def is_already_sent(exc: Exception) -> bool:
    """True only for Resend's ``invalid_idempotent_request`` (409): the key was
    already used, with a different payload, by a request Resend accepted. Our
    retry rebuilds the EPUB, whose timestamps differ, so this means "sent".

    ``concurrent_idempotent_requests`` is also a 409 but means the first request
    is still in flight and may yet fail, so it stays a retryable failure.
    """
    return "invalid_idempotent_request" in str(exc)


class FeedsUnavailable(Exception):
    """None of a user's feeds could be read this run; retry next run."""


class Convex(Protocol):
    def query(self, path: str, args: dict[str, Any]) -> Any: ...
    def mutation(self, path: str, args: dict[str, Any]) -> Any: ...


SendFn = Callable[..., str]


def sign(secret: str, message: str) -> str:
    """hex(HMAC-SHA256(secret, message)); must match ``hmacHex`` in digest.ts."""
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def _system_resolver(host: str) -> list[str]:
    import socket

    return [info[4][0] for info in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)]


def check_public_https_url(url: str, *, resolve: Callable[[str], list[str]] | None = None) -> None:
    """Refuse anything but https to a public host (feeds are user-supplied: SSRF).

    Host names are resolved and every address must be public, so a public
    name pointing at a private or metadata address is refused. (A DNS answer
    that changes between this check and the connection is not covered.)
    """
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host:
        raise UnsafeUrlError(f"not an https URL: {url!r}")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise UnsafeUrlError(f"local host: {url!r}")
    try:
        literal: str | None = str(ipaddress.ip_address(host))
    except ValueError:
        literal = None
    if literal is not None:
        addresses = [literal]
    else:
        if "." not in host:
            raise UnsafeUrlError(f"not a public host: {url!r}")
        try:
            addresses = (resolve or _system_resolver)(host)
        except OSError as exc:
            raise UnsafeUrlError(f"could not resolve {host!r}: {exc}") from None
        if not addresses:
            raise UnsafeUrlError(f"no addresses for {host!r}")
    for address in addresses:
        if not ipaddress.ip_address(address.split("%", 1)[0]).is_global:
            raise UnsafeUrlError(f"non-public address for {url!r}")


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


WORDS_PER_MINUTE = 230


def _cover(posts: list[Any], now: datetime, publications: int) -> bytes | None:
    """Text cover (#78); newest post leads. A cover failure never blocks a digest."""
    try:
        newest_first = sorted(posts, key=lambda p: p.published, reverse=True)
        words = sum(len(p.markdown.split()) for p in posts)
        return render_cover(
            day=now.date(),
            posts=[CoverPost(title=p.title, publication=p.sender) for p in newest_first],
            publication_count=publications,
            minutes=max(1, round(words / WORDS_PER_MINUTE)),
        )
    except Exception as exc:
        print(f"digest: cover skipped: {type(exc).__name__}", file=sys.stderr)
        return None


def _readable_feeds(urls: list[str], http_get: Callable[[str], bytes | str]) -> dict[str, Any]:
    """Fetch and parse-check each feed; failures are counted, never logged with
    their URL or error text (user-supplied URLs must not reach public logs)."""
    good: dict[str, Any] = {}
    for url in urls:
        try:
            body = http_get(url)
            parse_feed(body)
        except Exception:
            continue
        good[url] = body
    return good


def _digest_one(
    target: dict[str, Any],
    *,
    convex: Convex,
    secret: str,
    http_get: Callable[[str], bytes | str],
    send: SendFn,
    now: datetime,
    clock: Callable[[], datetime],
) -> str:
    user_id = str(target["userId"])
    ensure_distinct_local_parts(
        whitelist_email=target["sender"], kindle_email=target["kindleEmail"]
    )
    delivered = set(target.get("deliveredKeys") or [])
    urls = list(target["feedUrls"])
    feeds = _readable_feeds(urls, http_get)
    if urls and not feeds:
        # Every feed failed (outage, timeouts): leave the request and the daily
        # slot open so the next run retries, instead of recording "nothing new".
        raise FeedsUnavailable(f"0 of {len(urls)} feeds readable")
    posts = fetch_posts(
        feeds.__getitem__,
        feed_urls=list(feeds),
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

    if not fresh:
        issued = _ms(clock())  # signatures carry the time of the call itself
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
    publications = len({p.sender for p in fresh})
    epub_bytes = build_job_epub(
        [p.to_section() for p in fresh],
        book_title=title,
        identifier=f"urn:digest:{digest_key}",
        subtitle=f"{len(fresh)} posts from {publications} publications",
        cover_image=_cover(fresh, now, publications),
    )
    try:
        send(
            epub_bytes=epub_bytes,
            to=target["kindleEmail"],
            from_=target["sender"],
            filename=f"substack-digest-{now:%Y-%m-%d}.epub",
            idempotency_key=digest_key,
        )
    except AlreadySent:
        pass  # an earlier run sent this exact post set; record it now
    issued = _ms(clock())
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
    clock: Callable[[], datetime] | None = None,
) -> DigestRunSummary:
    clock = clock or (lambda: datetime.now(UTC))
    now = now or clock()  # the content window; signing uses clock() per call
    issued = _ms(clock())
    targets = convex.query(
        "digest:targets",
        {"issuedAt": float(issued), "signature": sign(secret, f"digest-targets:{issued}")},
    )
    summary = DigestRunSummary()
    for target in targets:
        try:
            outcome = _digest_one(
                target,
                convex=convex,
                secret=secret,
                http_get=http_get,
                send=send,
                now=now,
                clock=clock,
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
        try:
            return resend.send_epub(
                api_key=api_key, http_post=http_transport.post, **kwargs
            ).message_id
        except resend.ResendError as exc:
            if is_already_sent(exc):
                raise AlreadySent() from None
            raise

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
