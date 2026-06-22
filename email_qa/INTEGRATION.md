# Email QA — Streamlit Integration Guide

This document describes how to wire the QA pipeline (in this directory) into the mat-app Streamlit UI. **None of this is implemented yet.** The current PR ships specs + code only; this guide is the roadmap for the follow-up runtime PR.

---

## The integration shape

The QA pipeline becomes a **new standalone Streamlit page** accessible from the sidebar nav, independent of Pages 1-5 session state. Marketers can run QA whenever they have an HTML candidate ready, regardless of where audience build / approval is in the workflow.

```
┌─────────────────────────────────────────────────────────────────────┐
│  Sidebar nav                                                         │
│  ──────────────                                                      │
│  📍 Page 1 — Jira Intake                                             │
│     Page 2 — Audience Builder                                        │
│     Page 3 — Approval Gate                                           │
│     Page 4 — Monitoring                                              │
│     Page 5 — Post-Campaign ROI                                       │
│  ─────                                                                │
│  🆕 QA Validation       ← new entry, independent (no page number)    │
└─────────────────────────────────────────────────────────────────────┘
```

---

## The qa.py page (to be written in the runtime PR)

A standalone Streamlit page module that does NOT depend on session state from Pages 1-5. Three inputs:

| Input | Behaviour |
|---|---|
| **HTML upload** (required) | Marketer uploads the HTML file every run |
| **Reference folder** (required) | Dropdown of subfolders under `<BASE_DRIVE>/assets/`. **Sticky**: defaults to last selection (persisted via `db.py`), with a "change" link to override. |
| **Jira ticket URL** (optional) | If provided, enables QA writeback (comment + status transition). Skipped silently if blank. |

### Workflow

```
1. Marketer goes to QA Validation page
2. Uploads HTML; system parses W-code from filename or body content (hint only)
3. Folder dropdown auto-defaults to sticky preference (or W-code match on first run)
4. Jira ticket auto-suggested via JQL search on W-code (overridable)
5. Click "Run QA"
6. Page streams progress as pipeline runs (~40-55 sec with vision)
7. Page renders qa/report.html in an iframe
8. If Jira ticket provided, page confirms comment posted + status transition
9. Report saved to opmXX_reports/qa_report.html (matching OPM-67 convention)
```

---

## Runtime architecture — pick ONE of three paths

The pipeline relies on Node.js + Playwright + poppler + rclone, none of which are currently installed in mat-app's Railway image. Pick the path that fits the team's deployment philosophy:

### Path A — Rewrite pipeline in Python (long-term clean fit)

Replace each Node script with a Python equivalent:

| Node component | Python replacement |
|---|---|
| Playwright (Node) | `playwright-python` |
| `pdftoppm` (poppler) subprocess | `pdf2image` library |
| `pixelmatch` | `Pillow` + custom diff |
| `juice` (CSS inliner) | `premailer` |
| Atlassian MCP | `atlassian-python-api` + API token |
| Drive (rclone) | `google-api-python-client` + service account |
| Claude Code CLI vision review | direct `anthropic` SDK calls with asyncio (12 parallel) |

**Effort: ~2-3 weeks.** Pipeline lives entirely inside mat-app, no external service.

### Path B — Multi-runtime container

Keep the Node code as-is. Add Node + Chromium + poppler + rclone to Railway's build:

```toml
# nixpacks.toml additions
[phases.setup]
aptPkgs = ["pkg-config", "libcairo2-dev", "libffi-dev",
           "poppler-utils", "rclone",
           "libnss3", "libatk1.0-0", "libatk-bridge2.0-0",
           "libcups2", "libxkbcommon0", "libxcomposite1",
           "libxdamage1", "libxrandr2", "libgbm1",
           "libxshmfence1", "libdrm2"]
nixPkgs = ["nodejs_20"]
```

`qa.py` shells out to `node email_qa/scripts/run-qa.mjs <args>`. Anthropic MCP is replaced with REST API (token-based).

**Effort: ~1 week.** Railway image grows by ~400 MB (Chromium dominates). Cold starts get slower.

### Path C — Two-service architecture (recommended)

Keep mat-app Python-pure on Railway. Host the QA pipeline as a separate service:

```
mat-app (Railway / Python)                  email-qa-service (Node + binaries)
─────────────────────────                   ─────────────────────────────────
qa.py page  ────POST /run-qa──────►        Express/Fastify endpoint
            ◄────polls /status────         runs the Node pipeline
            ◄────receives result──         posts to Jira via REST + token
```

Deploy targets for the QA service:

- **GitHub Actions** — best for low-volume (~10-30 QA/day). Triggered by webhook from mat-app. Per-run pricing model. Heavy binaries (Chromium) pre-cached.
- **Second Railway service** — best for always-on convenience. Costs slightly more idle.
- **Cloud Run / Fly.io** — best for spiky bursts; container-native; pay-per-request.

**Effort: ~1.5 weeks.** Cleanest separation. mat-app's Railway image stays small + fast.

---

## Sketch — qa.py page module (Python; to be added in runtime PR)

```python
# qa.py — standalone QA Validation page
# Mirrors the structure of approvals.py / campaigns.py.
# Calls the email_qa pipeline via subprocess (Path B) OR HTTP (Path C).

import streamlit as st
import streamlit.components.v1 as components
import subprocess
import json
import re
from pathlib import Path
from db import get_user_preference, set_user_preference   # mat-app's existing db.py
from drive_client import list_assets_subfolders            # new helper (Drive API)


W_CODE_REGEX = re.compile(r"WF\d{7,9}")


def parse_w_code(html_filename: str, html_content: str) -> str | None:
    """Parse W-code from filename first, fallback to body content. Hint only."""
    m = W_CODE_REGEX.search(html_filename)
    if m: return m.group(0)
    m = W_CODE_REGEX.search(html_content)
    if m: return m.group(0)
    return None


def render_qa_page():
    st.title("HTML Creative QA Validation")
    st.caption("Validate developer-built HTML against the customer-approved PDF and Figma design.")

    # ── INPUT 1: HTML upload (required) ──
    uploaded_html = st.file_uploader(
        "Upload HTML",
        type=["html", "htm"],
        help="The system auto-resolves the campaign folder and Jira ticket from the W-code in the filename"
    )
    if not uploaded_html: return

    html_bytes = uploaded_html.getvalue()
    html_text = html_bytes.decode("utf-8", errors="replace")
    w_code_hint = parse_w_code(uploaded_html.name, html_text)
    if w_code_hint:
        st.success(f"Detected W-code: **{w_code_hint}**")

    # ── INPUT 2: Reference folder dropdown (sticky default) ──
    folders = list_assets_subfolders()  # returns [(id, name), ...]
    saved_folder_id = get_user_preference("qa_default_folder")
    default_idx = next(
        (i for i, (fid, fname) in enumerate(folders)
         if (w_code_hint and fname.startswith(w_code_hint)) or fid == saved_folder_id),
        0
    )
    selected_id, selected_name = folders[st.selectbox(
        "Reference folder (source of truth)",
        range(len(folders)),
        index=default_idx,
        format_func=lambda i: folders[i][1]
    )]
    # Persist the selection as the new sticky default
    set_user_preference("qa_default_folder", selected_id)

    # ── INPUT 3: Jira ticket (optional) ──
    jira_url = st.text_input(
        "Jira ticket URL (optional — enables writeback)",
        placeholder="https://capillarytech.atlassian.net/browse/OPM-73"
    )

    # ── Run button ──
    if not st.button("▶  Run QA"): return

    # Save uploaded HTML to a temp location
    tmp_html = Path("/tmp") / uploaded_html.name
    tmp_html.write_bytes(html_bytes)

    # Path C: HTTP call to email-qa-service
    # ----- OR -----
    # Path B: subprocess to local Node pipeline
    with st.spinner("Running QA pipeline (~40-55 sec)..."):
        result = subprocess.run(
            ["node", "email_qa/scripts/run-qa.mjs",
             "--html", str(tmp_html),
             "--drive-folder-id", selected_id,
             "--jira-url", jira_url or ""],
            capture_output=True, text=True
        )
        summary = json.loads(result.stdout.strip().split("\n")[-1])

    # Display decision
    decision = summary["decision"]
    if decision == "READY TO SEND":
        st.success(f"✓ {decision}")
    elif decision == "READY WITH WARNINGS":
        st.warning(f"⚠ {decision}")
    else:
        st.error(f"✗ {decision}")

    counts = summary["counts"]
    st.write(f"**{counts['critical']}** critical · **{counts['major']}** major · **{counts['minor']}** minor")

    # Render report inline
    report_path = summary["report_path"]
    with open(report_path) as f:
        components.html(f.read(), height=900, scrolling=True)

    # Jira confirmation
    if jira_url:
        st.info(f"✓ Posted comment + transitioned status on {summary.get('jira_ticket')}")
```

This is illustrative — the actual qa.py will be added in the follow-up runtime PR alongside the chosen Path (A/B/C).

---

## What this PR does NOT do

To preserve the Railway deployment's stability, this PR explicitly does NOT:

- Add `qa.py` to the repo (would tempt someone to import it before the runtime is ready)
- Modify `app.py` (no nav wiring; no imports of the QA module)
- Modify `requirements.txt` (no new Python deps yet)
- Modify `nixpacks.toml` / `packages.txt` (no new system deps yet)
- Modify `Procfile` / `railpack.json` (deploy config unchanged)

The Streamlit app's import graph is completely untouched. After this PR merges:

1. Railway detects the merge
2. Rebuilds with the unchanged `nixpacks.toml` / `requirements.txt`
3. Restarts `streamlit run app.py`
4. Loads exactly the same module graph it loaded yesterday
5. Existing pages render exactly as before

**Risk of breaking the live deployment: zero.**

---

## Recommended next steps after this PR merges

1. **Review the skill specs** (`email_qa/skills/*.md`) — they document the architecture
2. **Skim the Node code** in `email_qa/scripts/` if curious about the pipeline mechanics
3. **Discuss Path A vs B vs C** with the team — this is the only blocker for the runtime PR
4. **When ready, open the follow-up runtime PR**: adds `qa.py`, the chosen runtime infrastructure, and wires it into `app.py` nav

---

## Provenance

The pipeline was built standalone in `ayushi-24git/optum-email-pipeline`. That repo remains the canonical dev environment for QA pipeline changes. PRs to `mat-app` for QA functionality should generally land in `optum-email-pipeline` first, get tested there, then be mirrored over.
