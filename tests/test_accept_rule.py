"""Tests for the intake accept rule (SAT-1104, docs/design/intake-mailbox.md §2).

The header set is the real one captured from the intake mailbox on 2026-10-19
(a ByteByteGo issue), with the owner's plus-tag and Substack ids replaced by
fixture values. The rule is a pure function over headers: no I/O, no Gmail client.
"""

from __future__ import annotations

import json

import pytest

from substack_kindle.accept_rule import (
    GOOGLE_AUTHSERV_ID,
    INTAKE_DOMAIN,
    SUBSTACK_FROM_DOMAIN,
    Decision,
    accept,
)

CODE = "tim-ab12cd34"
INTAKE = f"substack+{CODE}@{INTAKE_DOMAIN}"
SUBSTACK_USER_ID = "3002150"

AUTH_PASS = (
    "mx.google.com; dkim=pass header.i=@mg-d1.substack.com header.s=k1 header.b=gCtYTpkJ; "
    "spf=pass (google.com: domain of bounce+86c1c1.39b08c6-substack+tim=fong888.com"
    "@mg-d1.substack.com designates 159.112.244.5 as permitted sender) "
    'smtp.mailfrom="bounce+86c1c1.39b08c6-substack+tim=fong888.com@mg-d1.substack.com"; '
    "dmarc=pass (p=REJECT sp=REJECT dis=NONE) header.from=substack.com"
)
AUTH_FAIL = AUTH_PASS.replace("dmarc=pass", "dmarc=fail")
AUTH_OTHER_DOMAIN = AUTH_PASS.replace("header.from=substack.com", "header.from=evil.example")


def _mailgun_vars(**overrides: str) -> str:
    base = {
        "category": "post",
        "email_generated_at": "1792424161053",
        "is_freemail": "true",
        "open_tracking_suppressed": "",
        "post_audience": "everyone",
        "post_id": "220742828",
        "post_type": "newsletter",
        "pub_community_enabled": "true",
        "publication_id": "817132",
        "subdomain": "bytebytego",
        "user_id": SUBSTACK_USER_ID,
    }
    base.update(overrides)
    return json.dumps(base)


def _headers(**overrides: str | None) -> dict[str, str]:
    """The newsletter header set; a ``None`` override deletes the header."""
    headers: dict[str, str | None] = {
        "Delivered-To": INTAKE,
        "Authentication-Results": AUTH_PASS,
        "From": "ByteByteGo <bytebytego@substack.com>",
        "To": INTAKE,
        "Subject": "How Uber Built a Genie to Answer Questions Correctly",
        "Date": "Mon, 19 Oct 2026 15:31:03 +0000",
        "Message-Id": "<20261019153103.3.3227308d743d5e0d@mg-d1.substack.com>",
        "List-Id": "<bytebytego.substack.com>",
        "List-Post": "<https://blog.bytebytego.com/p/how-uber-built-a-genie-to-answer-051>",
        "X-Mailgun-Variables": _mailgun_vars(),
    }
    headers.update(overrides)
    return {k: v for k, v in headers.items() if v is not None}


def _known(code: str = CODE, pinned: str | None = SUBSTACK_USER_ID) -> dict[str, str | None]:
    return {code: pinned}


# ---------------------------------------------------------------- newsletter


def test_authenticated_newsletter_for_known_code_is_accepted():
    decision = accept(_headers(), known_codes=_known())
    assert decision == Decision(
        kind="newsletter",
        user_code=CODE,
        substack_user_id=SUBSTACK_USER_ID,
        publication="bytebytego.substack.com",
        post_url="https://blog.bytebytego.com/p/how-uber-built-a-genie-to-answer-051",
        reason=None,
    )


def test_first_newsletter_for_code_with_no_pin_is_accepted_and_reports_id_to_pin():
    decision = accept(_headers(), known_codes=_known(pinned=None))
    assert decision.kind == "newsletter"
    assert decision.user_code == CODE
    # The caller pins this id on the user; later mismatches are rejected.
    assert decision.substack_user_id == SUBSTACK_USER_ID


def test_header_lookup_is_case_insensitive():
    lower = {k.lower(): v for k, v in _headers().items()}
    assert accept(lower, known_codes=_known()).kind == "newsletter"


def test_delivered_to_decides_the_user_not_to():
    # Gmail's Delivered-To is authoritative; To could be anything the sender wrote.
    decision = accept(_headers(To="someone-else@example.com"), known_codes=_known())
    assert decision.kind == "newsletter"
    assert decision.user_code == CODE


# ---------------------------------------------------------------- rejections


def test_dmarc_fail_is_rejected():
    decision = accept(_headers(**{"Authentication-Results": AUTH_FAIL}), known_codes=_known())
    assert decision.kind == "rejected"
    assert "dmarc" in (decision.reason or "")
    assert decision.user_code == CODE  # still attributed so the dashboard can show it


def test_dmarc_pass_for_another_domain_is_rejected():
    decision = accept(
        _headers(**{"Authentication-Results": AUTH_OTHER_DOMAIN}), known_codes=_known()
    )
    assert decision.kind == "rejected"
    assert SUBSTACK_FROM_DOMAIN in (decision.reason or "")


def test_missing_authentication_results_is_rejected():
    decision = accept(_headers(**{"Authentication-Results": None}), known_codes=_known())
    assert decision.kind == "rejected"
    assert GOOGLE_AUTHSERV_ID in (decision.reason or "")


def test_sender_injected_authentication_results_is_ignored():
    # Only the header Google added on receipt counts; a sender can write its own.
    forged = AUTH_PASS.replace("mx.google.com;", "attacker.example;", 1)
    decision = accept(_headers(**{"Authentication-Results": forged}), known_codes=_known())
    assert decision.kind == "rejected"


def test_repeated_authentication_results_uses_googles():
    forged_fail = AUTH_FAIL.replace("mx.google.com;", "attacker.example;", 1)
    decision = accept(
        _headers(**{"Authentication-Results": [forged_fail, AUTH_PASS]}), known_codes=_known()
    )
    assert decision.kind == "newsletter"


def test_header_from_must_be_the_apex_domain_exactly():
    subdomain = AUTH_PASS.replace("header.from=substack.com", "header.from=mg-d1.substack.com")
    decision = accept(_headers(**{"Authentication-Results": subdomain}), known_codes=_known())
    assert decision.kind == "rejected"


def test_repeated_header_first_value_wins():
    headers = _headers()
    headers["from"] = "Someone Else <else@substack.com>"  # a second, lower-cased From
    decision = accept(headers, known_codes=_known())
    assert decision.kind == "newsletter"  # From is still the first (original) value


def test_unknown_plus_tag_is_rejected_with_no_user():
    decision = accept(
        _headers(**{"Delivered-To": f"substack+nobody@{INTAKE_DOMAIN}"}), known_codes=_known()
    )
    assert decision.kind == "rejected"
    assert decision.user_code is None
    assert "unknown" in (decision.reason or "")


def test_delivered_to_without_plus_tag_is_rejected():
    decision = accept(
        _headers(**{"Delivered-To": f"substack@{INTAKE_DOMAIN}"}), known_codes=_known()
    )
    assert decision.kind == "rejected"
    assert decision.user_code is None


def test_substack_user_id_mismatch_with_pin_is_rejected():
    # Someone else's Substack account sending to a leaked plus-address.
    decision = accept(
        _headers(**{"X-Mailgun-Variables": _mailgun_vars(user_id="999")}),
        known_codes=_known(pinned=SUBSTACK_USER_ID),
    )
    assert decision.kind == "rejected"
    assert "user_id" in (decision.reason or "")
    assert decision.substack_user_id == "999"


def test_newsletter_without_mailgun_variables_is_rejected():
    # No account id means rule 3 cannot be evaluated; fail closed.
    decision = accept(_headers(**{"X-Mailgun-Variables": None}), known_codes=_known())
    assert decision.kind == "rejected"


def test_malformed_mailgun_variables_is_rejected_not_raised():
    decision = accept(_headers(**{"X-Mailgun-Variables": "{not json"}), known_codes=_known())
    assert decision.kind == "rejected"


def test_malformed_mailgun_variables_from_notification_sender_is_still_rejected():
    # A parse failure must surface, not be filed quietly as account mail.
    decision = accept(
        _headers(From="Substack <no-reply@substack.com>", **{"X-Mailgun-Variables": "{not json"}),
        known_codes=_known(),
    )
    assert decision.kind == "rejected"
    assert "JSON" in (decision.reason or "")


def test_null_user_id_is_rejected_not_pinned_as_the_string_none():
    raw = json.loads(_mailgun_vars())
    raw["user_id"] = None
    decision = accept(
        _headers(**{"X-Mailgun-Variables": json.dumps(raw)}), known_codes=_known(pinned=None)
    )
    assert decision.kind == "rejected"
    assert decision.substack_user_id is None


def test_numeric_user_id_is_normalised_to_a_string():
    raw = json.loads(_mailgun_vars())
    raw["user_id"] = 3002150
    decision = accept(_headers(**{"X-Mailgun-Variables": json.dumps(raw)}), known_codes=_known())
    assert decision.kind == "newsletter"
    assert decision.substack_user_id == SUBSTACK_USER_ID


# ---------------------------------------------------------------- not newsletters


def test_live_video_notice_is_a_notification():
    decision = accept(
        _headers(
            From="Substack <no-reply@substack.com>",
            Subject="Live video with Grace now: Live meditation with special guest Elinda",
            **{"X-Mailgun-Variables": _mailgun_vars(category="live_stream", post_type="")},
        ),
        known_codes=_known(),
    )
    assert decision.kind == "notification"
    assert decision.user_code == CODE


def test_account_mail_from_substack_is_account_kind():
    # Login codes / receipts: authenticated, from Substack, but not a post.
    decision = accept(
        _headers(
            From="Substack <no-reply@substack.com>",
            Subject="Your Substack sign-in link",
            **{"X-Mailgun-Variables": None, "List-Id": None, "List-Post": None},
        ),
        known_codes=_known(),
    )
    assert decision.kind == "account"
    assert decision.user_code == CODE
    assert decision.substack_user_id is None


def test_non_newsletter_post_type_is_a_notification():
    decision = accept(
        _headers(**{"X-Mailgun-Variables": _mailgun_vars(post_type="podcast")}),
        known_codes=_known(),
    )
    assert decision.kind == "notification"


def test_notification_does_not_check_the_pin():
    # Account mail carries no post user_id; a pinned user must still receive it.
    decision = accept(
        _headers(
            From="Substack <no-reply@substack.com>",
            Subject="Your Substack sign-in link",
            **{"X-Mailgun-Variables": None},
        ),
        known_codes=_known(pinned="999"),
    )
    assert decision.kind == "account"


# ---------------------------------------------------------------- publication fields


def test_publication_and_post_url_are_unbracketed():
    decision = accept(_headers(), known_codes=_known())
    assert decision.publication == "bytebytego.substack.com"
    assert decision.post_url is not None and decision.post_url.startswith("https://")


def test_missing_list_headers_leave_publication_fields_none():
    decision = accept(_headers(**{"List-Id": None, "List-Post": None}), known_codes=_known())
    assert decision.kind == "newsletter"
    assert decision.publication is None
    assert decision.post_url is None


@pytest.mark.parametrize("field", ["Delivered-To", "From"])
def test_missing_core_header_is_rejected_not_raised(field):
    decision = accept(_headers(**{field: None}), known_codes=_known())
    assert decision.kind == "rejected"
