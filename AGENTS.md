# Implementation Notes

This repository implements the design in `docs/detailed-design.md`.

## Progress

- [x] Read the high-level and detailed designs.
- [x] Confirmed the target architecture: Python/LangGraph agent, constrained Pi RPC sessions, Bun static server, local/S3 storage, two images, cost-free CI.
- [x] Scaffold typed configuration, schemas, local/S3 storage, renderer, hard validation, atomic publication, retention, locking, and the LangGraph workflow.
- [x] Implement bounded SSRF-aware source tools and role-specific terminating Pi tools.
- [x] Implement the read-only Bun server with pointer/hash verification, cache policy, and security headers.
- [x] Add deterministic fixtures, Python and Bun tests, static checks, and cost-free CI.
- [x] Resolve lint/type/test findings; Python tests, Python lint/types, TypeScript types, Bun tests, fixture publication, live HTTP smoke test, and dependency audit pass locally.
- [x] Build both Docker images in GitHub CI (local Docker Hub pulls timed out, but the remote Buildx job passed).
- [x] Merge PR #1 into `main`; verify Python, TypeScript, and both image-build jobs on `main`.
- [x] Add a production-only CD workflow modeled on `fold.link`, without exposing credentials.
- [x] Provision exactly one Railway project, two services, and one private bucket; avoid staging and databases.
- [ ] Verify the first CD run for the configured web service and daily scheduled agent.
- [x] Diagnose production: Railway and the web container are healthy, but the bucket has zero objects; the scheduled agent is intentionally stopped until its first cron run at 06:00 UTC. The web app also needs Bun S3 virtual-host configuration for Railway's bucket endpoint.
- [x] Deploy the S3 fix and point CD smoke tests at `https://matteodelseppia.xyz`; no temporary high-frequency cron schedule is enabled.
- [x] Fix installed-image resource paths (`XYZ_REPOSITORY_ROOT=/app`) and enlarge bounded Pi JSONL framing for evidence events.
- [x] Perform one bounded production agent run after the new agent image is deployed; it produced the initial immutable publication in the private bucket without changing the cron schedule.
- [x] Deploy the final virtual-hosted S3 hostname fix; the web service now reads Railway Object Storage through the bucket hostname.
- [x] Verify `https://matteodelseppia.xyz/health` and `/` return `200`; the current pointer, calendar, manifest, HTML, CSS, and publication metadata are readable.
- [x] Reproduce the production workflow locally with Railway-injected variables: Pi RPC startup and a complete producer/reviewer/publication run succeeded.
- [x] Deploy bounded, redacted Pi stderr diagnostics so future remote child-process exits expose the startup failure without credentials or source bodies.
- [x] Trigger the existing remote cron execution and identify the root cause: Node.js 22.14.0 lacks `zlib.createZstdDecompress`, which Pi calls while handling the provider response.
- [x] Upgrade the agent image to Node.js 22.15.0+, rebuild, and verify a remote cron execution succeeds.
- [x] Require ten-item publications (or an evidence-supported empty digest), strengthen adversarial review, and add deterministic source-diversity validation.

## Working rules

- Keep fetched evidence private; public artifacts contain annotations and resolved source links only.
- Model output is typed data, never HTML.
- Deterministic validation is a hard publication gate.
- Tests and CI use a fake runtime and fixture feeds and incur no model cost.
- CD deploys only after successful `main` CI and uses an environment-scoped Railway project token.
