"""Merge-gate decision logic (SAT-258 / PRD §Development Loop).

Encodes the rule the development loop enforces before a story's PR reaches
``main``: the gate is satisfied only when CI passes, Greptile has reviewed the
**current HEAD commit** with no unresolved P1/P2 findings, and there is at least
one approval. The loop merges only past a satisfied gate; otherwise it FLAGs the
story for attention. There is no force-push path here by design — this module
decides, it never pushes.

IMPORTANT — the loop's responsibility: this module is a pure predicate; it is
only as strong as the data the loop feeds it. Before calling ``decide``/
``is_mergeable`` the loop MUST, on the PR's *latest* commit:
  1. read the current HEAD sha -> ``head_sha``
  2. re-fetch Greptile for the sha it last reviewed -> ``greptile_reviewed_sha``
     (trigger a re-review if HEAD changed since the last one)
  3. count unresolved P1/P2 comments on HEAD -> ``greptile_open_blocking_findings``
Passing stale or HEAD-agnostic values reintroduces the PR #39 failure mode (a
fix commit merged seconds after it landed, before Greptile re-reviewed).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class Decision(enum.Enum):
    MERGE = "merge"
    FLAG = "flag"


@dataclass
class GateStatus:
    ci_passed: bool
    greptile_complete: bool
    approvals: int
    # Fresh-review hardening (optional; defaults preserve the prior behavior).
    # ``head_sha`` is the PR's current HEAD; ``greptile_reviewed_sha`` is the
    # commit Greptile last reviewed. When ``head_sha`` is provided, the gate
    # requires Greptile to have reviewed *that* commit — not merely "at some
    # point" — so a fix pushed after the last review cannot merge unreviewed.
    head_sha: str | None = None
    greptile_reviewed_sha: str | None = None
    # Count of unresolved blocking (P1/P2) Greptile findings on the current HEAD.
    greptile_open_blocking_findings: int = 0

    def __post_init__(self) -> None:
        if self.approvals < 0:
            raise ValueError(f"approvals must be >= 0, got {self.approvals}")
        if self.greptile_open_blocking_findings < 0:
            raise ValueError(
                "greptile_open_blocking_findings must be >= 0, "
                f"got {self.greptile_open_blocking_findings}"
            )


def gate_blockers(status: GateStatus, *, required_approvals: int = 1) -> list[str]:
    """Return the unmet gate conditions (empty list means the gate is satisfied)."""
    blockers: list[str] = []
    if not status.ci_passed:
        blockers.append("CI (lint + test) has not passed")
    if not status.greptile_complete:
        blockers.append("Greptile review is not complete")
    # When the current HEAD is known, Greptile must have reviewed exactly that
    # commit. A mismatch (or no review yet) means the latest changes are
    # unreviewed — the PR #39 failure mode where a fix merged before re-review.
    if status.head_sha is not None and status.greptile_reviewed_sha != status.head_sha:
        blockers.append(
            "Greptile review is stale: reviewed "
            f"{status.greptile_reviewed_sha or 'nothing'}, HEAD is {status.head_sha}"
        )
    if status.greptile_open_blocking_findings > 0:
        blockers.append(
            f"{status.greptile_open_blocking_findings} unresolved Greptile "
            "P1/P2 finding(s)"
        )
    if status.approvals < required_approvals:
        blockers.append(
            f"needs {required_approvals} approval(s), has {status.approvals}"
        )
    return blockers


def is_mergeable(status: GateStatus, *, required_approvals: int = 1) -> bool:
    """True only when every gate condition is satisfied."""
    return not gate_blockers(status, required_approvals=required_approvals)


def decide(status: GateStatus, *, required_approvals: int = 1) -> Decision:
    """Merge past a satisfied gate; otherwise flag the story (never force-push)."""
    if is_mergeable(status, required_approvals=required_approvals):
        return Decision.MERGE
    return Decision.FLAG
