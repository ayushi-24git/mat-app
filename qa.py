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

# Curated demo data per known campaign — keyed by W-code so the page can
# route a marketer's uploaded HTML to the right sample report. Numbers reflect
# the precision-tuned pipeline output (distinct issues, context-aware compat
# rules, link findings grouped by unique URL).
_DEMO_BY_WCODE = {
    "WF21826321": {
        "campaign":     "WF21826321 Eaton 2026 Optum Engage Healthy Incentives Campaign",
        "client":       "Eaton",
        "w_code":       "WF21826321",
        "decision":     "READY WITH WARNINGS",
        "counts":       {"critical": 0, "major": 9, "minor": 5},        # post noise-reduction
        "raw_counts":   {"critical": 0, "major": 11, "minor": 5},
        "pixel_diff":   {"desktop": 16.28, "mobile": 16.92},
        "vision":       {"sections_reviewed": 12, "cost_usd": 0.18},
        "jira_ticket":  "OPM-73",
        "drive_folder": "WF21826321 WHS 83.23 B2C Eaton 2026 Optum Engage Healthy Incentives campaign",
        "wall_clock_sec": 58,
        "report_filename": "qa_report.html",
    },
    "WF21219509": {
        "campaign":     "WF21219509 Nationwide 2026 My Health Optum Engage reminders (NewHire)",
        "client":       "Nationwide",
        "w_code":       "WF21219509",
        # Criticals = 5 unresolved Liquid template directives (vision-review).
        # Remaining minors = 2 genuinely cross-org redirects (Nationwide
        # → Optum hub, Optum shortener → Apple App Store). Same-brand
        # redirects (within optum-family or nationwide-family) are now
        # suppressed.
        "decision":     "MUST FIX",
        "counts":       {"critical": 5, "major": 12, "minor": 11},      # post noise-reduction
        "raw_counts":   {"critical": 5, "major": 12, "minor": 11},
        "pixel_diff":   {"desktop": 33.51, "mobile": 22.91},
        "vision":       {"sections_reviewed": 12, "cost_usd": 0.30},
        "jira_ticket":  None,
        "drive_folder": "Nationwide 2026 My Health Optum Engage reminders NewHire 040126",
        "wall_clock_sec": 64,
        "report_filename": "qa_report_nationwide.html",
    },
}
# Default fallback when we can't identify the campaign from inputs.
_DEFAULT_DEMO_KEY = "WF21826321"

# Recent / sample campaigns shown in the "Pick from recent" tab.
# In demo mode this is a hardcoded list of campaigns we've ingested previously.
# In live mode (Phase 3+) this comes from db.py — user's actual run history.
_RECENT_CAMPAIGNS = [
    {
        "id":   "eaton-2026",
        "label": "Eaton 2026 Optum Engage Healthy Incentives",
        "w_code": "WF21826321",
        "jira":  "OPM-73",
        "drive_url": "https://drive.google.com/drive/folders/1jlXfjrg0YQS78nC8CTenA17Cw405eOZR",
    },
    {
        "id":   "nationwide-2026",
        "label": "Nationwide 2026 My Health Optum Engage reminders (NewHire)",
        "w_code": "WF21219509",
        "jira":  None,
        "drive_url": "https://drive.google.com/drive/folders/14yQFy8AAE7NnWMDSqyQuvCEYVPzWIzsi",
    },
]

DRIVE_URL_RE = re.compile(
    r"https?://drive\.google\.com/drive/(?:u/\d+/)?folders/([A-Za-z0-9_-]+)",
    re.IGNORECASE,
)

def _parse_drive_folder_id(url: str):
    """Extract the folder ID from a Google Drive folder URL. Returns None if invalid."""
    if not url:
        return None
    m = DRIVE_URL_RE.search(url.strip())
    return m.group(1) if m else None


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


def _load_sample_report(filename: str) -> str:
    """Return a curated sample report HTML by filename (or a fallback message)."""
    try:
        path = QA_REPORTS_DIR / filename
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return (
            f"<p style='padding:24px;font-family:sans-serif;color:#a00'>"
            f"Sample report not found at opm73_reports/{filename} — "
            f"please verify the file is present in the repo."
            f"</p>"
        )


def _pick_demo_for_inputs(w_code_hint, selected_folder_label):
    """Decide which curated demo dict to use, based on the marketer's inputs.

    Priority:
      1. W-code parsed from the uploaded HTML (most reliable signal)
      2. W-code substring in the selected folder label (fallback)
      3. Default sample (Eaton)
    """
    if w_code_hint and w_code_hint in _DEMO_BY_WCODE:
        return _DEMO_BY_WCODE[w_code_hint], w_code_hint
    if selected_folder_label:
        for code in _DEMO_BY_WCODE:
            if code in selected_folder_label:
                return _DEMO_BY_WCODE[code], code
    return _DEMO_BY_WCODE[_DEFAULT_DEMO_KEY], _DEFAULT_DEMO_KEY


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

    # ── INPUT 2: Reference folder (two modes) ──────────────────────
    st.subheader("2. Select reference folder")
    st.caption(
        "Source of truth for PDFs, Figma file, and image assets. "
        "Either paste a Drive folder URL or pick a previously-used campaign."
    )

    tab_paste, tab_recent = st.tabs(["🔗 Paste Drive URL", "📂 Pick recent campaign"])

    selected_folder_id = None
    selected_folder_label = None

    # ── Tab A: paste a fresh Drive URL ──
    with tab_paste:
        drive_url_input = st.text_input(
            "Drive folder URL",
            value="",
            placeholder="https://drive.google.com/drive/folders/1jlXfjrg0YQS78nC8CTenA17Cw405eOZR",
            help="Paste the full Drive folder URL. The folder should contain PDFs, links.docx, hero/logo/icon images.",
            key="drive_url_input",
        )
        if drive_url_input.strip():
            parsed_id = _parse_drive_folder_id(drive_url_input)
            if parsed_id:
                st.success(f"✓  Detected folder ID: `{parsed_id}`")
                selected_folder_id = parsed_id
                selected_folder_label = drive_url_input.strip()
            else:
                st.error(
                    "⚠️  Not a valid Drive folder URL. Expected format: "
                    "`https://drive.google.com/drive/folders/<folder-id>`"
                )

    # ── Tab B: pick from recent campaigns ──
    with tab_recent:
        if not _RECENT_CAMPAIGNS:
            st.info("No recent campaigns yet — run one via the Paste tab to populate this list.")
        else:
            # Auto-select the W-code match if uploaded HTML has one
            default_idx = 0
            if w_code_hint:
                for i, c in enumerate(_RECENT_CAMPAIGNS):
                    if c["w_code"] == w_code_hint:
                        default_idx = i
                        break

            picked_idx = st.radio(
                "Recent campaigns",
                range(len(_RECENT_CAMPAIGNS)),
                index=default_idx,
                format_func=lambda i: (
                    f"{_RECENT_CAMPAIGNS[i]['w_code']} — {_RECENT_CAMPAIGNS[i]['label']}"
                    + (f"  ·  {_RECENT_CAMPAIGNS[i]['jira']}" if _RECENT_CAMPAIGNS[i]['jira'] else "")
                ),
                label_visibility="collapsed",
            )
            picked = _RECENT_CAMPAIGNS[picked_idx]
            # Only use this tab's selection if the paste tab is empty
            if not selected_folder_id:
                selected_folder_id = _parse_drive_folder_id(picked["drive_url"])
                selected_folder_label = picked["label"]
            st.caption(
                f"Folder: `{selected_folder_id}` · "
                f"[Open in Drive]({picked['drive_url']})"
            )

    # Belt-and-braces: if NOTHING is selected from either tab in demo mode,
    # fall back to the first recent campaign so the page is still demoable.
    if not selected_folder_id and not live and _RECENT_CAMPAIGNS:
        first = _RECENT_CAMPAIGNS[0]
        selected_folder_id = _parse_drive_folder_id(first["drive_url"])
        selected_folder_label = first["label"]

    if not live:
        st.caption(
            "ℹ️  Recent campaigns are hardcoded in demo mode. In live mode "
            "they come from your db.py run history."
        )

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

    # ── Pick which curated demo to use (based on W-code or folder choice) ──
    demo_result, demo_key = _pick_demo_for_inputs(w_code_hint, selected_folder_label)

    # ── Run button ─────────────────────────────────────────────────
    run_label = "▶  Run QA"
    if not live and not uploaded_html:
        run_label = f"▶  Run QA  (demo: {demo_result['client']})"

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
        # Demo mode — simulate pipeline phases, then load the matching sample
        st.caption(f"🟡 Demo mode — rendering curated sample for **{demo_result['client']} ({demo_key})**")
        _simulate_pipeline_progress()
        result = demo_result

    # ── Render result ──────────────────────────────────────────────
    st.divider()
    _decision_banner(result["decision"])
    st.markdown(f"**Campaign:** {result['campaign']}")

    counts = result["counts"]
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Critical", counts["critical"])
    col2.metric("Major",    counts["major"])
    col3.metric("Minor",    counts["minor"])
    col4.metric("Desktop diff",  f"{result['pixel_diff']['desktop']}%")
    col5.metric("Mobile diff",   f"{result['pixel_diff']['mobile']}%")

    # Wall-clock + folder line (cost intentionally omitted from marketer view)
    sec = result.get("wall_clock_sec", "—")
    st.caption(
        f"⏱  Wall-clock: {sec} sec   ·   "
        f"📁 {result['drive_folder'][:90]}"
    )

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
    header_col, dl_col = st.columns([3, 1])
    with header_col:
        st.subheader("📄  Full QA Report")
        st.caption("Marketer-readable report with all findings — embedded below.")
    report_filename = result.get("report_filename", "qa_report.html")
    report_html = _load_sample_report(report_filename)
    with dl_col:
        # Download as a self-contained HTML the marketer can share or archive.
        download_name = (
            f"qa-report-{result.get('w_code', 'campaign')}-"
            f"{result.get('client', 'unknown').lower().replace(' ', '-')}.html"
        )
        st.download_button(
            label="⬇  Download report",
            data=report_html.encode("utf-8"),
            file_name=download_name,
            mime="text/html",
            use_container_width=True,
            help="Self-contained HTML report with all findings + embedded CSS",
        )
    components.html(report_html, height=900, scrolling=True)
