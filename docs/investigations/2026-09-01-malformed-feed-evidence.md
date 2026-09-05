# 2026-09-01: malformed feed evidence caused producer retries

## Report

Run `6ad8512eca2e4518adc54a7214a276f3` collected 126 valid evidence objects but recorded 156 diagnostics during its first producer prompt, then retried the producer's terminating output.

## Root cause

The extension passed RSS/Atom `pubDate` values through directly into `Evidence.published_at`. Some feeds provide non-ISO or non-string date values. Python's strict evidence schema rejected those records, while the producer had still seen their IDs. This could make its submission refer to evidence absent from the workflow ledger and trigger a malformed-output correction.

The run was then interrupted by the scheduled-agent redeployment at 06:21 UTC; it was not a model-process crash.

## Fix

The source extension now normalizes every feed date to an ISO-8601 timestamp or `null` before emitting evidence. Prompt-settlement logs also include a safe diagnostic-count breakdown, so a future malformed-evidence source is immediately identifiable without logging source content.

## Verification

Added a source-tool test covering an RFC-822 RSS date, invalid date text, and a non-string date value.
