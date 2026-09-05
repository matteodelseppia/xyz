# 2026-09-01: unresolved evidence at iteration limit

## Report

Production `xyz-agent` run `0a3ec4da377d4166b4255c1db08a77ec` failed on 2026-09-01 with `unresolved evidence at iteration limit`.

## Facts

- Failure is from `Workflow._render` when a publication update references an ID absent from the orchestration evidence ledger.
- The run had reached the configured generation iteration limit, so rendering cannot request a correction.
- The scheduled agent deployment built successfully; this is a workflow/data-integrity failure rather than a container startup failure.

## Root cause

The producer's terminating schema accepted arbitrary evidence-ID strings. A model could therefore submit an otherwise schema-valid draft containing a fabricated or stale ID. The workflow treated it as a candidate and spent a generation iteration on review. On the final iteration, the deterministic render gate correctly refused to publish it.

The deployment and Pi runtime were healthy. Two prior runs using the same deployment published successfully; this is a bounded model-output integrity failure, not a container startup failure.

## Fix

- The Pi `submit_publication` tool now rejects references not created during its live source-tool session.
- The workflow independently treats unresolved references as malformed producer output, retries without incrementing the generation-iteration budget, and supplies the captured IDs for correction.
- If an invariant is bypassed, terminal diagnostics include up to 12 missing IDs without exposing source bodies.

## Verification

- Added a Python workflow regression test: an invalid evidence reference is corrected and published with `max_iterations=1`.
- Added a Bun unit test for rejecting unknown evidence IDs while preserving known ones.
- Passed focused Python tests, Ruff, mypy, TypeScript checks, and Pi-extension tests.

## Impact

Publication contracts are unchanged. The agent now rejects invalid evidence references at the producer boundary rather than consuming its review/validation budget and failing at render time.
