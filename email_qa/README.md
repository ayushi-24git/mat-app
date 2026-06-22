# Email QA Pipeline

Automated QA validation for developer-built HTML emails against customer-approved PDF references, Figma designs, and asset bundles.

This directory contains the **pipeline code and specs only** — it is not yet wired into the Streamlit app. The accompanying `email_qa/skills/` markdown files document the architecture and serve as the authoritative spec. See [INTEGRATION.md](./INTEGRATION.md) for how to plug this into the mat-app UI.

---

## What this validates

Given a candidate HTML email and a reference Drive folder (containing the customer-approved PDF, Figma source, hero images, logos, icons, CTAs), the pipeline produces a marketer-readable QA report covering:

| Dimension | What it checks |
|---|---|
| **Style audit** | Colour palette drift, font stack, typography tokens, image alt-text, HTML byte size vs Gmail's 102 KB clip |
| **Compat check** | 31 rules across Outlook Classic (Word HTML engine quirks), Gmail (web / iOS / Android), Outlook New / 365 |
| **Link QA** | Anchor extraction, URL resolution, mapping verification, Mustache token recognition |
| **Visual diff** | Per-section pixel comparison of rendered HTML vs PDF reference (desktop + mobile viewports) |
| **Vision review** | Claude-vision per-section comparison for layout, spacing, colour, typography, image variant correctness |

Output: a self-contained HTML report + a comment + status transition on the linked Jira ticket.

---

## Architecture (4 composite skills)

```
                  ┌──────────────────────┐
                  │   email-qa  (MASTER) │  ← public entry point
                  └──────────┬───────────┘
                             │
        ┌────────────────────┼────────────────────────────┐
        │                    │                            │
        ▼                    ▼                            ▼
   ┌─────────┐         ┌────────────┐            ┌──────────────────┐
   │ ingest  │         │ pre-flight │            │ visual-validation│
   │         │         │  (parallel)│            │   (3-stage DAG)  │
   └─────────┘         └────────────┘            └──────────────────┘
        │                                                 │
        └────────────────────┬────────────────────────────┘
                             │
                             ▼
                   ┌────────────────────────┐
                   │ report-and-writeback   │
                   │ (HTML report + Jira)   │
                   └────────────────────────┘
```

Each composite is documented in `email_qa/skills/` at the repo root.

---

## Pipeline directory layout

```
email_qa/
├── README.md             ← this file
├── INTEGRATION.md        ← how to wire into mat-app's Streamlit UI
├── package.json          ← Node deps (Playwright, pixelmatch, juice, sharp)
└── scripts/
    ├── ingest.mjs            ← W-code parsing, file classification, meta synthesis
    ├── pre-flight.mjs        ← parallel runner over style + compat + link checks
    ├── visual-validation.mjs ← 3-stage orchestrator (pdf, render, diff, crop, vision)
    ├── report.mjs            ← aggregates findings → decides → renders HTML report
    ├── pdf-to-png.mjs        ← poppler wrapper (supports separate desktop/mobile or combined PDFs)
    ├── render.mjs            ← Playwright render at 640px + 375px
    ├── diff.mjs              ← pixelmatch full-page diff
    ├── crop-section.mjs      ← section-level crop pairs for vision review
    ├── style-audit.mjs       ← palette / font / token / size rules
    ├── compat-check.mjs      ← Outlook Classic / Gmail / Outlook New rule packs
    ├── link-check.mjs        ← anchor extraction + URL validation
    ├── vision-review-cli.mjs ← Claude CLI ×12 parallel per-section vision review
    └── compat-rules/
        ├── outlook-classic.mjs
        ├── outlook-new.mjs
        └── gmail.mjs
```

---

## Why this exists separately from the Streamlit app

The pipeline relies on system binaries (`poppler`, `Playwright/Chromium`, `rclone`) and is written in Node.js. These don't fit cleanly into the current Python-on-Railway deployment.

Three architectural options for runtime integration are documented in [INTEGRATION.md](./INTEGRATION.md):

- **(A)** Rewrite the pipeline in Python (long-term clean fit)
- **(B)** Multi-runtime container — add Node + Chromium + poppler to Railway's nixpacks
- **(C)** Two-service architecture — pipeline runs as a separate service, Streamlit calls it via HTTP (recommended)

This PR ships the **specs and code only**, deliberately not changing `app.py`, `requirements.txt`, or any deployment config. It cannot break the Railway deployment because nothing in the live import chain references this directory.

---

## Status

- **Specs** — complete (5 skill markdowns in `email_qa/skills/`)
- **Pipeline code** — complete (12 Node scripts, all tested standalone)
- **End-to-end smoke test** — passed against OPM-73 (Eaton 2026 campaign): real Drive folder pulled, all assets classified, full report generated, decision = READY WITH WARNINGS
- **Vision review live test** — deferred (avoids burning Claude API budget on every dev iteration)
- **Jira writeback live test** — deferred (avoids posting test comments on real tickets)
- **Streamlit integration** — pending; this is the runtime work blocked on the (A/B/C) decision in INTEGRATION.md

---

## Provenance

Built standalone in `ayushi-24git/optum-email-pipeline`. Mirrored here for integration into the broader Marketing Automation pipeline. The standalone repo remains the canonical dev environment for the QA pipeline itself.
