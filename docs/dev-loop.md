# Development loop & merge gate (SAT-258)

The coding agent works the backlog in a layered loop. A story's PR reaches
`main` only past a satisfied **merge gate**. The gate logic lives in
`src/substack_kindle/merge_gate.py` (`decide` / `is_mergeable` / `gate_blockers`)
and is a pure predicate — it decides, it never pushes.

## Gate conditions

A PR is mergeable only when ALL hold for the **current HEAD commit**:

1. **CI passed** — `lint + test` green on HEAD.
2. **Greptile reviewed HEAD** — Greptile's last-reviewed sha == HEAD sha.
3. **No open P1/P2 findings** — zero unresolved blocking Greptile comments.
4. **>= 1 approval.**

Otherwise the story is **FLAGged**, never force-merged.

## Why "reviewed HEAD", not "reviewed at all"

PR #39 (F1 Onboarding) exposed the gap a coarse "Greptile complete?" boolean
leaves open:

- Greptile review #1 (commit `1a42b7c`) flagged P1s; the agent pushed a fix
  (`64d0a7a`).
- Greptile **re-reviewed** `64d0a7a` and found a *new* P1 (the fix stored an
  unstripped `kindle_email`).
- The agent pushed the fix (`e852523`) and the PR **merged 37 seconds later —
  before Greptile reviewed `e852523`**.

The final commit reached `main` unreviewed. It happened to be correct, but the
process allowed an unreviewed merge — and review #1's fix had *introduced* the
bug review #2 caught, which is exactly the case where re-review matters.

## Loop responsibility (required before deciding)

`merge_gate` is only as strong as the data it's given. Before calling `decide`,
the loop MUST, against the PR's **latest** commit:

1. Read HEAD sha → `head_sha`.
2. If HEAD changed since Greptile's last review, **trigger a re-review** and
   wait for it; record the reviewed sha → `greptile_reviewed_sha`.
3. Count unresolved P1/P2 comments on HEAD → `greptile_open_blocking_findings`.
4. Read CI + approvals, then `decide(...)`. Never merge within seconds of a
   push without confirming the above.

## Hard enforcement (follow-up)

This is loop-level (procedural) enforcement. Hard, GitHub-level enforcement —
requiring Greptile's status check on `main` with "branches must be up to date"
so GitHub itself blocks a stale-review merge — is tracked in **SAT-259** and is
pending the Greptile status-check / account setup.
