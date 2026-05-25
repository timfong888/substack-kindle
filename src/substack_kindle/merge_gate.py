"""Merge-gate decision logic (SAT-258 / PRD §Development Loop).

Encodes the rule the development loop enforces before a story's PR reaches
``main``: the gate is satisfied only when CI passes, the Greptile review is
complete, and there is at least one approval. The loop merges only past a
satisfied gate; otherwise it FLAGs the story for attention. There is no
force-push path here by design — this module decides, it never pushes.
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

    def __post_init__(self) -> None:
        if self.approvals < 0:
            raise ValueError(f"approvals must be >= 0, got {self.approvals}")


def gate_blockers(status: GateStatus, *, required_approvals: int = 1) -> list[str]:
    """Return the unmet gate conditions (empty list means the gate is satisfied)."""
    blockers: list[str] = []
    if not status.ci_passed:
        blockers.append("CI (lint + test) has not passed")
    if not status.greptile_complete:
        blockers.append("Greptile review is not complete")
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
