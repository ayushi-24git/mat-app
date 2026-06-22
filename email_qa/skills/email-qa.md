---
description: Master Email QA skill — single entry point for the full QA pipeline. Takes an uploaded HTML, a Drive assets folder ID, and an optional Jira ticket key; runs ingest → pre-flight → visual-validation → report-and-writeback. Designed to be pluggable into GitHub Actions, mat-app's Streamlit page, or invoked directly via Claude Code CLI.
argument-hint: <html-path> <drive-folder-id> [jira-ticket-key]
---

# /email-qa — master QA pipeline (public entry point)

You are running the master email QA pipeline. Your job: orchestrate the four composite skills in sequence, surface progress, handle partial failures, and emit a single final summary the caller can consume.

This is the **public API** of the pipeline. It's what gets called from:

- mat-app's Streamlit `qa.py` page (Souradeep's repo)
- GitHub Actions workflow (`.github/workflows/email-qa.yml`)
- Claude Code CLI directly: `/email-qa <html> <folder> [ticket]`
- The local demo UI (`ui/server.mjs`)

Treat the four composite skills as black boxes. Don't re-implement their logic here; just orchestrate.

## Inputs

| Argument | Required | Notes |
|---|---|---|
| `$1` — HTML file path | Yes | The candidate HTML to QA. Uploaded by marketer or provided by automation. |
| `$2` — Drive folder ID | Yes | Drive ID of the campaign's assets subfolder under `<BASE_DRIVE>/assets/`. Picked by marketer or auto-resolved from a sticky default. |
| `$3` — Jira ticket key | No | E.g. `OPM-73`. If provided, enables writeback. If omitted, runs in report-only mode. |

## Step 1 — Ingest

Invoke the `ingest` skill with the three inputs:

```
/ingest $1 $2 $3
```

Capture the final line from its output: `INGEST_OK campaign_dir=<path>`. Use `<path>` for all subsequent steps.

### Failure handling

If ingest fails (no usable HTML, no usable PDF, both Drive and Jira unreachable), halt. Without inputs there's nothing to QA. Emit:

```
MASTER_FAILED stage=ingest reason=<short description>
```

## Step 2 — Pre-flight (style + compat + link)

Invoke the `pre-flight` skill against the campaign dir:

```
/pre-flight <campaign_dir>
```

Pre-flight is **fail-soft**: if one sub-stage (style-audit / compat-check / link-check) fails, the others still run and produce findings. Don't halt on its non-zero exit code; just note it for the final summary.

Pre-flight produces `qa/pre-flight.json`.

## Step 3 — Visual validation (render + diff + crop + vision)

Invoke the `visual-validation` skill:

```
/visual-validation <campaign_dir>
```

By default this includes the vision-review sub-stage (Claude CLI ×12 parallel, ~30-40 seconds, $0.25-0.34 cost). If the caller passes a `--skip-vision` flag through to email-qa, propagate it:

```
/visual-validation <campaign_dir> --skip-vision
```

Visual-validation has a hard gate at Stage A: if `pdf-to-png` or `render` fails, the run is marked degraded and downstream sub-stages are skipped. In that case, continue to Step 4 anyway — the report will still surface pre-flight findings + the failure.

Visual-validation produces `qa/visual-validation.json`.

## Step 4 — Report + Jira writeback

Invoke the `report-and-writeback` skill:

```
/report-and-writeback <campaign_dir>
```

This stage:

1. Aggregates findings from pre-flight + visual-validation
2. Decides MUST FIX / READY WITH WARNINGS / READY TO SEND
3. Renders `qa/report.html`
4. If `input/campaign-meta.json.source.jira_ticket` is set, posts a comment + transitions status on the Jira ticket via MCP
5. If no Jira ticket, skips writeback silently

This is fail-soft on the Jira side: a failed comment-post or status-transition does not block the report from being emitted.

## Step 5 — Emit final summary

After all four stages, print the master summary line(s) the caller will parse:

### Success cases

```
MASTER_OK slug=<slug> decision=<DECISION> critical=<N> major=<N> minor=<N> report=<path>
WRITEBACK ticket=<JIRA-KEY-or-none> status=<posted|skipped|failed>
```

### Partial success

If one or more stages had non-fatal failures (e.g. one pre-flight sub-stage failed, or vision review failed but rest worked):

```
MASTER_PARTIAL slug=<slug> decision=<DECISION> critical=<N> major=<N> minor=<N> report=<path>
FAILED_STAGES=<comma-separated list>
WRITEBACK ticket=<JIRA-KEY-or-none> status=<posted|skipped|failed>
```

### Hard failure

If ingest failed (no inputs available):

```
MASTER_FAILED stage=ingest reason=<...>
```

## Concurrency / sequencing rules

| Step pair | Sequencing |
|---|---|
| 1 → 2 | Sequential — pre-flight needs ingest's campaign dir |
| 1 → 3 | Sequential — visual-validation needs ingest's campaign dir |
| 2 ↔ 3 | **Could run in parallel.** Pre-flight (static analysis on HTML) and visual-validation (PDF/render/diff/vision) have no shared mutable state. |
| 4 | Sequential after both 2 and 3 — needs the artifacts they produce |

In the current implementation we run 2 then 3 sequentially. Future optimization: launch 2 and 3 in parallel for ~30% wall-clock saving. Worth doing once we have stable end-to-end tests.

## Cost summary per run

| Stage | Cost | Time |
|---|---|---|
| ingest | $0 | ~3-10 sec (depends on Drive folder size + rclone speed) |
| pre-flight | $0 | ~0.1 sec (all parallel) |
| visual-validation (with vision) | $0.25-0.34 | ~35-45 sec |
| visual-validation (--skip-vision) | $0 | ~3-4 sec |
| report-and-writeback | $0 | ~0.5 sec |
| **TOTAL with vision** | **~$0.30** | **~40-55 sec** |
| **TOTAL --skip-vision** | **$0** | **~4-15 sec** |

## Output artifacts (per run)

Everything lives under `campaigns/<slug>/`:

```
input/
  campaign-meta.json
  desktop.pdf
  mobile.pdf
  (figma .fig file)
  assets/  (10+ images)
output/
  email.html  (the uploaded candidate)
extracted/
  pdf-desktop.png
  pdf-mobile.png
qa/
  pre-flight.json          ← aggregated findings from style + compat + link
  visual-validation.json   ← aggregated findings from vision review
  style-audit.json         ← preserved for backwards compat
  compat-check.json        ← preserved
  link-check.json          ← preserved
  vision-review.json       ← preserved
  diff-stats.json          ← pixel-diff stats
  diff-desktop.png         ← visual diff overlay
  diff-mobile.png          ← visual diff overlay
  rendered-desktop.png     ← Playwright render of HTML at 640px
  rendered-mobile.png      ← Playwright render of HTML at 375px
  crops/                   ← per-section image pairs for vision review
  report.html              ← self-contained HTML report (marketer-readable)
```

## Constraints

- This skill **does not** re-implement composite logic. It orchestrates.
- This skill **does not** access MCP tools directly. The writeback skill owns Jira contact.
- This skill **does not** modify campaign source files (HTML, PDF, assets). It only writes to `qa/`.
- Stages are fail-soft except ingest (which is the hard gate — no inputs = no QA).
- The decision (MUST FIX / WARNINGS / READY) is computed in `report-and-writeback`, not here. The final summary line just relays it.

## Why this is the master skill

Three reasons this is the right top-level entry point for the GitHub-pluggable / mat-app-embedded use case:

1. **Single-command invocation** — one slash command, three positional args, zero state assumptions. Easy to embed in any orchestrator.
2. **Composite-only orchestration** — this skill's body is short and high-leverage. Logic lives in the four composites; this just sequences them.
3. **Caller-parseable output contract** — the `MASTER_OK / MASTER_PARTIAL / MASTER_FAILED` lines are designed for grep-friendly parsing by Streamlit, GitHub Actions, or a CI wrapper script. No JSON-only output that's awkward to chain.
