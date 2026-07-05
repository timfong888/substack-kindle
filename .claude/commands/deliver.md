# deliver

Run the Newsletter-to-Kindle pipeline for a date window and report results.

## Usage

```text
/deliver                          # yesterday → today (default)
/deliver 2026-06-09               # single day
/deliver 2026-06-08 2026-06-10    # explicit range
```

## What to do

1. Parse the `/deliver` arguments (if any) to compute concrete START/END
   dates. These are resolved here, in this slash-command layer — the
   underlying `substack-kindle` CLI's `--start`/`--end` flags are both
   required and take no defaults (see `src/substack_kindle/cli.py`), so
   this step must always produce two concrete dates before invoking it:
   - 0 args → START = yesterday, END = today
   - 1 arg  → START = that date, END = that date
   - 2 args → START = first arg, END = second arg
   - Dates must be YYYY-MM-DD

2. Run the pipeline:
```bash
cd <project-root>   # the repository root where you cloned substack-kindle
set -a && source .env && set +a
uv run substack-kindle --start <START> --end <END>
```

3. Report back:
   - The output line is prefixed `substack-kindle: ` and has the form
     `substack-kindle: trigger=... status=... outcome=... delivered=N`
   - `status` is `succeeded` or `failed`.
   - `outcome` is `delivered` (EPUB sent), `empty` (no newsletters left after
     dedup — nothing to send), or `error` (see `src/substack_kindle/pipeline.py`).
   - If status != succeeded, show the error and stop
   - If delivered=0, note that all newsletters in the window were already delivered (dedup hit) or none matched the approved senders list
