"""Tests for the Substack template cleaner (SAT-265).

Substack newsletters render with a wall of chrome (tracking pixel, invisible
preview-pane padding, "Forwarded this email?" banner, title-as-link duplicate,
author + heart/comment/share/restack/"READ IN APP" icon row, footer). The
cleaner strips these structurally — no LLM — before html2text/markdownify so
the digest reads as the actual article body.

Rules are encoded by stable URL hosts/paths and one constant phrase, so a
non-Substack email passes through untouched.
"""

from substack_kindle.parsing import html_to_markdown


def _wrap(body_html: str) -> str:
    # Minimal "Substack-shaped" envelope: presence of any substackcdn.com or
    # substack.com/app-link URL is enough for the cleaner to activate.
    return f"<html><body>{body_html}</body></html>"


# --- Rule 1: tracking pixel ---------------------------------------------------


def test_tracking_pixel_is_dropped():
    html = _wrap(
        '<p>Real text.</p>'
        '<img src="https://eotrx.substackcdn.com/o/abc/p.gif?token=xyz" />'
    )
    md = html_to_markdown(html)
    assert "eotrx.substackcdn.com" not in md
    assert "Real text." in md


# --- Rule 2: invisible preview-pane padding -----------------------------------


def test_invisible_padding_line_is_dropped():
    # Substack stuffs the preview pane with zero-width / soft-hyphen runs.
    padding = "‍ ­͏ " * 30
    html = _wrap(
        f'<p>{padding}</p>'
        '<p>Subscribe at <a href="https://substack.com/app-link/post?x=1">x</a></p>'
        '<p>Body text.</p>'
    )
    md = html_to_markdown(html)
    assert "Body text." in md
    # The padding paragraph must not survive as a line of invisible chars.
    assert "‍" not in md
    assert "­" not in md
    assert "͏" not in md


# --- Rule 3: "Forwarded this email?" banner ----------------------------------


def test_forwarded_this_email_banner_is_dropped():
    html = _wrap(
        '<table><tr><td>'
        'Forwarded this email? <a href="https://substack.com/subscribe">Subscribe here</a> for more'
        '</td></tr></table>'
        '<p>Article body starts here.</p>'
    )
    md = html_to_markdown(html)
    assert "Forwarded this email" not in md
    assert "Subscribe here" not in md
    assert "Article body starts here." in md


# --- Rule 4: title-as-link duplicate H1 --------------------------------------


def test_link_wrapped_title_heading_is_dropped():
    html = _wrap(
        '<h1>Pax Silica</h1>'  # the real title — keep
        '<h1><a href="https://substack.com/app-link/post?id=1">Pax Silica</a></h1>'
        '<p>Body.</p>'
    )
    md = html_to_markdown(html)
    # The real H1 survives, the link-wrapped duplicate does not.
    assert md.count("Pax Silica") == 1
    assert "app-link/post" not in md


# --- Rule 5: author / icon metadata table ------------------------------------


def test_author_and_icon_row_table_is_dropped():
    html = _wrap(
        '<table>'
        '<tr><td><a href="https://substack.com/@kinjalshah">Kinjal</a></td></tr>'
        '<tr><td>May 24</td></tr>'
        # Heart, comments, share, restack, "READ IN APP" — each an icon-only anchor.
        '<tr><td><a href="https://substack.com/app-link/post?submitLike=true">'
        '<img src="https://substackcdn.com/icon/LucideHeart" /></a></td></tr>'
        '<tr><td><a href="https://substack.com/app-link/post?comments=true">'
        '<img src="https://substackcdn.com/icon/LucideComments" /></a></td></tr>'
        '<tr><td><a href="https://substack.com/app-link/post?action=share">'
        '<img src="https://substackcdn.com/icon/LucideShare2" /></a></td></tr>'
        '<tr><td><a href="https://substack.com/redirect/2/abc">'
        '<img src="https://substackcdn.com/icon/notes__NoteRestackIcon" /></a></td></tr>'
        '<tr><td>READ IN APP'
        '<a href="https://open.substack.com/pub/x/p/y">'
        '<img src="https://substackcdn.com/icon/LucideArrowUpRight" /></a>'
        '</td></tr>'
        '</table>'
        '<p>Hello, this is the article body.</p>'
    )
    md = html_to_markdown(html)
    assert "Hello, this is the article body." in md
    # Every icon URL and the "READ IN APP" label are gone.
    assert "LucideHeart" not in md
    assert "LucideComments" not in md
    assert "LucideShare2" not in md
    assert "notes__NoteRestackIcon" not in md
    assert "READ IN APP" not in md
    assert "app-link/post" not in md


# --- Rule 6: unwrap redirect-anchor around content images --------------------


def test_redirect_wrapped_content_image_keeps_image_drops_anchor():
    html = _wrap(
        '<p>Intro.</p>'
        '<a href="https://substack.com/redirect/abc">'
        '<img src="https://substack-post-media.s3.amazonaws.com/images/hero.png" alt="hero"/>'
        '</a>'
        '<p>Outro.</p>'
    )
    md = html_to_markdown(html)
    assert "substack-post-media.s3.amazonaws.com/images/hero.png" in md
    # The wrapping redirect URL must not appear as link text.
    assert "substack.com/redirect" not in md


# --- Rule 7: footer chrome ---------------------------------------------------


def test_footer_unsubscribe_and_copyright_dropped():
    html = _wrap(
        # Realistic emails always carry a Substack tracking pixel — including
        # one here is also what activates the cleaner under the tightened
        # template-specific detection.
        '<img src="https://eotrx.substackcdn.com/o/abc/p.gif" />'
        '<p>Last real paragraph.</p>'
        '<table><tr><td>'
        '© 2026 Substack Inc. '
        '<a href="https://substack.com/account/unsubscribe?token=xyz">Unsubscribe</a>'
        '</td></tr></table>'
    )
    md = html_to_markdown(html)
    assert "Last real paragraph." in md
    assert "© 2026 Substack Inc." not in md
    assert "Unsubscribe" not in md


# --- Pass-through: non-Substack email is untouched ---------------------------


def test_non_substack_email_passes_through_unchanged():
    # No substackcdn.com / substack.com / "Forwarded this email?" markers.
    html = (
        '<html><body>'
        '<h1>Lenny Newsletter</h1>'
        '<p>Subscribe at <a href="https://lennys.com">lennys.com</a></p>'
        '<p>Body content here.</p>'
        '</body></html>'
    )
    md = html_to_markdown(html)
    # All three pieces survive — the cleaner did not activate.
    assert "Lenny Newsletter" in md
    assert "Subscribe at" in md
    assert "Body content here." in md


def test_generic_substack_link_does_not_activate_cleaner():
    # A non-Substack email that merely *mentions* substack.com (e.g. a "We're
    # also on Substack" link) must NOT be cleaned — otherwise we'd strip the
    # other newsletter's footer / unsubscribe block as if it were Substack's.
    html = _wrap(
        '<h1>Other Newsletter</h1>'
        '<p>Body content.</p>'
        '<table><tr><td>Unsubscribe from this newsletter</td></tr></table>'
        '<p>Also follow us on <a href="https://www.substack.com/">Substack</a>.</p>'
    )
    md = html_to_markdown(html)
    # Footer-shaped block survives because the cleaner never activated:
    # generic www.substack.com is not a template-specific signal.
    assert "Other Newsletter" in md
    assert "Body content." in md
    assert "Unsubscribe from this newsletter" in md


def test_footer_match_is_case_insensitive():
    # If Substack ships SHOUTY or lowercased footer text, the cleaner should
    # still strip it. Detection uses casefold internally.
    html = _wrap(
        '<p>Real article body.</p>'
        '<table><tr><td>'
        '© 2026 SUBSTACK INC. <a href="https://substack.com/app-link/post?id=1">UNSUBSCRIBE</a>'
        '</td></tr></table>'
    )
    md = html_to_markdown(html)
    assert "Real article body." in md
    assert "SUBSTACK INC" not in md
    assert "UNSUBSCRIBE" not in md
