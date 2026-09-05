# 2026-09-02: agent image omits editorial anchors

## Report

The production agent run `b93df4281af54b6baa7176f62a07b55b` exited immediately with:

```
[Errno 2] No such file or directory: '/app/web/public/loved-ones/index.html'
```

## Initial diagnosis

`Settings.editorial_anchors()` parses `web/public/loved-ones/index.html` before the workflow starts. The agent Dockerfile sets `XYZ_REPOSITORY_ROOT=/app` but copies `config`, `prompts`, `templates`, and the Pi extension only; it does not copy `web/public`. The file exists in a checkout and in the web image, explaining why Python tests pass while the deployed agent fails.

## Expected behavior

The agent image must include the versioned loved-ones page it uses as editorial-anchor input. CI should verify this runtime-image dependency.

## Fix

The agent Dockerfile now copies only `web/public/loved-ones/index.html`, the versioned input used by `Settings.editorial_anchors()`. The CSS and JavaScript remain exclusively in the web image because the agent does not consume them.

The image CI job now loads the built agent image and runs `Settings().editorial_anchors()` inside it. This regression check failed before the Dockerfile change with the reported `FileNotFoundError` and verifies both the path and parser after the change.

## Verification

- Reproduced the failure by building the original agent image and testing for `/app/web/public/loved-ones/index.html`; the command exited `1`.
- Built the corrected agent image and invoked `Settings().editorial_anchors()` inside it successfully.
- `git diff --check` passed.
- The local environment does not have `uv`, `pytest`, `ruff`, or `mypy`; the Python suite and static checks were not run locally. CI runs them and now additionally runs the image regression check.
