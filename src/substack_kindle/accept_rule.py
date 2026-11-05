"""Intake accept rule: which user, and what kind, is an email in the intake mailbox?

Pure function over a message's headers (SAT-1104, docs/design/intake-mailbox.md §2).
A message is attributed to user ``code`` only when all of:

1. The ``Authentication-Results`` header that Google added on receipt (authserv-id
   ``mx.google.com``) says ``dmarc=pass`` with ``header.from=substack.com``. Google
   evaluates this; we only read it. A sender can inject its own
   ``Authentication-Results`` header, so any other authserv-id is ignored.
2. ``Delivered-To`` is ``substack+<code>@<INTAKE_DOMAIN>`` for a known code.
3. For a newsletter, the Substack account id in ``X-Mailgun-Variables`` matches the
   id pinned to that code. The pin is set from the first accepted newsletter; a
   later mismatch is a different Substack account writing to a leaked address.

Anything that passes 1 and 2 but is not a post (login codes, receipts, "Live
video" notices) is ``account`` or ``notification``: stored as metadata only and
never put in an EPUB. Gmail forwards those to the user regardless.

Headers are passed as a mapping of name to value, where a value may be a list
when the header is repeated (Gmail delivers several ``Authentication-Results``
and ``Received`` headers). Names are matched case-insensitively.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from email.utils import parseaddr

from .email_store import Kind

INTAKE_DOMAIN = "fong888.com"
INTAKE_LOCAL_PART = "substack"
SUBSTACK_FROM_DOMAIN = "substack.com"
SUBSTACK_NOTIFICATION_SENDER = "no-reply@substack.com"
GOOGLE_AUTHSERV_ID = "mx.google.com"

HeaderValue = str | Sequence[str]

_PLUS_TAG_RE = re.compile(
    rf"^{INTAKE_LOCAL_PART}\+([a-z0-9][a-z0-9-]*)@{re.escape(INTAKE_DOMAIN)}$", re.IGNORECASE
)
_DMARC_RE = re.compile(r"\bdmarc=(\w+)")
_HEADER_FROM_RE = re.compile(r"\bheader\.from=([\w.-]+)")


@dataclass(frozen=True)
class Decision:
    kind: Kind
    user_code: str | None
    substack_user_id: str | None
    publication: str | None
    post_url: str | None
    reason: str | None


class _Headers:
    """Case-insensitive view over a header mapping that may hold repeated values."""

    def __init__(self, headers: Mapping[str, HeaderValue]) -> None:
        self._h: dict[str, list[str]] = {}
        for name, value in headers.items():
            values = [value] if isinstance(value, str) else list(value)
            self._h.setdefault(name.lower(), []).extend(v.strip() for v in values)

    def get(self, name: str) -> str | None:
        """The first value of ``name``, or None."""
        values = self._h.get(name.lower())
        return values[0] if values else None

    def get_all(self, name: str) -> list[str]:
        return list(self._h.get(name.lower(), []))


def _unbracket(value: str | None) -> str | None:
    if not value:
        return None
    return value.strip().strip("<>").strip() or None


def _user_code(delivered_to: str | None) -> str | None:
    if not delivered_to:
        return None
    _, address = parseaddr(delivered_to)
    m = _PLUS_TAG_RE.match(address or delivered_to)
    return m.group(1).lower() if m else None


def _google_auth_results(values: list[str]) -> str | None:
    """The Authentication-Results value whose authserv-id is Google's, if any."""
    for value in values:
        authserv_id = value.split(";", 1)[0].strip().split()[0].lower() if value.strip() else ""
        if authserv_id == GOOGLE_AUTHSERV_ID:
            return value
    return None


def _dmarc_problem(auth_results: str | None) -> str | None:
    """None when Google authenticated the message as substack.com; else the reason."""
    if not auth_results:
        return f"no Authentication-Results from {GOOGLE_AUTHSERV_ID}"
    dmarc = _DMARC_RE.search(auth_results)
    if not dmarc or dmarc.group(1).lower() != "pass":
        return f"dmarc={dmarc.group(1) if dmarc else 'none'}"
    header_from = _HEADER_FROM_RE.search(auth_results)
    domain = header_from.group(1).lower() if header_from else ""
    if domain != SUBSTACK_FROM_DOMAIN:
        return f"header.from={domain or 'none'} is not {SUBSTACK_FROM_DOMAIN}"
    return None


def _mailgun_vars(raw: str | None) -> dict[str, object] | None:
    """Parsed ``X-Mailgun-Variables``; None when absent or malformed."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _user_id(mailgun: dict[str, object]) -> str | None:
    value = mailgun.get("user_id")
    if isinstance(value, bool) or not isinstance(value, str | int):
        return None  # null, missing, or something that is not an id
    text = str(value).strip()
    return text or None


def accept(
    headers: Mapping[str, HeaderValue], *, known_codes: Mapping[str, str | None]
) -> Decision:
    """Classify one message.

    ``known_codes`` maps each user's code to the Substack ``user_id`` pinned to it,
    or ``None`` when nothing is pinned yet. A ``newsletter`` decision for an
    unpinned code carries the id the caller should pin.
    """
    h = _Headers(headers)
    publication = _unbracket(h.get("List-Id"))
    post_url = _unbracket(h.get("List-Post"))

    def rejected(reason: str, code: str | None, user_id: str | None = None) -> Decision:
        return Decision("rejected", code, user_id, publication, post_url, reason)

    code = _user_code(h.get("Delivered-To"))
    if code is None:
        return rejected("Delivered-To is not a substack+<code> intake address", None)
    if code not in known_codes:
        return rejected(f"unknown user code in Delivered-To: {code}", None)

    auth_problem = _dmarc_problem(_google_auth_results(h.get_all("Authentication-Results")))
    if auth_problem:
        return rejected(auth_problem, code)

    from_header = h.get("From")
    if not from_header:
        return rejected("missing From", code)
    _, from_address = parseaddr(from_header)
    from_address = from_address.lower()

    raw_vars = h.get("X-Mailgun-Variables")
    mailgun = _mailgun_vars(raw_vars)
    if raw_vars and mailgun is None:
        return rejected("X-Mailgun-Variables is not valid JSON", code)

    if mailgun is None:
        if from_address == SUBSTACK_NOTIFICATION_SENDER:
            # Login codes, receipts, email-change confirmations: no post variables.
            return Decision("account", code, None, publication, post_url, None)
        # Authenticated Substack mail with neither post variables nor the
        # notification sender: can't tell what it is, so don't digest it.
        return rejected("no X-Mailgun-Variables on a non-notification message", code)

    if mailgun.get("post_type") != "newsletter":
        # Live video notices, podcast episodes, anything that is not a post.
        return Decision("notification", code, None, publication, post_url, None)

    user_id = _user_id(mailgun)
    if user_id is None:
        return rejected("newsletter has no user_id in X-Mailgun-Variables", code)
    pinned = known_codes[code]
    if pinned is not None and pinned != user_id:
        return rejected(f"user_id {user_id} does not match the id pinned to {code}", code, user_id)
    return Decision("newsletter", code, user_id, publication, post_url, None)


__all__ = [
    "GOOGLE_AUTHSERV_ID",
    "INTAKE_DOMAIN",
    "SUBSTACK_FROM_DOMAIN",
    "SUBSTACK_NOTIFICATION_SENDER",
    "Decision",
    "HeaderValue",
    "accept",
]
