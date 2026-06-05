# Shared Linear Gateway Intake Protocol

Gateway-owned narrative source work is tracked in the shared Linear project
`Fund Narrative Intelligence`. Gateway developers should use Linear as the
source of truth rather than chat memory.

## Intake Filter

Open Linear and filter:

- Project = `Fund Narrative Intelligence`
- Milestone = `M20 - Open Narrative Source Gateway Capability`
- Label = Gateway
- Title contains `[GATEWAY]`

The current foundation sequence is:

- MIK-276: shared Linear requirement intake and execution protocol
- MIK-273: open source crawl governance runtime
- MIK-277: unified source-event query API
- MIK-278: source event dedupe and freshness ledger

## Ownership Boundary

Gateway owns external data acquisition, crawler/runtime/cache/provider logic,
source-event normalization, source governance, and provider degradation
semantics.

FNI remains a consumer only. Gateway work must not add FNI-side source
acquisition, crawler adapters, paid-provider integration, browser automation,
proxy rotation, CAPTCHA bypass, or anti-detect infrastructure.

## Execution Notes

For each completed Gateway-owned Linear issue, add a Linear comment with:

- Linear issue id and implementation scope
- Changed endpoints and source-event contract changes
- Validation commands and results
- Degraded/fallback behavior
- Live-provider caveats
- Commit hash or local change summary when a commit is not available yet

Keep external source access behind Gateway endpoints and source-event contracts.
FNI should not need provider-specific upstream code to consume narrative sources.
