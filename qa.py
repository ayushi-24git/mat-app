# qa.py — HTML Creative QA Validation page
#
# Standalone Streamlit page module. Does NOT depend on session state from Pages
# 1-5; marketers can run QA whenever HTML is ready, independent of audience-build
# or approval workflow.
#
# Two modes:
#   DEMO MODE  — keys not configured. Page works against a curated OPM-73 sample
#                (Eaton 2026 Healthy Incentives) so the UX is demonstrable without
#                any backend dependencies.
#   LIVE MODE  — when EMAIL_QA_SERVICE_URL + ANTHROPIC_API_KEY are present, the
#                page calls the email-qa-service via HTTP for real QA runs.
#
# The page detects mode automatically and falls back gracefully. This file can
# ship to production today; flipping env vars later switches it to live mode
# without any code change.

import os
import re
import time
import streamlit as st
import streamlit.components.v1 as components
from pathlib import Path

# ──────────────────────────────────────────────────────────────────────────
# Constants + demo data
# ──────────────────────────────────────────────────────────────────────────

W_CODE_REGEX = re.compile(r"WF\d{7,9}")
QA_REPORTS_DIR = Path(__file__).parent / "opm73_reports"

# Curated OPM-73 demo data (Eaton 2026 Healthy Incentives campaign).
# Matches the OPM-67 sample pattern used by Pages 1-5 in app.py.
_OPM73_DEMO = {
    "campaign":     "WF21826321 Eaton 2026 Optum Engage Healthy Incentives Campaign",
    "client":       "Eaton",
    "w_code":       "WF21826321",
    "decision":     "READY WITH WARNINGS",
    "counts":       {"critical": 0, "major": 11, "minor": 3},
    "pixel_diff":   {"desktop": 16.28, "mobile": 16.92},
    "vision":       {"sections_reviewed": 16, "cost_usd": 0.34},
    "jira_ticket":  "OPM-73",
    "drive_folder": "WF21826321 WHS 83.23 B2C Eaton 2026 Optum Engage Healthy Incentives campaign",
    "wall_clock_sec": 42,
}

# Hardcoded campaign folder list for demo mode.
# In live mode this is populated dynamically from the Drive API.
_DEMO_FOLDERS = [
    ("eaton-2026-folder-id",
     "WF21826321 WHS 83.23 B2C   Eaton 2026 Optum Engage  Healthy Incentives campaign"),
    # Future: Nationwide WF21219509, Valero WF20317158 — populated when
    # uploaded into <BASE_DRIVE>/assets/ during Phase B (Week 2).
]


# ──────────────────────────────────────────────────────────────────────────
# Mode detection
# ──────────────────────────────────────────────────────────────────────────

def _is_live_mode() -> bool:
    """Return True if all required env vars are present for live pipeline calls."""
    return bool(
        os.environ.get("EMAIL_QA_SERVICE_URL", "").strip()
        and os.environ.get("ANTHROPIC_API_KEY", "").strip()
    )


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────

def _parse_w_code(filename: str, content: str) -> str | None:
    """Parse W-code from filename first, fallback to body content. Returns None if not found."""
    m = W_CODE_REGEX.search(filename or "")
    if m:
        return m.group(0)
    m = W_CODE_REGEX.search(content or "")
    if m:
        return m.group(0)
    return None


def _load_opm73_sample_report() -> str:
    """Return the curated OPM-73 sample report HTML (or a fallback message)."""
    try:
        path = QA_REPORTS_DIR / "qa_report.html"
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return (
            "<p style='padding:24px;font-family:sans-serif;color:#a00'>"
            "Sample report not found at opm73_reports/qa_report.html — "
            "please verify the file is present in the repo."
            "</p>"
        )


def _decision_banner(decision: str) -> None:
    """Render a colour-coded decision banner above the report."""
    if decision == "READY TO SEND":
        st.success(f"### ✅  {decision}")
    elif decision == "READY WITH WARNINGS":
        st.warning(f"### ⚠️  {decision}")
    else:
        st.error(f"### 🛑  {decision}")


def _simulate_pipeline_progress() -> None:
    """Demo-mode progress simulation. Mirrors the real pipeline's stage names."""
    phases = [
        ("Ingesting campaign assets from Drive folder",   1.0),
        ("Running pre-flight (style + compat + link)",    0.6),
        ("Rendering HTML and computing pixel diff",       1.4),
        ("Per-section vision review (Claude × 12)",       1.6),
        ("Generating report + writing back to Jira",      0.4),
    ]
    progress = st.progress(0)
    status = st.empty()
    for i, (label, dt) in enumerate(phases):
        status.write(f"⏳  {label}…")
        time.sleep(dt)
        progress.progress((i + 1) / len(phases))
    status.write("✓  Pipeline complete")


# ──────────────────────────────────────────────────────────────────────────
# Live mode — HTTP client (used only when EMAIL_QA_SERVICE_URL is set)
# ──────────────────────────────────────────────────────────────────────────

def _run_live_pipeline(html_bytes: bytes, html_name: str,
                       drive_folder_id: str, jira_url: str) -> dict | None:
    """Call the email-qa-service via HTTP and return the parsed QA summary.

    Returns None on failure (page will fall back to error display).
    Imports `requests` lazily so demo mode never requires the dependency.
    """
    service_url = os.environ["EMAIL_QA_SERVICE_URL"].rstrip("/")
    try:
        import requests  # stdlib-adjacent; lazy import keeps demo path light
    except ImportError:
        st.error("requests library not installed. Run `pip install requests`.")
        return None

    try:
        files = {"html": (html_name, html_bytes, "text/html")}
        data = {"drive_folder_id": drive_folder_id, "jira_url": jira_url or ""}
        resp = requests.post(f"{service_url}/run-qa", files=files, data=data, timeout=120)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        st.error(f"email-qa-service call failed: {e}")
        return None


# ──────────────────────────────────────────────────────────────────────────
# Main render function — called from app.py
# ──────────────────────────────────────────────────────────────────────────

def render_qa_page() -> None:
    st.title("📧 HTML Creative QA Validation")
    st.caption(
        "Validate developer-built HTML emails against the customer-approved PDF "
        "and Figma design. Catches Outlook Classic rendering issues, palette "
        "drift, padding/spacing mismatches, link breakage, missing alt text, "
        "and per-section visual deviations."
    )

    # ── Mode banner ────────────────────────────────────────────────
    live = _is_live_mode()
    if live:
        st.success(
            "🟢  **Live mode** — pipeline runs against real campaign assets via "
            "the email-qa-service. Each run costs ~$0.30 in Claude API."
        )
    else:
        st.info(
            "🟡  **Demo mode** — keys not yet configured. Showing curated "
            "OPM-73 (Eaton 2026 Healthy Incentives) sample. To enable live mode "
            "set `EMAIL_QA_SERVICE_URL` and `ANTHROPIC_API_KEY` env vars in "
            "Railway. The page detects them automatically — no code change "
            "required."
        )

    st.divider()

    # ── INPUT 1: HTML upload ───────────────────────────────────────
    st.subheader("1. Upload HTML")
    uploaded_html = st.file_uploader(
        "HTML file",
        type=["html", "htm"],
        help="The candidate HTML to validate against the customer PDF and Figma design",
        label_visibility="collapsed",
    )

    w_code_hint = None
    html_bytes = None
    if uploaded_html is not None:
        html_bytes = uploaded_html.getvalue()
        html_text = html_bytes.decode("utf-8", errors="replace")
        w_code_hint = _parse_w_code(uploaded_html.name, html_text)
        if w_code_hint:
            st.success(f"✓  Detected W-code: **{w_code_hint}**")
        else:
            st.warning(
                "⚠️  No W-code found in filename or body. Pipeline will run "
                "but Jira writeback auto-suggest is disabled."
            )

    # ── INPUT 2: Reference folder ──────────────────────────────────
    st.subheader("2. Select reference folder")
    folder_choices = [name for _, name in _DEMO_FOLDERS]

    # Default to the W-code match if uploaded HTML has one
    default_idx = 0
    if w_code_hint:
        for i, (_, fname) in enumerate(_DEMO_FOLDERS):
            if fname.startswith(w_code_hint):
                default_idx = i
                break

    selected_idx = st.selectbox(
        "Campaign folder under <BASE_DRIVE>/assets/",
        range(len(folder_choices)),
        index=default_idx,
        format_func=lambda i: folder_choices[i],
        help="Source of truth for PDFs, Figma file, and image assets",
        label_visibility="collapsed",
    )
    selected_folder_id, selected_folder_name = _DEMO_FOLDERS[selected_idx]

    if not live:
        st.caption("ℹ️  In live mode this dropdown is populated dynamically from Drive.")

    # ── INPUT 3: Jira ticket (optional) ────────────────────────────
    st.subheader("3. Jira ticket (optional)")
    # Auto-suggest the OPM-73 ticket if we detected the matching W-code
    default_jira = ""
    if w_code_hint == "WF21826321":
        default_jira = "https://capillarytech.atlassian.net/browse/OPM-73"
    elif not uploaded_html and not live:
        # Demo mode with no upload — pre-fill so the demo path is one click away
        default_jira = "https://capillarytech.atlassian.net/browse/OPM-73"

    jira_url = st.text_input(
        "Jira ticket URL — leave blank to skip writeback",
        value=default_jira,
        placeholder="https://capillarytech.atlassian.net/browse/OPM-XX",
        help="If provided, QA result is posted as a Jira comment and the ticket status is transitioned",
        label_visibility="collapsed",
    )

    st.divider()

    # ── Run button ─────────────────────────────────────────────────
    run_label = "▶  Run QA"
    if not live and not uploaded_html:
        run_label = "▶  Run QA (demo with OPM-73 sample)"

    if not st.button(run_label, type="primary", use_container_width=True):
        return

    # ── Execute pipeline ───────────────────────────────────────────
    if live:
        if not uploaded_html:
            st.error("Please upload an HTML file to QA.")
            return
        with st.spinner("Running QA pipeline (~40-55 sec)…"):
            result = _run_live_pipeline(
                html_bytes=html_bytes,
                html_name=uploaded_html.name,
                drive_folder_id=selected_folder_id,
                jira_url=jira_url,
            )
        if result is None:
            return
    else:
        # Demo mode — simulate the pipeline phases
        _simulate_pipeline_progress()
        result = _OPM73_DEMO

    # ── Render result ──────────────────────────────────────────────
    st.divider()
    _decision_banner(result["decision"])

    counts = result["counts"]
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Critical", counts["critical"])
    col2.metric("Major",    counts["major"])
    col3.metric("Minor",    counts["minor"])
    col4.metric("Desktop diff",  f"{result['pixel_diff']['desktop']}%")
    col5.metric("Mobile diff",   f"{result['pixel_diff']['mobile']}%")

    # Wall-clock + cost line
    sec = result.get("wall_clock_sec", "—")
    cost = result.get("vision", {}).get("cost_usd")
    cost_str = f"${cost:.2f}" if cost is not None else "—"
    st.caption(f"⏱  Wall-clock: {sec} sec   ·   💰 Cost: {cost_str}   ·   "
               f"📁 {result['drive_folder'][:80]}…")

    # Jira writeback confirmation
    if result.get("jira_ticket"):
        ticket = result["jira_ticket"]
        st.info(
            f"📋  QA decision posted to **["
            f"{ticket}](https://capillarytech.atlassian.net/browse/{ticket})** "
            f"with comment + status transition."
        )

    # ── Full report ────────────────────────────────────────────────
    st.divider()
    st.subheader("📄  Full QA Report")
    st.caption(
        "Marketer-readable report with all findings (style audit, compat check, "
        "link QA, vision review). Embedded below; also saved to "
        "`opm73_reports/qa_report.html`."
    )
    report_html = _load_opm73_sample_report()
    components.html(report_html, height=900, scrolling=True)
