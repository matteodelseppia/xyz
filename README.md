# xyz

`matteodelseppia.xyz` publishes one brief daily digest from a versioned set of feeds. A Python LangGraph job coordinates isolated producer and reviewer Pi sessions, validates typed output, and publishes immutable artifacts. A read-only Bun service serves the current and seven most recent successful days.

**Owner:** `matteodelseppia`

- [Detailed design](docs/detailed-design.md)
- [High-level design](docs/high-level-design.md)

## Tutorial: run a cost-free local publication

### Prerequisites

- Python 3.12 and [uv](https://docs.astral.sh/uv/)
- Bun 1.2.23

Install dependencies:

```sh
uv sync --all-extras --dev
bun install --frozen-lockfile
```

Generate the deterministic fixture publication (no model or network calls):

```sh
XYZ_RUNTIME=fake XYZ_STORAGE_ROOT="$PWD/storage" uv run xyz-agent --date 2026-01-02
```

The command prints a result with `"status":"published"`. Start the web service:

```sh
XYZ_STORAGE_ROOT="$PWD/storage" bun run --cwd web start
```

Open <http://localhost:3000/>. `/health` should return `{"ok":true}`.

Run every cost-free check:

```sh
uv run ruff check .
uv run mypy agent/src
uv run pytest
bun run check
bun test web/tests
```

## How to run with real Pi models

1. Configure provider credentials supported by Pi. For OpenRouter, set `OPENROUTER_API_KEY`.
2. Select separate role models and the Pi runtime:

   ```sh
   export XYZ_RUNTIME=pi
   export XYZ_PRODUCER_PROVIDER=openrouter
   export XYZ_PRODUCER_MODEL=anthropic/claude-sonnet-4
   export XYZ_REVIEWER_PROVIDER=openrouter
   export XYZ_REVIEWER_MODEL=google/gemini-2.5-flash
   ```

3. Run `uv run xyz-agent`. A successful command exits zero and updates local storage atomically. A failed run exits non-zero and leaves existing pointers unchanged.

To use another Pi-supported local provider, change the two provider/model pairs; workflow semantics do not change.

## How to use object storage

Set the same bucket endpoint and read credentials on the web service. Give write/delete credentials only to the agent.

```sh
export XYZ_STORAGE=s3
export XYZ_S3_BUCKET=...
export XYZ_S3_ENDPOINT_URL=...
export XYZ_S3_REGION=auto
export XYZ_S3_ACCESS_KEY_ID=...
export XYZ_S3_SECRET_ACCESS_KEY=...
```

Railway deployment configuration is intentionally not included; CI builds both images but does not deploy them.

## Configuration reference

All application variables use the `XYZ_` prefix.

| Variable | Default | Meaning |
|---|---|---|
| `XYZ_RUNTIME` | `pi` | `pi` or deterministic `fake` runtime |
| `XYZ_STORAGE` | `local` | `local` or `s3` adapter |
| `XYZ_STORAGE_ROOT` | `./storage` | Shared local artifact directory |
| `XYZ_MAX_ITERATIONS` | `3` | Shared reviewer/validator generation limit (1–8) |
| `XYZ_MALFORMED_RETRIES` | `2` | Invalid terminating-output retries |
| `XYZ_TOOL_BUDGET` | `24` | Source-tool calls per Pi session |
| `XYZ_SESSION_TIMEOUT_SECONDS` | `300` | Deadline for one Pi prompt settlement |
| `XYZ_PRODUCER_PROVIDER`, `XYZ_PRODUCER_MODEL` | OpenRouter / Claude | Producer selection |
| `XYZ_REVIEWER_PROVIDER`, `XYZ_REVIEWER_MODEL` | OpenRouter / Gemini | Reviewer selection |
| `XYZ_SOURCE_REVISION` | `unknown` | Revision recorded in provenance |
| `XYZ_RETENTION_DAYS` | `7` | Distinct successful UTC dates retained |
| `XYZ_S3_*` | unset | Private S3-compatible bucket configuration |
| `PORT` | `3000` | Bun HTTP listen port |

Versioned inputs live in `config/sources.json`, `prompts/`, and `templates/`. Model output is always typed content; only repository templates produce public markup.

## HTTP reference

| Route | Result |
|---|---|
| `/` | Current pointer's verified `index.html` |
| `/days/YYYY-MM-DD/` | Verified retained daily artifact, or `404` |
| `/assets/YYYY-MM-DD/RUN_ID/HASH.css` | Pointer-referenced immutable CSS |
| `/health` | `200` only when current pointer and manifest are readable and valid |

The server exposes no storage listing or mutation route. It verifies pointer, manifest, and artifact hashes before serving content.

## Repository map

```text
agent/          Python workflow, adapters, renderer, validators, publisher
pi-extension/  constrained source and terminating-output tools for Pi
web/            read-only Bun artifact server
config/         versioned source registry
prompts/        separate producer, reviewer, and shared policies
templates/      deterministic HTML and CSS
.github/        cost-free CI only; no deployment workflow
```
