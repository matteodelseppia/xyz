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

1. Configure provider credentials supported by Pi. For OpenCode Go, set `OPENCODE_API_KEY`.
2. Select separate role models and the Pi runtime:

   ```sh
   export XYZ_RUNTIME=pi
   export OPENCODE_API_KEY=...
   export XYZ_PRODUCER_PROVIDER=opencode-go
   export XYZ_PRODUCER_MODEL=gpt-5.6-luna
   export XYZ_PRODUCER_THINKING=high
   export XYZ_REVIEWER_PROVIDER=opencode-go
   export XYZ_REVIEWER_MODEL=deepseek-v4-flash
   export XYZ_REVIEWER_THINKING=medium
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

## How production deployment works

A successful `CI` run on `main` triggers `.github/workflows/cd.yml`. CD deploys the web and agent services to the single Railway `production` environment, then checks that the web health route is reachable. The workflow authenticates with the environment-scoped `RAILWAY_TOKEN_PRODUCTION` GitHub secret; application and bucket credentials remain Railway variables and are never copied to GitHub.

Production uses only three Railway resources:

- `xyz-web`: one continuously running replica in Amsterdam;
- `xyz-agent`: an ephemeral cron service scheduled at `0 6 * * *` (once daily at 06:00 UTC);
- `xyz-publications`: one private Amsterdam object-storage bucket.

There is no staging environment, database, volume, or duplicate service. Deployment records the successful CI commit as `XYZ_SOURCE_REVISION` before uploading both services.

## Configuration reference

All application variables use the `XYZ_` prefix.

| Variable | Default | Meaning |
|---|---|---|
| `XYZ_RUNTIME` | `pi` | `pi` or deterministic `fake` runtime |
| `XYZ_STORAGE` | `local` | `local` or `s3` adapter |
| `XYZ_STORAGE_ROOT` | `./storage` | Shared local artifact directory |
| `XYZ_REPOSITORY_ROOT` | detected | Root containing `config/`, `prompts/`, and `templates/`; set to `/app` in the agent image |
| `XYZ_MAX_ITERATIONS` | `3` | Shared reviewer/validator generation limit (1–50) |
| `XYZ_MALFORMED_RETRIES` | `2` | Invalid terminating-output retries |
| `XYZ_MAX_VALIDATION_RETRIES` | `2` | Separate deterministic-validation correction retries (0–10); these do not consume generation iterations |
| `XYZ_TOOL_BUDGET` | `100` | Source-tool calls per run, divided between producer and reviewer |
| `XYZ_SESSION_TIMEOUT_SECONDS` | `600` | Deadline for one Pi prompt settlement (10–3000 seconds) |
| `XYZ_PRODUCER_PROVIDER`, `XYZ_PRODUCER_MODEL`, `XYZ_PRODUCER_THINKING` | OpenCode Go / DeepSeek V4 Pro / medium | Producer selection |
| `XYZ_REVIEWER_PROVIDER`, `XYZ_REVIEWER_MODEL`, `XYZ_REVIEWER_THINKING` | OpenCode Go / GPT-5.6 Luna / high | Reviewer selection |
| `XYZ_SCHEDULED_RUN_CRON` | `0 6 * * *` | UTC daily cron expression used for the displayed next-run countdown |
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
| `/days/YYYY-MM-DD/prompt/` (or `/YYYY-MM-DD/prompt/`) | Public, redacted Pi/LangGraph run trace |
| `/sources/` | Sources configured for the current publication |
| `/loved-ones/` | Static list of selected articles; `?tag=<tag>` filters by a listed tag |
| `/assets/YYYY-MM-DD/RUN_ID/HASH.<ext>` | Pointer-referenced immutable CSS, JavaScript, or SVG |
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
.github/        cost-free CI and production Railway CD workflows
```
