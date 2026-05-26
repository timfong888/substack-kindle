"""Tests for the merge-gate decision logic (SAT-258 / #22, PRD §Development Loop).

Acceptance:
- The merge gate is enforced: Greptile review complete + CI pass + >= 1 approval.
- The outer loop only merges past a satisfied gate; otherwise the story is
  flagged (never force-pushed).
"""

import pytest

import substack_kindle.merge_gate as merge_gate_mod
from substack_kindle.merge_gate import (
    Decision,
    GateStatus,
    decide,
    gate_blockers,
    is_mergeable,
)


def _ready(**overrides):
    base = dict(ci_passed=True, greptile_complete=True, approvals=1)
    base.update(overrides)
    return GateStatus(**base)


def test_satisfied_gate_is_mergeable():
    status = _ready()
    assert is_mergeable(status) is True
    assert gate_blockers(status) == []
    assert decide(status) is Decision.MERGE


def test_failing_ci_blocks_merge():
    status = _ready(ci_passed=False)
    assert is_mergeable(status) is False
    assert decide(status) is Decision.FLAG
    assert any("ci" in b.lower() for b in gate_blockers(status))


def test_incomplete_greptile_blocks_merge():
    status = _ready(greptile_complete=False)
    assert is_mergeable(status) is False
    assert any("greptile" in b.lower() for b in gate_blockers(status))


def test_missing_approval_blocks_merge():
    status = _ready(approvals=0)
    assert is_mergeable(status) is False
    assert any("approv" in b.lower() for b in gate_blockers(status))


def test_required_approvals_is_configurable():
    status = _ready(approvals=1)
    assert is_mergeable(status, required_approvals=2) is False
    assert is_mergeable(_ready(approvals=2), required_approvals=2) is True


def test_blockers_list_all_unmet_conditions():
    status = GateStatus(ci_passed=False, greptile_complete=False, approvals=0)
    blockers = gate_blockers(status)
    assert len(blockers) == 3


def test_incomplete_story_is_flagged_not_force_pushed():
    # The decision is only ever MERGE or FLAG — there is no force-push path.
    status = _ready(ci_passed=False)
    assert decide(status) is Decision.FLAG
    assert set(Decision) == {Decision.MERGE, Decision.FLAG}


def test_module_has_no_force_push_capability():
    import inspect

    # inspect.getsource returns the .py text even in bytecode-only installs.
    source = inspect.getsource(merge_gate_mod).lower()
    # No git/push side effects: the gate is a pure decision function, never an actor.
    assert "subprocess" not in source
    assert "git push" not in source
    assert "force_push" not in source
    assert "--force" not in source


def test_negative_approvals_rejected():
    with pytest.raises(ValueError, match="approvals must be"):
        GateStatus(ci_passed=True, greptile_complete=True, approvals=-1)


# --- Fresh-review hardening (prevents the PR #39 case: a fix commit merged
# --- before Greptile re-reviewed the new HEAD). ---


def test_fresh_review_on_head_is_mergeable():
    status = _ready(head_sha="abc123", greptile_reviewed_sha="abc123")
    assert is_mergeable(status) is True
    assert gate_blockers(status) == []


def test_stale_review_blocks_merge():
    # Greptile reviewed an earlier commit; a newer commit is now HEAD.
    status = _ready(head_sha="def456", greptile_reviewed_sha="abc123")
    assert is_mergeable(status) is False
    assert decide(status) is Decision.FLAG
    assert any("stale" in b.lower() for b in gate_blockers(status))


def test_head_never_reviewed_blocks_merge():
    # HEAD is known but Greptile has not reviewed any SHA yet.
    status = _ready(head_sha="def456", greptile_reviewed_sha=None)
    assert is_mergeable(status) is False
    assert any("stale" in b.lower() for b in gate_blockers(status))


def test_open_blocking_findings_block_merge():
    status = _ready(
        head_sha="abc123",
        greptile_reviewed_sha="abc123",
        greptile_open_blocking_findings=2,
    )
    assert is_mergeable(status) is False
    assert any("finding" in b.lower() for b in gate_blockers(status))


def test_negative_findings_rejected():
    with pytest.raises(ValueError, match="findings must be"):
        GateStatus(
            ci_passed=True,
            greptile_complete=True,
            approvals=1,
            greptile_open_blocking_findings=-1,
        )
