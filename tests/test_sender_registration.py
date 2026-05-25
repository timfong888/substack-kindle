"""Tests for sender registration via the Gmail label gesture (SAT-242 / #6).

Acceptance:
- Reads labelled messages and extracts the true `From` header (no body parsing).
- That sender is added to approved_sources.
- Label is never removed (read-only) and never used to mean "processed".
- A sender already in approved_sources is not duplicated.
"""

from substack_kindle.sender_registration import (
    EmailMessage,
    register_senders_from_label,
    sender_of,
)

LABEL = "Newsletters"


class FakeReadOnlyGmail:
    """A read-only Gmail double. Mutating operations raise — proving we never call them."""

    def __init__(self, messages):
        self._messages = messages
        self.read_calls = 0

    def messages_with_label(self, label):
        self.read_calls += 1
        return [m for m in self._messages if label in m.label_ids]

    # Any of these being called would violate the read-only contract.
    def add_label(self, *a, **k):  # pragma: no cover - must never run
        raise AssertionError("mutating Gmail call: add_label")

    def remove_label(self, *a, **k):  # pragma: no cover
        raise AssertionError("mutating Gmail call: remove_label")

    def archive(self, *a, **k):  # pragma: no cover
        raise AssertionError("mutating Gmail call: archive")

    def delete(self, *a, **k):  # pragma: no cover
        raise AssertionError("mutating Gmail call: delete")


def _msg(mid, from_header, labels=(LABEL,)):
    return EmailMessage(message_id=mid, headers={"From": from_header}, label_ids=list(labels))


def test_sender_of_extracts_email_from_display_name():
    msg = _msg("1", "Jane Doe <jane@news.example>")
    assert sender_of(msg) == "jane@news.example"


def test_sender_of_handles_bare_address():
    assert sender_of(_msg("1", "bare@news.example")) == "bare@news.example"


def test_sender_of_is_case_insensitive_on_header_name():
    msg = EmailMessage("1", {"from": "Jane <jane@news.example>"}, [LABEL])
    assert sender_of(msg) == "jane@news.example"


def test_registers_senders_from_labelled_messages():
    gmail = FakeReadOnlyGmail([
        _msg("1", "Alice <alice@a.example>"),
        _msg("2", "Bob <bob@b.example>"),
    ])
    approved = register_senders_from_label(gmail, LABEL)
    assert approved == ["alice@a.example", "bob@b.example"]


def test_ignores_messages_without_the_label():
    gmail = FakeReadOnlyGmail([
        _msg("1", "Alice <alice@a.example>", labels=[LABEL]),
        _msg("2", "Other <other@x.example>", labels=["Inbox"]),
    ])
    approved = register_senders_from_label(gmail, LABEL)
    assert approved == ["alice@a.example"]


def test_existing_sender_is_not_duplicated():
    gmail = FakeReadOnlyGmail([_msg("1", "Alice <alice@a.example>")])
    approved = register_senders_from_label(gmail, LABEL, approved_sources=["alice@a.example"])
    assert approved == ["alice@a.example"]


def test_duplicate_within_batch_is_collapsed():
    gmail = FakeReadOnlyGmail([
        _msg("1", "Alice <alice@a.example>"),
        _msg("2", "Alice (2nd issue) <alice@a.example>"),
    ])
    approved = register_senders_from_label(gmail, LABEL)
    assert approved == ["alice@a.example"]


def test_sender_match_is_case_insensitive():
    gmail = FakeReadOnlyGmail([_msg("1", "Alice <Alice@A.Example>")])
    approved = register_senders_from_label(gmail, LABEL, approved_sources=["alice@a.example"])
    assert approved == ["alice@a.example"]


def test_no_mutating_gmail_call_and_labels_unchanged():
    messages = [_msg("1", "Alice <alice@a.example>")]
    gmail = FakeReadOnlyGmail(messages)
    register_senders_from_label(gmail, LABEL)  # would raise if any mutator were called
    # The label is still on the message — it is never removed or repurposed as "processed".
    assert messages[0].label_ids == [LABEL]


def test_does_not_parse_body():
    # Only the From header is read; a sender hint hidden in the body must be ignored.
    msg = EmailMessage(
        "1",
        {"From": "Real <real@news.example>"},
        [LABEL],
    )
    msg.body = "From: spoof@evil.example"  # arbitrary extra attribute; must be ignored
    gmail = FakeReadOnlyGmail([msg])
    approved = register_senders_from_label(gmail, LABEL)
    assert approved == ["real@news.example"]


def test_input_approved_list_not_mutated():
    gmail = FakeReadOnlyGmail([_msg("1", "Bob <bob@b.example>")])
    original = ["alice@a.example"]
    result = register_senders_from_label(gmail, LABEL, approved_sources=original)
    assert original == ["alice@a.example"]  # caller's list untouched
    assert result == ["alice@a.example", "bob@b.example"]


def test_message_missing_from_header_is_skipped():
    msg = EmailMessage("1", {}, [LABEL])
    gmail = FakeReadOnlyGmail([msg])
    assert register_senders_from_label(gmail, LABEL) == []


def test_bare_display_name_without_address_is_ignored():
    # parseaddr("Newsletter") -> ('', 'Newsletter'); not a real address.
    assert sender_of(_msg("1", "Newsletter")) is None
    gmail = FakeReadOnlyGmail([_msg("1", "Newsletter")])
    assert register_senders_from_label(gmail, LABEL) == []


def test_preexisting_entries_are_normalized_lowercase():
    gmail = FakeReadOnlyGmail([_msg("1", "Bob <bob@b.example>")])
    result = register_senders_from_label(gmail, LABEL, approved_sources=["Alice@A.Example"])
    assert result == ["alice@a.example", "bob@b.example"]
