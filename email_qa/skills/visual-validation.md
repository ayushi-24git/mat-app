---
description: Visual validation composite QA stage — renders the HTML, converts PDF reference, computes pixel diff, crops per-section pairs, and runs Claude-vision review. Emits qa/visual-validation.json with severity-classified findings.
argument-hint: <campaign-dir-path> [--skip-vision] [--vision-concurrency=N]
---

# /visual-validation — composite visual QA pass

You are running the visual-validation stage of the email QA pipeline. This stage produces every visual artifact and finding the master skill needs to decide whether the candidate HTML matches the customer-approved PDF.

## What's included

| Stage | Sub-tasks | Purpose |
|---|---|---|
| **A** (parallel) | `pdf-to-png` + `render` | Produce reference + candidate PNGs from PDF and HTML |
| **B** (parallel) | `diff` + `crop-section[desktop]` + `crop-section[mobile]` | Full-page pixel diff + per-section image pairs for vision review |
| **C** (serial)  | `vision-review` (Claude CLI ×N internal parallel) | Per-section visual comparison; emits severity-classified findings |

## Input

| Argument | Required | Notes |
|---|---|---|
| `$1` — campaign directory | Yes | Absolute path to `campaigns/<slug>/` produced by the ingest skill |
| `--skip-vision` | No | Flag — skip Stage C. Useful for CI iterations where you only want full-page diff + crops. |
| `--vision-concurrency=N` | No | Number of parallel Claude CLI calls for vision review (default `12`). |

The campaign directory must contain at minimum:

- `output/email.html` — the candidate HTML
- One of:
  - `input/desktop.pdf` + `input/mobile.pdf` (Drive-assets convention), OR
  - `input/reference.pdf` (legacy single-PDF with 2 pages)

Optional but improves quality:

- `extracted/design-tokens.json` — when present, vision review uses per-section bounds (sharper findings). When absent, falls back to equal-height panel slicing (6 panels per viewport).

## Step 1 — Verify inputs

Check the campaign dir contains:

1. `output/email.html` (hard requirement — halt if missing)
2. Either separate desktop/mobile PDFs OR a combined reference PDF (hard requirement — halt if neither found)

Optional check: warn if `extracted/design-tokens.json` is missing, noting vision review will fall back to panel mode.

## Step 2 — Run the composite

Single command:

```bash
node scripts/visual-validation.mjs $1
```

For faster iterations (skip the expensive vision step):

```bash
node scripts/visual-validation.mjs $1 --skip-vision
```

The runner orchestrates Stages A → B → C with these dependencies:

```
                   ┌─────────────┐
   STAGE A         │ pdf-to-png  │   render
   (parallel)      └──────┬──────┘   │
                          │          │
                          └────┬─────┘
                               ↓
                   ┌─────────────────────────┐
   STAGE B         │  diff   crop[desktop]   │   crop[mobile]
   (parallel)      └─────────────────────────┘
                               ↓
                   ┌─────────────────────────┐
   STAGE C         │   vision-review CLI ×N  │
   (serial)        └─────────────────────────┘
```

## Failure modes

| Failure | Behaviour |
|---|---|
| `output/email.html` missing | Halt — nothing to validate |
| No PDF found | Halt — no reference to compare against |
| `pdf-to-png` or `render` fails | Mark report `degraded: true`, emit empty findings, exit 1. Downstream stages cannot run without the PNGs they produce. |
| `diff` fails | Continue — crop + vision still run; pixel-diff stats absent from final report |
| `crop-section` fails (one or both viewports) | Continue — vision review may fall back or skip the affected viewport |
| `vision-review` fails | Continue — emit report with diff stats + crops but zero vision findings; failure recorded in `failures[]` |
| `design-tokens.json` missing | Vision review uses panel-mode fallback (less precise but works) |

## Output shape — qa/visual-validation.json

```jsonc
{
  "generated_at": "2026-06-20T...",
  "total_ms": 38421,
  "degraded": false,
  "skip_vision": false,
  "sub_stages": {
    "pdf-to-png":     { "code": 0, "ms": 1842 },
    "render":         { "code": 0, "ms": 2104 },
    "diff":           { "code": 0, "ms": 521 },
    "crop[desktop]":  { "code": 0, "ms": 412 },
    "crop[mobile]":   { "code": 0, "ms": 389 },
    "vision-review":  { "code": 0, "ms": 33153 }
  },
  "failures": [],
  "pixel_diff": {
    "desktop_mismatch_pct": 13.93,
    "mobile_mismatch_pct":  24.12
  },
  "vision": {
    "sections_reviewed": 16,
    "total_cost_usd":    0.34,
    "mode":              "named"     // or "panel" if design-tokens.json was absent
  },
  "counts": {
    "critical": 0,
    "major":    2,
    "minor":    4,
    "total":    6
  },
  "findings": [
    {
      "source":      "vision-review",
      "viewport":    "desktop",
      "section":     "hero",
      "severity":    "major",
      "category":    "spacing",
      "description": "Hero band padding is ~12 px shorter than the PDF reference.",
      "suggested_fix": "Increase top padding on the hero <td> from 30 px to 42 px.",
      "panel_index": null
    },
    ...
  ]
}
```

## Performance characteristics

Typical wall-clock on the Eaton-sized campaign (16 sections, 2 viewports):

| Stage | Wall-clock | Notes |
|---|---|---|
| A — pdf-to-png + render | ~2-3 sec (parallel) | Dominated by Playwright render |
| B — diff + 2× crop-section | ~1 sec (parallel) | I/O bound |
| C — vision-review | ~30-40 sec | Claude CLI ×12 parallel inside |
| **Total** | **~35-45 sec** | With vision; ~3-4 sec with --skip-vision |

## Cost (Stage C only)

Vision review uses the Claude Code CLI in `--no-session-persistence` mode with `--max-budget-usd 0.20` per call. Per-campaign cost: **$0.25–$0.34** depending on section count.

Skip Stage C via `--skip-vision` for cost-free iterations (e.g. quick CI passes, layout-only changes that don't need vision).

## Severity rules (applied inside vision-review-cli)

The skill defers to the existing prompt in `scripts/vision-review-cli.mjs`. Summary of what it produces:

| Severity | Triggers |
|---|---|
| **Critical** | Section collapsed entirely (zero/near-zero content height), missing required section, broken/missing image, wrong CTA target visible, blank content area |
| **Major** | Wrong image variant, wrong shape (filled vs outlined, square vs circle, elongated vs square), wrong colour outside ΔE 10, copy differs substantively, **spacing/padding off by >5 px**, positioning visibly wrong |
| **Minor** | Colour within ΔE 5–10, **padding off by ≤5 px**, headline wrap different, sub-pixel typography, JPEG compression noise in PDF reference |

## Constraints

- Read-only on `output/email.html` and `input/` — never modify campaign source
- Idempotent: re-running overwrites `qa/visual-validation.json` cleanly
- Vision review's `view_in_browser` suppression is enforced by `vision-review-cli.mjs` (not this composite — keeps the pattern localized to where vision-specific prompts live)
- Pre-flight findings are NOT included here — that's a separate composite. The master skill (`email-qa`) combines both.

## Why composite

1. **Dependency-aware parallelism** — Stage A's two scripts have no dependency on each other; same for Stage B's three. We save ~50% wall-clock vs serial.
2. **Single entry for the master skill** — orchestrator gets one JSON back instead of stitching five
3. **Degraded-mode handling** — if Stage A fails, downstream stages are skipped with a clear `degraded: true` signal rather than a chain of cascading errors
4. **`--skip-vision` flag** — vision is the expensive stage; making it skippable enables fast CI iterations
