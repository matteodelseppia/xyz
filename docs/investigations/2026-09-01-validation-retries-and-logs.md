# 2026-09-01: validation failures were opaque and exhausted the draft budget

## Report

Production run `ebc2d6ef85df499287bbc249c5c8179e` failed with two deterministic-validation findings, but the previous logs only contained container start, a finding count, and terminal status.

## Root cause

The workflow shared one draft-iteration budget between reviewer and validation corrections. A validation finding at the draft limit failed the run without a correction attempt. It also emitted no phase, duration, evidence-count, tool-count, or validation-rule diagnostics.

The historical log record contains only the finding count, so the specific two failed rules cannot be reconstructed safely after the run.

## Fix

- Deterministic validation now has a separate bounded retry budget (`XYZ_MAX_VALIDATION_RETRIES`, default `2`) and does not consume a generation iteration.
- Producer, reviewer, render, validation, publication, session, and terminal lifecycle events are now structured logs with run ID, phase duration, iteration and validation-retry counts, update/evidence counts, tool counts, approval/finding counts, and validation rule IDs.
- Logs intentionally omit fetched bodies, prompts, findings, credentials, and model output text.

## Verification

A regression test proves a validation correction succeeds with `max_iterations=1`; a separate test preserves failure when validation retries are explicitly zero.
