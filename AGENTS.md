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
- [x] Push `feat/one-shot-implementation`, open PR #1, and verify Python, TypeScript, and image CI jobs. No CD was added.

## Working rules

- Keep fetched evidence private; public artifacts contain annotations and resolved source links only.
- Model output is typed data, never HTML.
- Deterministic validation is a hard publication gate.
- Tests and CI use a fake runtime and fixture feeds and incur no model cost.
- CD is intentionally out of scope until the owner returns.
