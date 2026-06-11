# deliver

Run the Newsletter-to-Kindle pipeline for a date window and report results.

## Usage

```
/deliver                          # yesterday → today (default)
/deliver 2026-06-09               # single day
/deliver 2026-06-08 2026-06-10    # explicit range
```

## What to do

1. Parse the arguments (if any):
   - 0 args → START = yesterday, END = today
   - 1 arg  → START = that date, END = that date
   - 2 args → START = first arg, END = second arg
   - Dates must be YYYY-MM-DD

2. Run the pipeline:
```bash
cd /Users/tfong/development/substack-kindle
set -a && source .env && set +a
uv run substack-kindle --start <START> --end <END>
```

3. Report back:
   - The output line: `trigger=... status=... outcome=... delivered=N`
   - If status != succeeded, show the error and stop
   - If delivered=0, note that all newsletters in the window were already delivered (dedup hit) or none matched the approved senders list
