# 2026-09-01: unavailable article dates

## Report

Some rendered digest entries show `date unavailable` even after the producer read the corresponding article.

## Root cause

The renderer considered dates only on the evidence IDs selected by the model. Feed evidence often has no date, while a later `read_entry` result for the same article can contain one. The model may correctly cite the feed ID, leaving the read article's dated evidence unused by rendering.

## Expected behavior

When a producer has read an article, its evidence-backed publication date should be shown for a cited feed record of the same source article. Visible, explicitly labelled publication dates should also be extracted from article pages when structured metadata is absent.

## Fix

- The renderer now joins same-source evidence by normalized article URL. It uses a date recovered by `read_entry` even when the published update cites an undated feed ID.
- The source tool now also extracts explicitly labelled visible dates such as `Published on September 1, 2026`, in addition to structured metadata and `<time>` elements.

## Verification

- Added a renderer regression test where a cited feed record is undated but the corresponding read entry is dated.
- Added a source-tool test for a labelled visible publication date.
- Passed focused Python rendering tests, Ruff, mypy, TypeScript checks, and Pi-extension tests.

## Impact

Only evidence-backed dates are displayed. The model does not supply dates, so it cannot invent them.
