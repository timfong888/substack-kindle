"""Tests for provider-neutral email header helpers (``sender_display_name``)."""

from substack_kindle.email_headers import sender_display_name


def test_sender_name_uses_display_name():
    assert sender_display_name("ByteByteGo <alex@bytebytego.com>") == "ByteByteGo"


def test_sender_name_fallback_when_no_display_name():
    # No display name → derive from local part of email address.
    assert sender_display_name("lenny@substack.com") == "Lenny"


def test_sender_name_strips_plus_tag():
    assert sender_display_name("newsletter+promo@example.com") == "Newsletter"


def test_sender_name_blank_for_whitespace_only_display_name():
    # SAT-288: a whitespace-only display name (e.g. `"  " <>`) is truthy in
    # Python, so a naive `if name:` check would return it as-is — a label
    # that *looks* empty but isn't. It must come back "" so the EPUB TOC
    # falls back to the subject-only label instead of a blank one.
    assert sender_display_name('"  " <>') == ""


def test_sender_name_blank_when_from_has_no_at_sign():
    # A malformed From header with no "@" at all (no valid address to derive
    # a domain/local-part from) must not produce a misleading name.
    assert sender_display_name("Undisclosed recipients:;") == ""


def test_sender_name_blank_for_empty_from_header():
    assert sender_display_name("") == ""


def test_sender_name_decodes_rfc2047_encoded_display_name():
    # Non-ASCII sender names are commonly RFC 2047 encoded-word in the raw
    # header (e.g. "=?UTF-8?B?...?="). The raw encoded token must never leak
    # into the TOC label — it must be decoded to the human-readable name.
    raw = "=?UTF-8?B?Qnl0ZUJ5dGVHbw==?= <alex@bytebytego.com>"
    assert sender_display_name(raw) == "ByteByteGo"


def test_sender_name_falls_back_on_unparseable_encoded_word():
    # A display name with an unknown/bogus RFC 2047 charset makes
    # email.header.decode_header raise LookupError. That must never crash
    # (SAT-288 AC: unknown sender format -> subject-only fallback, never a
    # crash) — the raw display-name text is kept as-is instead.
    raw = "=?BOGUS-CHARSET?B?SGVsbG8=?= <alex@bytebytego.com>"
    assert sender_display_name(raw) == "=?BOGUS-CHARSET?B?SGVsbG8=?="
