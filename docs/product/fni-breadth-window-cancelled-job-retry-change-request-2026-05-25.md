# FNI Breadth Window Cancelled Job Retry Change Request - 2026-05-25

Status: active request

Upstream: `fund-narrative-intelligence` (`FNI`)

Consumer workspace:

```text
/Users/mikezhang/Coding/AI-Learning/fund-narrative-intelligence
```

Supersedes follow-up work from:

```text
docs/product/archive/fni-breadth-scale-job-ops-change-request-2026-05-25.md
```

## Summary

FNI has integrated the gateway `breadth-window` async job API into its breadth scanner.

Passing evidence:

- 2-symbol, 2-trading-day breadth scanner completed through `breadth_window`.
- 50-symbol, 20-trading-day breadth scanner completed through `breadth_window`.
- FNI no longer falls back from terminal cancelled/failed/interrupted breadth-window jobs to heavy daily-bars pulls.

New blocker:

When FNI retries the same 500-symbol, 20-trading-day breadth-window request that was previously cancelled, gateway returns the old cancelled semantic job. FNI now fails fast with:

```text
gateway breadth-window job ended with cancelled: none
```

This is safer than falling back to daily-bars, but it prevents a legitimate retry after cancellation.

## Required Behavior

Gateway should support retrying a semantic request whose previous job ended as `cancelled`, `failed`, or `interrupted`.

Acceptable designs:

1. Recreate a new job when the existing semantic job is terminal and not successful.
2. Resume compatible partial work from the previous job.
3. Add an explicit request option, for example `force_new: true`, `rerun: true`, or `force_refresh: true`, that creates a new job id while preserving cache.

FNI prefers option 1 or 2 for normal consumer ergonomics. Option 3 is acceptable if documented and exposed consistently for `daily-bars` and `breadth-window`.

## Acceptance Checks

1. Create a breadth-window job.
2. Cancel it after partial rows exist.
3. Submit the same semantic request again.
4. Gateway should not simply return the old `cancelled` job as the active result.
5. The second request should either:
   - return a new accepted/running/completed job, or
   - resume the cancelled job into a non-cancelled active or completed state.
6. Partial rows/cache from the cancelled job should remain available.
7. `/api/health` remains HTTP `200`.

## Non-Goals

Do not add browser automation, proxy rotation, anti-detect, CAPTCHA bypass, trading strategy, or AI prediction logic.
