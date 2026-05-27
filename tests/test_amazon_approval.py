"""Tests for safety-constrained Amazon approval-email handling (SAT-254 / #18, Req 13).

SECURITY-CRITICAL. Acceptance:
- An approval action only happens during the onboarding window, right after the
  customer added whitelist_email.
- The message must be verified to genuinely originate from Amazon before any action.
- One-tap path: the system surfaces a pending approval; it never silently clicks.
- Outside the onboarding window, approval-type emails are NOT auto-actioned.
"""

import pytest

from substack_kindle.amazon_approval import (
    InboundEmail,
    confirm_pending_approval,
    detect_pending_approval,
    select_pending_approval,
)

APPROVAL_BODY = (
    "<p>To approve, click "
    '<a href="https://www.amazon.com/gp/f.html?approve=abc123">this link</a>.</p>'
)


def _amazon_email(body=APPROVAL_BODY, sender="no-reply@amazon.com", subject="Approve your email"):
    return InboundEmail(from_address=sender, subject=subject, body=body)


def _authentic(_message):
    return True


def _not_authentic(_message):
    return False


class ClickSpy:
    def __init__(self):
        self.clicked = []

    def __call__(self, url):
        self.clicked.append(url)
        return {"ok": True}


def test_detects_pending_approval_in_window_for_authentic_amazon_mail():
    pending = detect_pending_approval(
        _amazon_email(), window_open=True, is_authentic=_authentic
    )
    assert pending is not None
    assert pending.approval_url == "https://www.amazon.com/gp/f.html?approve=abc123"


def test_detection_has_no_click_capability():
    # One-tap is structural: detection takes no click hook, so it cannot click.
    import inspect

    assert "click" not in inspect.signature(detect_pending_approval).parameters


def test_picks_approval_link_not_logo_or_footer_in_multi_url_body():
    body = (
        '<img src="https://images.amazon.com/logo.png">'
        '<a href="https://www.amazon.com/">Amazon</a>'
        '<a href="https://www.amazon.com/gp/sendtokindle/approve?tok=xyz">Approve</a>'
        '<a href="https://www.amazon.com/account">Manage account</a>'
    )
    pending = detect_pending_approval(
        _amazon_email(body=body), window_open=True, is_authentic=_authentic
    )
    assert pending is not None
    # The logo image and footer/account links must not be surfaced.
    assert pending.approval_url == "https://www.amazon.com/gp/sendtokindle/approve?tok=xyz"


def test_no_action_outside_onboarding_window():
    pending = detect_pending_approval(
        _amazon_email(), window_open=False, is_authentic=_authentic
    )
    assert pending is None


def test_spoofed_non_amazon_sender_is_ignored():
    spoof = _amazon_email(sender="no-reply@amaz0n-security.example")
    pending = detect_pending_approval(spoof, window_open=True, is_authentic=_authentic)
    assert pending is None


def test_unauthenticated_message_is_ignored_even_with_amazon_from():
    # A forged From: amazon.com header must not pass without authenticity (DKIM/SPF).
    pending = detect_pending_approval(
        _amazon_email(), window_open=True, is_authentic=_not_authentic
    )
    assert pending is None


def test_non_approval_amazon_email_is_ignored():
    order = _amazon_email(
        body="<p>Your order has shipped. https://www.amazon.com/orders</p>",
        subject="Your order has shipped",
    )
    pending = detect_pending_approval(order, window_open=True, is_authentic=_authentic)
    assert pending is None


def test_approval_email_without_amazon_link_is_ignored():
    body = '<p>Approve your email <a href="https://evil.example/approve">here</a>.</p>'
    msg = _amazon_email(body=body)
    pending = detect_pending_approval(msg, window_open=True, is_authentic=_authentic)
    assert pending is None  # link host is not an Amazon domain


@pytest.mark.parametrize(
    "evil_url",
    [
        "https://amazon.com.evil.example/approve",  # lookalike suffix
        "https://www.amazon.com.evil.example/approve",
        "https://amazon.com@evil.example/approve",  # userinfo spoof (host is evil.example)
        "https://evilamazon.com/approve",  # no dot boundary before amazon.com
    ],
)
def test_amazon_lookalike_approval_link_is_rejected(evil_url):
    # Host validation must not be fooled by lookalike suffixes or userinfo tricks.
    body = f'<p>Approve your email <a href="{evil_url}">here</a>.</p>'
    msg = _amazon_email(body=body)
    pending = detect_pending_approval(msg, window_open=True, is_authentic=_authentic)
    assert pending is None


def test_confirm_performs_one_tap_click_only_when_called():
    pending = detect_pending_approval(_amazon_email(), window_open=True, is_authentic=_authentic)
    spy = ClickSpy()
    result = confirm_pending_approval(pending, click=spy)
    assert spy.clicked == ["https://www.amazon.com/gp/f.html?approve=abc123"]
    assert result == {"ok": True}


def test_confirm_rejects_none():
    spy = ClickSpy()
    with pytest.raises(ValueError):
        confirm_pending_approval(None, click=spy)
    assert spy.clicked == []


# --- Recency-aware selection across multiple candidates (SAT-262) -------------
# Amazon's window keeps prior verification/approval emails live for ~48h. Picking
# a stale leftover yields a dead link, so the selector must choose the newest
# email that arrived after the request was initiated, and fail closed otherwise.

_FRESH_URL = "https://www.amazon.com/gp/f.html?approve=fresh"
_STALE_URL = "https://www.amazon.com/gp/f.html?approve=stale"


def _approval_at(received_at, approve_token):
    return InboundEmail(
        from_address="no-reply@amazon.com",
        subject="Approve your email",
        body=f'<p>Approve <a href="https://www.amazon.com/gp/f.html?approve={approve_token}">here</a>.</p>',
        received_at=received_at,
    )


def test_selects_newest_approval_after_request_time():
    sent_at = 1000.0
    messages = [
        _approval_at(900.0, "stale"),  # before request -> leftover
        _approval_at(1100.0, "fresh"),  # after request -> the one
    ]
    pending = select_pending_approval(
        messages, window_open=True, is_authentic=_authentic, since_epoch=sent_at
    )
    assert pending is not None
    assert pending.approval_url == _FRESH_URL


def test_stale_only_candidates_yield_none():
    sent_at = 1000.0
    messages = [_approval_at(900.0, "stale")]  # only the leftover
    pending = select_pending_approval(
        messages, window_open=True, is_authentic=_authentic, since_epoch=sent_at
    )
    assert pending is None  # fail closed; do not surface a dead link


def test_undated_candidate_is_excluded_cannot_prove_freshness():
    sent_at = 1000.0
    messages = [_amazon_email()]  # received_at defaults to None
    pending = select_pending_approval(
        messages, window_open=True, is_authentic=_authentic, since_epoch=sent_at
    )
    assert pending is None


def test_clock_skew_margin_allows_slightly_early_amazon_timestamp():
    sent_at = 1000.0
    messages = [_approval_at(970.0, "fresh")]  # 30s before our local send clock
    pending = select_pending_approval(
        messages, window_open=True, is_authentic=_authentic, since_epoch=sent_at, skew=60
    )
    assert pending is not None
    assert pending.approval_url == _FRESH_URL


def test_selection_still_enforces_security_gates():
    sent_at = 1000.0
    # Newest message is a spoof; it must be rejected even though it is freshest.
    spoof = InboundEmail(
        from_address="no-reply@amaz0n-security.example",
        subject="Approve your email",
        body='<p>Approve <a href="https://evil.example/approve">here</a>.</p>',
        received_at=1200.0,
    )
    genuine = _approval_at(1100.0, "fresh")
    pending = select_pending_approval(
        [spoof, genuine], window_open=True, is_authentic=_authentic, since_epoch=sent_at
    )
    assert pending is not None
    assert pending.approval_url == _FRESH_URL


def test_selection_respects_onboarding_window():
    sent_at = 1000.0
    messages = [_approval_at(1100.0, "fresh")]
    pending = select_pending_approval(
        messages, window_open=False, is_authentic=_authentic, since_epoch=sent_at
    )
    assert pending is None
