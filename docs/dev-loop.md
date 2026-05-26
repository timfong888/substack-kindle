# Development loop & merge gate (SAT-258)

The coding agent works the backlog in a layered loop. A story's PR reaches
`main` only past a satisfied **merge gate**. The gate logic lives in
`src/substack_kindle/merge_gate.py` (`decide` / `is_mergeable` / `gate_blockers`)
and is a pure predicate — it decides, it never pushes.

## Gate conditions

A PR is mergeable only when ALL hold for the **current HEAD commit**:

1. **CI passed** — `lint + test` green on HEAD.
2. **AI review of HEAD complete** — Greptile and CodeRabbit have reviewed the
   current HEAD sha (last-reviewed sha == HEAD sha), not merely "at some point".
3. **No open blocking findings** — zero unresolved Greptile P1/P2 **and** zero
   CodeRabbit Critical/Warning findings on HEAD.
4. **>= 1 approval.**

Otherwise the story is **FLAGged**, never force-merged.

## Reviewers

Three AI reviewers run on each PR; all feed conditions 2–3 above:

- **Greptile** (GitHub App) — posts a review on each PR/commit. The agent reads
  findings via the Greptile API/MCP and fixes them (see
  [[feedback_greptile_fix_via_mcp]] — never the "Fix with your Agent" Bridge).
- **CodeRabbit** (CLI) — the agent runs `coderabbit review --plain --base main`
  (alias `cr`) on the PR's HEAD and triages findings by severity
  (Critical / Warning / Info). Critical/Warning must be resolved before merge.
- **Sourcery** (GitHub App) — secondary reviewer; advisory.

**Important:** all three review the **diff**, so they do NOT catch pre-existing
bugs in unchanged code (e.g. SAT-261's content-loss was found by a real-data
characterization test, not by any reviewer). Keep characterization tests for
behavior the diff-based reviewers can't see.

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
2. If HEAD changed since the last review, **re-review on HEAD**: trigger
   Greptile's re-review and run `coderabbit review --plain --base main` against
   the latest commit; record the reviewed sha → `greptile_reviewed_sha`.
3. Count unresolved blocking findings on HEAD (Greptile P1/P2 + CodeRabbit
   Critical/Warning) → `greptile_open_blocking_findings`.
4. Read CI + approvals, then `decide(...)`. Never merge within seconds of a
   push without confirming the above.

## Hard enforcement (follow-up)

This is loop-level (procedural) enforcement. Hard, GitHub-level enforcement —
requiring Greptile's status check on `main` with "branches must be up to date"
so GitHub itself blocks a stale-review merge — is tracked in **SAT-259** and is
pending the Greptile status-check / account setup.
