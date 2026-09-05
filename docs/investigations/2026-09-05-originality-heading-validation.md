# Originality validation serializes simultaneous corrections

## Context

The production agent must use the exact primary-evidence title as each linked update heading, while its original annotations must not substantially overlap fetched evidence. Deterministic validation allows only a bounded number of correction turns.

## Observed failure

Production run `dc2ecf18a38447c18673c605f151b953` used deployment `fa764883-1e08-48d6-a47d-a594c4520f09` at revision `c5b82ed1be1b7154b6d2830f28db0562b85637c8`. The reviewer approved each corrected ten-item candidate, but originality validation returned exactly one finding on the initial pass and on all three correction passes. The run then failed without publishing.

That revision already excluded required linked headings from originality matching, so heading comparison was not the remaining failure. The repeated `Pi agent ended with an error` diagnostics did not abort any prompt: each prompt returned a valid terminal artifact and settled successfully.

Finding bodies and failed candidates are intentionally absent from production logs, so the historical record cannot prove whether each pass matched a different field. The validator's one-finding cap is deterministic and exactly accounts for the observed serialized-failure pattern.

## Root cause

The validator concatenated every generated field and stopped after the first evidence item with an eight-word match. It therefore returned at most one originality finding per pass, even when several updates needed independent corrections. A producer could fix every disclosed match and still exhaust the bounded retry budget as the validator revealed the next previously hidden match on each pass.

The concatenation also removed field boundaries, made feedback identify only a phrase rather than its update and field, and could report a synthetic match spanning unrelated generated fields.

## Fix

Build an index of eight-word windows for each generated prose field, scan each fetched evidence body once, and collect every matching component before returning. Group matched fields into at most one actionable finding per publication or update. Continue excluding exact linked article headings while checking the publication title, descriptions, reading rationales, and verification questions.

## Verification

`test_validator_reports_every_overlapping_update_in_one_pass` reproduced the defect: two independently copied update fields produced only one finding before the fix. It now verifies that both update fields are reported together.

Verified locally:

- Python formatting and linting pass with Ruff.
- Strict MyPy checking passes for `agent/src`.
- All 29 Python tests pass.
- The deterministic fake-runtime publication and private-evidence scan pass.
- TypeScript checks pass for the Pi extension and web service.
- All 17 Bun tests pass.

A real provider run has not been repeated; production verification requires deploying the fix and observing a scheduled or explicitly triggered agent run.
