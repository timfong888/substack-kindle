"""Provider-neutral email header helpers.

Used by ``handler.py`` to turn a raw ``From`` header value into a human-readable
publication name for the EPUB TOC. No I/O; no dependency on any mail provider.
"""

from __future__ import annotations

from email.errors import HeaderParseError
from email.header import decode_header, make_header
from email.utils import parseaddr


def sender_display_name(raw_from: str) -> str:
    """Return a human-readable publication name from a raw From header value.

    Prefers the display name (e.g. ``ByteByteGo`` from
    ``ByteByteGo <alex@bytebytego.com>``).  Falls back to the local part of
    the email address, title-cased (``lenny@substack.com`` → ``Lenny``).

    A malformed or blank ``From`` header must never produce a string that
    *looks* empty but is truthy — ``parseaddr`` can return a whitespace-only
    display name, which passes a bare ``if name:`` check. Both the display
    name and the derived local part are stripped before the truthiness
    check, so any header that yields no real name returns ``""`` (falsy).
    Callers rely on that to fall back to a subject-only label (SAT-288
    acceptance criteria: no empty/misleading label).

    A display name may also be RFC 2047 encoded-word (e.g.
    ``=?UTF-8?B?...?=``) for non-ASCII sender names; that is decoded before
    use so raw encoded tokens never leak into a TOC label.
    """
    name, address = parseaddr(raw_from)
    if name:
        try:
            name = str(make_header(decode_header(name)))
        except (HeaderParseError, LookupError, ValueError):
            pass  # keep the raw name rather than fail the whole run
    name = name.strip()
    if name:
        return name
    local = address.split("@")[0].split("+")[0].strip()
    if not local:
        return ""
    return local.replace("-", " ").replace("_", " ").title()


__all__ = ["sender_display_name"]
