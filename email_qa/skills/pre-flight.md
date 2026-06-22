---
description: Pre-flight composite QA stage — runs style-audit, compat-check, and link-check in parallel against a campaign folder. Aggregates findings with suppressions applied. Emits qa/pre-flight.json with unified shape.
argument-hint: <campaign-dir-path>
---

# /pre-flight — composite static-analysis QA pass

You are running the pre-flight stage of the email QA pipeline. This stage performs all static analysis on the candidate HTML — no rendering, no vision review — and produces a unified findings file the master skill aggregates with later visual-validation results.

## What's included

| Sub-stage | What it checks | Severity range |
|---|---|---|
| **style-audit** | Color palette drift, font stack, typography tokens, image references + alt text, HTML byte size vs Gmail clip threshold | minor → critical |
| **compat-check** | 31 rules across 3 client packs: Outlook Classic (Word HTML quirks, MSO conditionals, padding/margin), Gmail (web/iOS/Android), Outlook New / 365 web | minor → critical |
| **link-check** | Anchor extraction, URL validation, mapping verification (if `input/link-mapping.json` exists), `tel:` / `mailto:` recognition, Mustache token detection | minor → critical |

## Input

| Argument | Required | Notes |
|---|---|---|
| `$1` — campaign directory | Yes | Absolute path to `campaigns/<slug>/` produced by the ingest skill. Must contain `output/email.html`. |

## Step 1 — Verify input

Check `$1/output/email.html` exists. If not, halt with a clear error — pre-flight has nothing to validate.

The other inputs (`input/link-mapping.json`, `input/campaign-meta.json`) are optional. Pre-flight runs without them; some link findings will be lower-confidence.

## Step 2 — Run the composite

Single command:

```bash
node scripts/pre-flight.mjs $1
```

The runner:

1. Spawns `style-audit.mjs`, `compat-check.mjs`, `link-check.mjs` in parallel
2. Waits for all three to finish (or fail)
3. Reads their individual JSON outputs from `qa/`
4. Tags each finding with `source` (which sub-stage produced it)
5. Applies suppression patterns (see below)
6. Aggregates counts: per-severity, per-source, suppressed
7. Writes `qa/pre-flight.json`
8. Exits 0 if all sub-stages succeeded, 1 if any failed

## Suppression patterns — codified false positives

The runner drops findings matching any of these patterns. They are known not-real-bugs:

| Pattern | Why |
|---|---|
| `/view_in_browser/i` | ESP-side token. Capillary's ESP fills this at send time. Per Ayushi's policy we deliberately do not validate it; legacy rules in style-audit + compat-check would otherwise flag its absence/presence inconsistently. |

To extend: edit `ALWAYS_SUPPRESS` at the top of `scripts/pre-flight.mjs`. Each new entry should have a comment explaining *why* it's suppressed, not just *what* matches.

## Output shape — qa/pre-flight.json

```jsonc
{
  "generated_at": "2026-06-19T...",
  "total_ms": 1234,
  "sub_stages": {
    "style-audit":  { "code": 0, "ms": 412 },
    "compat-check": { "code": 0, "ms": 388 },
    "link-check":   { "code": 0, "ms": 502 }
  },
  "failures": [],                          // any sub-stage with non-zero exit code
  "counts": {
    "critical": 0,
    "major": 1,
    "minor": 5,
    "total": 6,
    "suppressed": 2
  },
  "counts_by_source": {
    "style-audit": 4,
    "compat-check": 1,
    "link-check": 1
  },
  "findings": [
    {
      "source": "style-audit",
      "severity": "minor",
      "category": "color_drift",
      "description": "Color #f9f9f9 is near the approved palette but not an exact match",
      "suggested_fix": "Replace with #f5f5f5 from design tokens",
      ...
    },
    ...
  ],
  "suppressed_findings": [
    {
      "source": "compat-check",
      "severity": "minor",
      "description": "view_in_browser token not validated",
      "matched_pattern": "view_in_browser"
    }
  ]
}
```

## Severity decision matrix

Pre-flight emits findings but does NOT decide pass/fail on its own. The master skill (`email-qa`) applies the final decision logic after combining pre-flight with visual-validation findings:

| Aggregate result | Master skill decision |
|---|---|
| any critical | MUST FIX |
| zero critical, ≥1 major or minor | READY WITH WARNINGS |
| zero findings | READY TO SEND |

Pre-flight just supplies the inputs.

## Failure modes — how to behave

| Failure | Behaviour |
|---|---|
| `$1/output/email.html` missing | Halt — pre-flight has no input |
| One sub-stage fails (e.g. link-check crashes) | Continue — emit `qa/pre-flight.json` with that sub-stage's failure recorded in `failures[]`, findings from the other two still aggregated. Exit code 1 surfaces it to the master skill. |
| All three fail | Emit a `qa/pre-flight.json` with empty findings + 3 entries in `failures[]`. Master skill will surface as "pre-flight stage failed" in the final report. |
| `qa/pre-flight.json` already exists | Overwrite — pre-flight runs are idempotent. |

## Constraints

- Do not modify `output/email.html` or any campaign input
- Do not invent findings — empty findings means clean pre-flight
- Suppression patterns are intentionally narrow; do not broaden them to silence noisy rules. If a rule is consistently noisy, fix the rule itself in `compat-rules/` or `style-audit.mjs`
- The three sub-scripts retain their individual JSON outputs (`qa/style-audit.json`, etc.) for backwards compatibility with the existing report builder

## Why composite

Three reasons we wrap these three:

1. **Parallel execution** — sub-stages don't depend on each other; parallel saves ~60% wall-clock vs serial
2. **Unified suppression policy** — one place to maintain known false positives across rules
3. **Single entry point for the master skill** — the orchestrator calls one stage, gets one JSON back, instead of stitching three
