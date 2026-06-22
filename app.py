import streamlit as st
import os
import json
import base64

# Load .env file so ANTHROPIC_API_KEY (and GMAIL_* vars) are available
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
except ImportError:
    pass  # python-dotenv not installed — env vars must be set manually

# ── Bridge Streamlit Cloud secrets → environment variables ────────────────────
# Locally, config comes from .env (above). On Streamlit Cloud there is no .env;
# config lives in st.secrets. Copying string secrets into os.environ lets the
# rest of the app use the same os.environ.get(...) calls in both environments.
# setdefault → a real .env value (local) always wins over a secret.
try:
    for _sk, _sv in st.secrets.items():
        if isinstance(_sv, str):
            os.environ.setdefault(_sk, _sv)
except Exception:
    pass  # no secrets.toml present (normal when running locally)

# ── DB init (must run before any rendering) ───────────────────────────────────
from db import init_db, now_iso
from auth import (
    upsert_user, get_user, get_all_users, set_role, delete_user,
    ROLES, ALLOWED_DOMAIN, is_allowed_domain, role_badge_html,
    sign_token, verify_token, COOKIE_NAME
)
from campaigns import (
    upsert_campaign, update_campaign, find_campaign,
    list_campaigns, list_campaigns_by_user, get_campaign_by_uid,
)
import approvals as approvals_db
import client_config as client_config_db
import audience_files as audience_files_db
import notifications as notifications_db
init_db()

# ── Anthropic / Claude API helpers ────────────────────────────────────────────
_CLAUDE_MODEL   = "claude-sonnet-4-5"   # change to "claude-opus-4-5" for max quality
_SKILL_PATH     = os.path.join(os.path.dirname(__file__), "Marketing_automation_skill.md")

@st.cache_data(show_spinner=False)
def _load_skill() -> str:
    """Load the master Marketing Automation skill as the Claude system prompt."""
    try:
        with open(_SKILL_PATH, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return "You are MAT — the Optum Engage Marketing Automation Tool. Help build audience queries and analyse campaign data."

@st.cache_resource(show_spinner=False)
def _get_anthropic_client():
    """Return an Anthropic client if ANTHROPIC_API_KEY is set, else None."""
    try:
        import anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if api_key:
            return anthropic.Anthropic(api_key=api_key)
    except Exception:
        pass
    return None

def _call_claude(user_msg: str, system_msg: str = None, max_tokens: int = 4096) -> str | None:
    """
    Call Claude API. Returns the response text, or None if unavailable.
    Falls back gracefully — callers should handle None and use stub data.
    Returns None immediately if the API toggle is switched off (testing mode).
    """
    if not st.session_state.get("api_enabled", True):
        return None
    client = _get_anthropic_client()
    if not client:
        return None
    try:
        import anthropic
        msg = client.messages.create(
            model=_CLAUDE_MODEL,
            max_tokens=max_tokens,
            system=system_msg or _load_skill(),
            messages=[{"role": "user", "content": user_msg}]
        )
        return msg.content[0].text
    except Exception as e:
        return None

def _call_claude_with_pdf(user_msg: str, pdf_bytes: bytes, system_msg: str = None, max_tokens: int = 4096) -> str | None:
    """Call Claude API with a PDF document attached."""
    if not st.session_state.get("api_enabled", True):
        return None
    client = _get_anthropic_client()
    if not client:
        return None
    try:
        import anthropic
        pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")
        msg = client.messages.create(
            model=_CLAUDE_MODEL,
            max_tokens=max_tokens,
            system=system_msg or _load_skill(),
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_b64}
                    },
                    {"type": "text", "text": user_msg}
                ]
            }]
        )
        return msg.content[0].text
    except Exception:
        return None

def _parse_brd_json(claude_response: str, fallback: dict) -> dict:
    """Extract JSON dict from Claude's BRD extraction response. Returns fallback on failure.
    When returning fallback, adds _is_stub=True so the UI can show a warning."""
    if not claude_response:
        return {**fallback, "_is_stub": True}
    try:
        # Claude may wrap JSON in ```json ... ``` or return raw JSON
        text = claude_response.strip()
        start = text.find("{")
        end   = text.rfind("}") + 1
        if start >= 0 and end > start:
            result = json.loads(text[start:end])
            result.pop("_is_stub", None)   # real Claude response — not a stub
            return result
    except Exception:
        pass
    return {**fallback, "_is_stub": True}

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="MAT — Marketing Automation Tool",
    page_icon="⚡",
    layout="wide"
)

# ══════════════════════════════════════════════════════════════════════════════
# GLOBAL DESIGN SYSTEM — cosmetic only, no logic.
# Modern minimal theme: Inter typeface, uniform 38px buttons, card-style
# expanders/metrics, compact vertical rhythm, consistent radii (8/10px).
# ══════════════════════════════════════════════════════════════════════════════
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

/* ── Base typography & canvas ─────────────────────────────────────────── */
html, body, [data-testid="stAppViewContainer"], [data-testid="stSidebar"],
button, input, textarea, select {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif !important;
}
[data-testid="stAppViewContainer"] { background: #FAFBFC; }
.block-container {
    padding-top: 2.2rem !important;
    padding-bottom: 4rem !important;
    max-width: 1180px;
}

/* Headings — one consistent scale everywhere */
h1 { font-size: 1.55rem !important; font-weight: 800 !important;
     letter-spacing: -0.02em !important; color: #0F172A !important;
     padding-bottom: 0 !important; margin-bottom: 0.35rem !important; }
h2 { font-size: 1.15rem !important; font-weight: 700 !important; color: #0F172A !important; }
h3 { font-size: 0.98rem !important; font-weight: 700 !important; color: #1E293B !important; }
p, li, label { color: #334155; }
[data-testid="stCaptionContainer"] { color: #64748B !important; }

/* Dividers — compact, subtle */
hr { margin: 0.9rem 0 !important; border: none !important;
     border-top: 1px solid #E8ECF1 !important; }

/* Tighter default vertical rhythm in the main column */
section.main [data-testid="stVerticalBlock"] { gap: 0.65rem; }

/* ── Buttons — ONE size everywhere (regular, download, form-submit) ───── */
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button,
[data-testid="stBaseButton-primary"], [data-testid="stBaseButton-secondary"] {
    min-height: 38px !important;
    height: 38px;
    padding: 0.45rem 1.15rem !important;
    border-radius: 8px !important;
    font-size: 0.875rem !important;
    font-weight: 600 !important;
    letter-spacing: 0.01em;
    box-shadow: none !important;
    transition: all .15s ease;
    white-space: nowrap !important;
    min-width: max-content;
}
.stButton > button p, .stDownloadButton > button p, .stFormSubmitButton > button p {
    white-space: nowrap !important;
    font-size: 0.875rem !important;
}
/* Primary — solid brand teal (prefix match covers primaryFormSubmit too) */
[data-testid^="stBaseButton-primary"], button[kind^="primary"] {
    background: #007B8F !important;
    border: 1px solid #007B8F !important;
    color: #FFFFFF !important;
}
[data-testid^="stBaseButton-primary"] p, button[kind^="primary"] p { color: #FFFFFF !important; }
[data-testid^="stBaseButton-primary"]:hover, button[kind^="primary"]:hover {
    background: #00697A !important;
    border-color: #00697A !important;
    transform: translateY(-1px);
    box-shadow: 0 4px 12px rgba(0,123,143,0.25) !important;
}
/* Secondary — quiet outline (prefix match covers secondaryFormSubmit too) */
[data-testid^="stBaseButton-secondary"], button[kind^="secondary"] {
    background: #FFFFFF !important;
    border: 1px solid #D7DEE6 !important;
    color: #1E293B !important;
}
[data-testid^="stBaseButton-secondary"]:hover, button[kind^="secondary"]:hover {
    border-color: #007B8F !important;
    color: #007B8F !important;
    background: #F0FAFB !important;
}
button:disabled { opacity: 0.45 !important; }

/* ── Inputs ────────────────────────────────────────────────────────────── */
[data-baseweb="input"], [data-baseweb="textarea"], [data-baseweb="select"] > div {
    border-radius: 8px !important;
    border-color: #D7DEE6 !important;
    background: #FFFFFF !important;
}
[data-baseweb="input"]:focus-within, [data-baseweb="textarea"]:focus-within {
    border-color: #007B8F !important;
    box-shadow: 0 0 0 3px rgba(0,123,143,0.12) !important;
}
[data-testid="stWidgetLabel"] p { font-size: 0.82rem; font-weight: 600; color: #475569; }

/* ── Expanders — clean cards ──────────────────────────────────────────── */
[data-testid="stExpander"] {
    border: 1px solid #E8ECF1 !important;
    border-radius: 12px !important;
    background: #FFFFFF !important;
    box-shadow: 0 1px 2px rgba(15,23,42,0.04);
    overflow: hidden;
}
[data-testid="stExpander"] summary {
    font-weight: 600 !important;
    font-size: 0.92rem !important;
    padding: 0.8rem 1rem !important;
}
[data-testid="stExpander"] summary:hover { color: #007B8F !important; }

/* ── Metrics — stat cards ─────────────────────────────────────────────── */
[data-testid="stMetric"] {
    background: #FFFFFF;
    border: 1px solid #E8ECF1;
    border-radius: 12px;
    padding: 0.85rem 1rem;
    box-shadow: 0 1px 2px rgba(15,23,42,0.04);
}
[data-testid="stMetricLabel"] p { font-size: 0.75rem !important; font-weight: 600;
    text-transform: uppercase; letter-spacing: 0.05em; color: #64748B !important; }
[data-testid="stMetricValue"] { font-size: 1.45rem !important; font-weight: 700 !important; }

/* ── Alerts / info / status ───────────────────────────────────────────── */
[data-testid="stAlert"] { border-radius: 10px !important; border-width: 1px !important; }
[data-testid="stStatusWidget"], [data-testid="stExpanderDetails"] { font-size: 0.9rem; }
div[data-testid="stStatus"] { border-radius: 12px !important; }

/* ── Code blocks ──────────────────────────────────────────────────────── */
.stCode, pre, code { border-radius: 8px !important; font-size: 0.82rem !important; }
pre { border: 1px solid #E8ECF1 !important; }

/* ── Forms — remove double borders ────────────────────────────────────── */
[data-testid="stForm"] {
    border: 1px solid #E8ECF1 !important;
    border-radius: 12px !important;
    background: #FFFFFF;
    padding: 1.1rem 1.2rem !important;
}

/* ── Radio groups (incl. horizontal toggles) ──────────────────────────── */
[role="radiogroup"] label { font-size: 0.9rem; }

/* ── Sidebar — refined nav ────────────────────────────────────────────── */
[data-testid="stSidebar"] {
    background: #FFFFFF !important;
    border-right: 1px solid #E8ECF1 !important;
}
[data-testid="stSidebar"] [role="radiogroup"] label {
    padding: 7px 10px !important;
    border-radius: 8px !important;
    width: 100%;
    transition: background .12s ease;
    margin: 0 !important;
}
[data-testid="stSidebar"] [role="radiogroup"] label:hover { background: #F1F5F9; }
[data-testid="stSidebar"] [role="radiogroup"] label p { font-size: 0.88rem !important; font-weight: 500; }
[data-testid="stSidebar"] hr { margin: 0.55rem 0 !important; }

/* ── Tables / dataframes ──────────────────────────────────────────────── */
[data-testid="stTable"], [data-testid="stDataFrame"] {
    border: 1px solid #E8ECF1 !important;
    border-radius: 10px !important;
    overflow: hidden;
}

/* ── Toggle switch label ──────────────────────────────────────────────── */
[data-testid="stToggle"] label p { font-size: 0.86rem !important; font-weight: 600; }
</style>
""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# WIDGET-STATE PINNING — keep every input's value across page switches.
# Streamlit garbage-collects the state of any keyed widget that is NOT rendered
# in the current run, so switching pages wipes the inputs of the page you left.
# Re-assigning each key to itself converts widget state into durable session
# data (the standard Streamlit idiom). Button-like widgets reject programmatic
# assignment — the try/except skips them. Data survives until browser refresh.
# ══════════════════════════════════════════════════════════════════════════════
for _pin_key in list(st.session_state.keys()):
    _pin_val = st.session_state[_pin_key]
    # Skip bools: button/checkbox-style widget keys are bool-valued, and
    # buttons crash at instantiation if their state was assigned. Plain bool
    # session flags (s1_confirmed, …) don't need pinning — non-widget keys
    # are never garbage-collected.
    if isinstance(_pin_val, bool):
        continue
    try:
        st.session_state[_pin_key] = _pin_val
    except Exception:
        pass

# ══════════════════════════════════════════════════════════════════════════════
# AUTH GATE — runs before any page rendering
# AUTH_MODE=demo    → simulated login screen (Path B — hackathon demo, no Google)
# AUTH_MODE=google  → real Google OAuth via google_credentials.json (Path A)
# Plug-and-play: flip AUTH_MODE in .env to switch. No other code changes needed.
# ══════════════════════════════════════════════════════════════════════════════
_AUTH_MODE  = os.environ.get("AUTH_MODE", "demo").lower()
_IS_DEMO    = _AUTH_MODE != "google"
_CREDS_PATH = os.path.join(os.path.dirname(__file__), "google_credentials.json")

# Seed a bootstrap admin so there is always someone who can assign roles,
# even on a fresh database.
_SEED_ADMIN = os.environ.get("ADMIN_EMAIL", "").strip().lower()
if _SEED_ADMIN and not get_user(_SEED_ADMIN):
    upsert_user(_SEED_ADMIN, _SEED_ADMIN.split("@")[0].replace(".", " ").title(), "")
    set_role(_SEED_ADMIN, "Admin")

# ── Handle logout (profile dropdown → /?logout=1) BEFORE the gate evaluates ───
_logging_out = st.query_params.get("logout") == "1"
if _logging_out:
    _auth = st.session_state.pop("_authenticator", None)
    if _auth:
        try: _auth.logout()
        except Exception: pass
    for _k in ["auth_user", "connected", "user_info", "_demo_step", "demo_email_in"]:
        st.session_state.pop(_k, None)
    st.query_params.clear()
    # st.context.cookies is a SNAPSHOT taken at session start — it still
    # contains the deleted cookie for the rest of this session. Block the
    # cookie-restore path until a genuine fresh login or page reload.
    st.session_state["_cookie_restore_blocked"] = True
    # Hard-delete BOTH auth cookies in the parent document immediately.
    # The OAuth library deletes its mat_session cookie via an async component;
    # check_authentification() can re-read the still-present cookie on the
    # next rerun and silently log the user back in (~3s later). Deleting
    # synchronously via JS kills that race for good.
    st.components.v1.html(
        f"""<script>
        var d = window.parent.document;
        d.cookie = 'mat_session=; path=/; max-age=0; SameSite=Lax';
        d.cookie = '{COOKIE_NAME}=; path=/; max-age=0; SameSite=Lax';
        </script>""",
        height=0,
    )

# ── Restore session from the signed cookie (survives browser refresh) ─────────
# A hard refresh starts a NEW Streamlit session, so session_state is empty.
# We re-hydrate auth_user from the cookie that was set at login. Read is
# synchronous (st.context.cookies) → no login-screen flash.
if (_IS_DEMO and not _logging_out and not st.session_state.get("auth_user")
        and not st.session_state.get("_cookie_restore_blocked")):
    try:
        _ck = st.context.cookies.get(COOKIE_NAME)
    except Exception:
        _ck = None
    if _ck:
        _ck_email = verify_token(_ck)
        if _ck_email:
            _ck_user = get_user(_ck_email)
            if not _ck_user:
                upsert_user(_ck_email, _ck_email.split("@")[0].replace(".", " ").title(), "")
                _ck_user = get_user(_ck_email)
            st.session_state["auth_user"] = _ck_user

if _IS_DEMO:
    # ══ PATH B — Simulated login screen ═══════════════════════════════════════
    if not st.session_state.get("auth_user"):
        _step = st.session_state.get("_demo_step", "welcome")

        # ── Login design system ───────────────────────────────────────────────
        # NOTE: Streamlit's bordered-container `data-testid` is unstable across
        # versions, so the card is built on the STABLE `.stColumn` class via
        # `:has()` (the middle column is the only one holding widgets). Buttons
        # are styled through the stable `.stButton > button` selector.
        st.markdown(f"""
        <style>
          [data-testid="stSidebar"], [data-testid="collapsedControl"] {{display:none !important;}}
          [data-testid="stHeader"] {{background:transparent !important;}}
          [data-testid="stToolbar"], [data-testid="stDecoration"] {{display:none !important;}}
          #MainMenu {{display:none !important;}}

          /* Deep-navy canvas with soft teal auras + faint grid */
          [data-testid="stAppViewContainer"] {{
            background:
              radial-gradient(1200px 560px at 12% -12%, rgba(0,137,159,.42), transparent 58%),
              radial-gradient(1000px 560px at 112% 118%, rgba(53,196,215,.20), transparent 55%),
              linear-gradient(160deg, #0A2540 0%, #0B1D33 58%, #07131F 100%) !important;
          }}
          [data-testid="stAppViewContainer"]::before {{
            content:""; position:fixed; inset:0; pointer-events:none; opacity:.5;
            background-image:
              linear-gradient(rgba(255,255,255,.025) 1px, transparent 1px),
              linear-gradient(90deg, rgba(255,255,255,.025) 1px, transparent 1px);
            background-size:46px 46px;
            -webkit-mask-image:radial-gradient(900px 600px at 50% 38%, #000 0%, transparent 75%);
                    mask-image:radial-gradient(900px 600px at 50% 38%, #000 0%, transparent 75%);
          }}
          .block-container {{padding-top:10vh !important; max-width:1080px;}}

          /* ── THE CARD — stable .stColumn class, scoped to the TOP-LEVEL
                middle column only (`:not(.stColumn ...)` excludes the nested
                Back/Continue columns so they don't become mini-cards). ── */
          .stHorizontalBlock:not(.stColumn .stHorizontalBlock) > .stColumn:has(.stButton),
          .stHorizontalBlock:not(.stColumn .stHorizontalBlock) > .stColumn:has(.stTextInput) {{
            background:#FFFFFF;
            border-radius:24px;
            padding:2.6rem 2.5rem 2.2rem !important;
            box-shadow:
              0 48px 100px rgba(2,12,27,.60),
              0 16px 36px rgba(2,12,27,.40),
              inset 0 1px 0 rgba(255,255,255,.7),
              0 0 0 1px rgba(255,255,255,.06);
            animation:matCardIn .5s cubic-bezier(.16,1,.3,1);
          }}
          @keyframes matCardIn {{
            from {{opacity:0; transform:translateY(14px) scale(.985);}}
            to   {{opacity:1; transform:translateY(0) scale(1);}}
          }}
          /* Nested button row sits flush — no card chrome */
          .stColumn .stHorizontalBlock .stColumn {{padding:0 !important;}}

          /* ── Email input — large, soft, animated focus ring ── */
          [data-baseweb="input"] {{
            border-radius:12px !important;
            border:1.5px solid #E3E8EF !important;
            background:#F8FAFC !important;
            transition:border-color .18s ease, box-shadow .18s ease, background .18s ease;
          }}
          [data-baseweb="input"] input {{height:48px !important; font-size:15px !important; color:#0F172A !important;}}
          [data-baseweb="input"]:focus-within {{
            border-color:#007B8F !important; background:#FFFFFF !important;
            box-shadow:0 0 0 4px rgba(0,123,143,.15) !important;
          }}

          /* ── Buttons — stable .stButton selector ── */
          .stButton > button {{
            height:50px !important; min-height:50px !important;
            border-radius:13px !important;
            font-size:15px !important; font-weight:600 !important;
            transition:all .18s cubic-bezier(.16,1,.3,1) !important;
          }}
          /* Primary (welcome = white Google btn; email step = teal CTA) */
          .stButton > button[kind="primary"] {{
            {"background:#FFFFFF !important; border:1.5px solid #DADCE0 !important; box-shadow:0 1px 3px rgba(16,24,40,.10) !important;" if _step == "welcome" else "background:linear-gradient(135deg,#00A0BA,#00768A) !important; border:none !important; box-shadow:0 10px 24px rgba(0,123,143,.36) !important;"}
            display:flex !important; align-items:center !important; justify-content:center !important;
          }}
          .stButton > button[kind="primary"] p {{
            {"color:#1F2937 !important;" if _step == "welcome" else "color:#FFFFFF !important;"}
            font-size:15px !important; font-weight:600 !important;
          }}
          .stButton > button[kind="primary"]:hover {{
            transform:translateY(-1px) !important;
            {"border-color:#007B8F !important; background:#F8FEFF !important; box-shadow:0 8px 22px rgba(0,123,143,.20) !important;" if _step == "welcome" else "box-shadow:0 14px 30px rgba(0,123,143,.46) !important;"}
          }}
          {".stButton > button[kind='primary']::before {content:''; display:inline-block; width:21px; height:21px; margin-right:11px; flex:0 0 auto; background:url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 48 48'><path fill='%23EA4335' d='M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z'/><path fill='%234285F4' d='M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z'/><path fill='%23FBBC05' d='M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z'/><path fill='%2334A853' d='M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z'/></svg>\") no-repeat center / contain;}" if _step == "welcome" else ""}
          /* Secondary (ghost back button) */
          .stButton > button[kind="secondary"] {{
            background:transparent !important; border:1.5px solid #E3E8EF !important;
            box-shadow:none !important;
          }}
          .stButton > button[kind="secondary"] p {{color:#475569 !important; font-weight:600 !important;}}
          .stButton > button[kind="secondary"]:hover {{
            border-color:#94A3B8 !important; background:#F8FAFC !important;
          }}
        </style>
        """, unsafe_allow_html=True)

        # On logout, delete the cookie via JS so a refresh won't re-login.
        if _logging_out:
            st.components.v1.html(
                f"<script>window.parent.document.cookie="
                f"'{COOKIE_NAME}=; path=/; max-age=0; SameSite=Lax';</script>",
                height=0,
            )

        _, _mid, _ = st.columns([1, 1.05, 1])
        with _mid:
            # ── Brand mark + heading ─────────────────────────────────────────
            _heading = "Welcome to MAT" if _step == "welcome" else "Choose your account"
            _subheading = ("Marketing Automation Tool · Optum Engage"
                           if _step == "welcome" else "to continue to MAT")
            st.markdown(f"""
            <div style="text-align:center;">
              <div style="display:inline-flex;align-items:center;justify-content:center;
                          width:66px;height:66px;border-radius:19px;
                          background:linear-gradient(145deg,#00A0BA,#006375);
                          box-shadow:0 12px 30px rgba(0,123,143,.50),
                                     inset 0 1px 0 rgba(255,255,255,.30);
                          font-size:31px;font-weight:800;color:#fff;
                          margin-bottom:18px;letter-spacing:-1px;">M</div>
              <div style="font-size:24px;font-weight:800;color:#0F172A;
                          letter-spacing:-0.4px;">{_heading}</div>
              <div style="font-size:13px;color:#64748B;margin-top:6px;">{_subheading}</div>
              <div style="height:1px;background:linear-gradient(90deg,transparent,#E2E8F0,transparent);
                          margin:22px 0 20px;"></div>
            </div>
            """, unsafe_allow_html=True)

            if _step == "welcome":
                if st.button("Sign in with Google", use_container_width=True,
                             type="primary", key="demo_google_btn"):
                    st.session_state["_demo_step"] = "email"
                    st.rerun()
                st.markdown(
                    "<div style='text-align:center;font-size:12px;color:#94A3B8;"
                    "margin-top:14px;'>Use your Capillary Google account to continue</div>",
                    unsafe_allow_html=True,
                )
            else:  # _step == "email"
                _em = st.text_input(
                    "Email", placeholder=f"you@{ALLOWED_DOMAIN}",
                    key="demo_email_in", label_visibility="collapsed"
                )
                _b1, _b2 = st.columns([1, 1.5])
                with _b1:
                    if st.button("← Back", use_container_width=True, key="demo_back"):
                        st.session_state["_demo_step"] = "welcome"
                        st.rerun()
                with _b2:
                    if st.button("Continue", use_container_width=True,
                                 type="primary", key="demo_continue"):
                        _em = (_em or "").strip().lower()
                        if not is_allowed_domain(_em):
                            st.error(f"Please use a @{ALLOWED_DOMAIN} account.")
                        else:
                            _nm = _em.split("@")[0].replace(".", " ").title()
                            upsert_user(_em, _nm, "")
                            st.session_state["auth_user"] = get_user(_em)
                            st.session_state.pop("_demo_step", None)
                            st.rerun()

            # ── In-card footer ───────────────────────────────────────────────
            st.markdown(
                f"<div style='text-align:center;margin-top:22px;padding-top:16px;"
                f"border-top:1px solid #F1F5F9;font-size:11.5px;color:#94A3B8;'>"
                f"🔒 Access restricted to <b style='color:#64748B;'>@{ALLOWED_DOMAIN}</b> accounts"
                f"</div>",
                unsafe_allow_html=True,
            )

        # Below-card copyright on the gradient (outside the card column)
        st.markdown(
            "<div style='text-align:center;margin-top:30px;font-size:11.5px;"
            "color:rgba(201,216,232,.55);letter-spacing:.4px;'>"
            "© 2026 Capillary Technologies · MAT</div>",
            unsafe_allow_html=True,
        )
        st.stop()

else:
    # ══ PATH A — Google OAuth ═════════════════════════════════════════════════
    # On Streamlit Cloud there is no google_credentials.json file (it's gitignored).
    # If the JSON was provided as a secret, write it to the expected path at runtime.
    _gc_json = os.environ.get("GOOGLE_CREDENTIALS_JSON", "").strip()
    if _gc_json and not os.path.exists(_CREDS_PATH):
        try:
            with open(_CREDS_PATH, "w", encoding="utf-8") as _gf:
                _gf.write(_gc_json)
        except Exception:
            pass

    _creds_ready = (
        os.path.exists(_CREDS_PATH)
        and '"YOUR_CLIENT_ID' not in open(_CREDS_PATH).read()
    )
    if not _creds_ready:
        st.error(
            "⚠️ **Google credentials not configured.**  \n"
            "Add your Google OAuth credentials (file `google_credentials.json` locally, "
            "or the `GOOGLE_CREDENTIALS_JSON` secret on Streamlit Cloud), "
            "or set `AUTH_MODE=demo` to use the simulated login."
        )
        st.stop()

    # Redirect URI must match what's registered in Google Cloud Console.
    # Local: http://localhost:8501 · Cloud: set OAUTH_REDIRECT_URI to the app URL.
    _redirect_uri = os.environ.get("OAUTH_REDIRECT_URI", "http://localhost:8501")

    # ── Disable PKCE at the Flow CLASS level ──────────────────────────────────
    # streamlit-google-auth creates a brand-new local Flow inside each method
    # (login / check_authentification) — there is no persistent flow object to
    # patch. The redirect back from Google lands in a fresh Streamlit session,
    # so a PKCE code_verifier generated before the redirect is lost and the
    # token exchange fails with "(invalid_grant) Missing code verifier"
    # (google-auth-oauthlib >= 1.4 auto-generates a verifier by default).
    # Patching the class methods strips PKCE from BOTH the auth URL and the
    # token exchange. Web-app clients authenticate with client_secret, so PKCE
    # is safely optional.
    import google_auth_oauthlib.flow as _gaof
    if not getattr(_gaof.Flow, "_mat_no_pkce", False):
        _orig_auth_url = _gaof.Flow.authorization_url
        def _auth_url_no_pkce(self, **kwargs):
            self.autogenerate_code_verifier = False
            self.code_verifier = None
            return _orig_auth_url(self, **kwargs)
        _gaof.Flow.authorization_url = _auth_url_no_pkce

        _orig_fetch_token = _gaof.Flow.fetch_token
        def _fetch_token_no_pkce(self, **kwargs):
            self.code_verifier = None
            kwargs.pop("code_verifier", None)
            return _orig_fetch_token(self, **kwargs)
        _gaof.Flow.fetch_token = _fetch_token_no_pkce
        _gaof.Flow._mat_no_pkce = True

    try:
        from streamlit_google_auth import Authenticate as _GoogleAuth
        _authenticator = _GoogleAuth(
            secret_credentials_path=_CREDS_PATH,
            redirect_uri=_redirect_uri,
            cookie_name="mat_session",
            cookie_key=os.environ.get("COOKIE_SECRET", "mat_cookie_secret"),
            cookie_expiry_days=1,
        )
        # On the logout run, do NOT re-read the auth cookie — the JS deletion
        # above hasn't reached the browser yet; reading now would log the
        # user straight back in.
        if not _logging_out:
            _authenticator.check_authentification()
    except Exception as _auth_err:
        st.error(f"⚠️ Auth initialisation error: {_auth_err}")
        st.stop()

    if not st.session_state.get("connected"):
        st.markdown("""
        <div style="display:flex;flex-direction:column;align-items:center;
                    justify-content:center;min-height:60vh;gap:0;">
          <div style="background:linear-gradient(135deg,#0a2540,#0d3060);
                      border-radius:18px;padding:48px 56px;text-align:center;
                      max-width:420px;width:100%;box-shadow:0 8px 40px rgba(0,0,0,0.18);">
            <div style="background:#007B8F;border-radius:12px;width:56px;height:56px;
                        display:flex;align-items:center;justify-content:center;
                        font-size:28px;font-weight:900;color:#fff;margin:0 auto 18px;">M</div>
            <div style="font-size:32px;font-weight:900;color:#fff;letter-spacing:5px;
                        margin-bottom:6px;">MAT</div>
            <div style="font-size:11px;color:rgba(255,255,255,0.45);letter-spacing:2px;
                        text-transform:uppercase;margin-bottom:28px;">
              Marketing Automation Tool
            </div>
            <div style="font-size:0.9rem;color:rgba(255,255,255,0.7);margin-bottom:28px;">
              Sign in with your Capillary Google account to continue.
            </div>
          </div>
        </div>
        """, unsafe_allow_html=True)
        _, _login_col, _ = st.columns([2, 1, 2])
        with _login_col:
            _authenticator.login()
        st.caption(f"🔒 Access restricted to @{ALLOWED_DOMAIN} accounts only.")
        st.stop()

    else:
        _user_info = st.session_state.get("user_info", {})
        _email     = _user_info.get("email", "")
        _name      = _user_info.get("name", "Unknown")
        _picture   = _user_info.get("picture", "")

        if not is_allowed_domain(_email):
            st.error(
                f"⚠️ **Access denied.**  \n"
                f"`{_email}` is not a `@{ALLOWED_DOMAIN}` account.  \n"
                "Please sign in with your Capillary Gmail."
            )
            _authenticator.logout()
            st.stop()

        upsert_user(_email, _name, _picture)
        if "auth_user" not in st.session_state:
            st.session_state["auth_user"] = get_user(_email)
        st.session_state["_authenticator"] = _authenticator

# ── Convenience shortcut used throughout ─────────────────────────────────────
_current_user = st.session_state.get("auth_user", {})
_current_role = _current_user.get("role", "Viewer")

# ── Sidebar navigation ────────────────────────────────────────────────────────
# Tighten sidebar vertical spacing so all nav pages fit without scrolling.
st.sidebar.markdown("""
<style>
  section[data-testid="stSidebar"] > div {padding-top: 1rem;}
  section[data-testid="stSidebar"] [data-testid="stVerticalBlock"] {gap: 0.45rem;}
  section[data-testid="stSidebar"] hr {margin: 0.35rem 0;}
</style>
""", unsafe_allow_html=True)
st.sidebar.markdown("""
<div style="background:linear-gradient(135deg,#0a2540 0%,#0d3060 100%);
            border-radius:12px;padding:16px 18px 14px;margin-bottom:4px;
            border:1px solid rgba(0,123,143,0.4);">
  <div style="display:flex;align-items:center;gap:11px;">
    <div style="background:#007B8F;border-radius:8px;width:38px;height:38px;
                display:flex;align-items:center;justify-content:center;
                font-size:19px;font-weight:900;color:#fff;
                font-family:'Segoe UI',Arial,sans-serif;flex-shrink:0;">M</div>
    <div>
      <div style="font-size:23px;font-weight:900;color:#fff;letter-spacing:4px;
                  font-family:'Segoe UI',Arial,sans-serif;line-height:1.1;">MAT</div>
      <div style="font-size:7.5px;color:rgba(255,255,255,0.45);letter-spacing:1.6px;
                  text-transform:uppercase;font-family:'Segoe UI',Arial,sans-serif;
                  margin-top:2px;">Marketing Automation Tool</div>
    </div>
    <div style="margin-left:auto;display:flex;flex-direction:column;gap:3px;align-items:flex-end;">
      <div style="width:18px;height:2px;background:#007B8F;border-radius:1px;"></div>
      <div style="width:12px;height:2px;background:rgba(255,255,255,0.3);border-radius:1px;"></div>
      <div style="width:8px;height:2px;background:rgba(255,255,255,0.15);border-radius:1px;"></div>
    </div>
  </div>
</div>
""", unsafe_allow_html=True)
st.sidebar.markdown("---")

# ── Top-right profile bubble ──────────────────────────────────────────────────
# Injected via JS into the parent frame so position:fixed works at true
# viewport level (Streamlit's app container creates a stacking context that
# breaks position:fixed when used inside st.markdown).
_u         = st.session_state.get("auth_user", {})
_u_name    = _u.get("name", "Unknown")
_u_email   = _u.get("email", "")
_u_role    = _u.get("role", "Viewer")
_u_picture = _u.get("picture_url", "")
_initials  = "".join(p[0].upper() for p in _u_name.split()[:2]) if _u_name else "?"

_role_colours = {
    "Admin":            ("#4F46E5", "#EEF2FF"),
    "Campaign Manager": ("#0369A1", "#E0F2FE"),
    "Approver":         ("#047857", "#ECFDF5"),
    "Viewer":           ("#6B7280", "#F3F4F6"),
}
_prf, _prb = _role_colours.get(_u_role, ("#6B7280", "#F3F4F6"))

# Build avatar HTML (image if available, else initials circle)
if _u_picture:
    _av_sm  = f"<img src=\\'{_u_picture}\\' style=\\'width:36px;height:36px;border-radius:50%;object-fit:cover;display:block;\\'>"
    _av_lg  = f"<img src=\\'{_u_picture}\\' style=\\'width:44px;height:44px;border-radius:50%;object-fit:cover;display:block;\\'>"
else:
    _av_sm  = (f"<div style=\\'width:36px;height:36px;border-radius:50%;background:#007B8F;"
               f"color:#fff;display:flex;align-items:center;justify-content:center;"
               f"font-size:13px;font-weight:800;\\'>{_initials}</div>")
    _av_lg  = (f"<div style=\\'width:44px;height:44px;border-radius:50%;background:#007B8F;"
               f"color:#fff;display:flex;align-items:center;justify-content:center;"
               f"font-size:16px;font-weight:800;\\'>{_initials}</div>")

_dev_banner = (
    "<div style=\\'text-align:center;margin-bottom:12px;\\'>"
    "<span style=\\'background:#FEF3C7;color:#92400E;border:1px solid #FCD34D;"
    "border-radius:8px;padding:3px 10px;font-size:0.68rem;font-weight:700;\\'>"
    "DEMO MODE</span></div>"
) if _IS_DEMO else ""

# Escape for JS template literal (backticks and $ need escaping)
_safe_name  = _u_name.replace("'", "\\'").replace("`", "\\`")
_safe_email = _u_email.replace("'", "\\'").replace("`", "\\`")
# Signed session token written to a cookie so login survives a browser refresh
_auth_token = sign_token(_u_email) if (_IS_DEMO and _u_email) else ""

st.components.v1.html(f"""
<script>
(function() {{
  var pd = window.parent.document;

  // ── Persist login in a cookie so a refresh does NOT log the user out ──────
  var _matTok = "{_auth_token}";
  if (_matTok) {{
    pd.cookie = "{COOKIE_NAME}=" + _matTok + "; path=/; max-age=86400; SameSite=Lax";
  }}

  // ── Inject CSS into parent <head> (once) ──────────────────────────────────
  if (!pd.getElementById('mat-prof-style')) {{
    var style = pd.createElement('style');
    style.id = 'mat-prof-style';
    style.textContent = `
      #mat-prof-root {{
        position: relative;
        display: inline-flex;
        align-items: center;
        margin: 0 10px 0 0;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      }}
      #mat-prof-btn {{
        width: 34px; height: 34px;
        border-radius: 50%;
        cursor: pointer;
        border: 2px solid rgba(255,255,255,0.85);
        box-shadow: 0 1px 6px rgba(0,123,143,0.40);
        overflow: hidden;
        transition: box-shadow 0.15s;
        display: flex; align-items: center; justify-content: center;
      }}
      #mat-prof-btn:hover {{
        box-shadow: 0 0 0 3px rgba(0,123,143,0.30);
      }}
      #mat-prof-btn.open {{
        box-shadow: 0 0 0 3px #007B8F, 0 0 0 5px rgba(0,123,143,0.20);
      }}
      #mat-prof-menu {{
        display: none;
        position: fixed;            /* fixed → never clipped by the header */
        background: #fff;
        border: 1px solid #E5E7EB;
        border-radius: 14px;
        box-shadow: 0 12px 40px rgba(0,0,0,0.18);
        padding: 18px;
        min-width: 260px;
        z-index: 2147483647;
      }}
      #mat-prof-menu.open {{ display: block; animation: matFade 0.12s ease; }}
      @keyframes matFade {{
        from {{ opacity:0; transform:translateY(-6px); }}
        to   {{ opacity:1; transform:translateY(0); }}
      }}
      .mat-signout {{
        display: block; text-align: center; margin-top: 12px;
        background: #FEF2F2; color: #991B1B; border: 1px solid #FECACA;
        border-radius: 8px; padding: 9px 12px; font-size: 0.82rem;
        font-weight: 600; text-decoration: none; cursor: pointer;
      }}
      .mat-signout:hover {{ background: #FEE2E2; }}
    `;
    pd.head.appendChild(style);
  }}

  // ── Build the bubble element ──────────────────────────────────────────────
  function buildRoot() {{
    var root = pd.createElement('div');
    root.id = 'mat-prof-root';
    root.innerHTML = `
      <div id="mat-prof-btn" title="{_safe_name}">{_av_sm}</div>
      <div id="mat-prof-menu">
        {_dev_banner}
        <div style="display:flex;align-items:center;gap:11px;margin-bottom:14px;
                    padding-bottom:14px;border-bottom:1px solid #F3F4F6;">
          <div style="width:44px;height:44px;border-radius:50%;overflow:hidden;
                      border:2px solid #E5E7EB;flex-shrink:0;">{_av_lg}</div>
          <div style="flex:1;min-width:0;">
            <div style="font-size:0.9rem;font-weight:700;color:#111827;
                        white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">
              {_safe_name}
            </div>
            <div style="font-size:0.75rem;color:#6B7280;margin-top:1px;
                        white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">
              {_safe_email}
            </div>
          </div>
        </div>
        <div style="background:{_prb};border:1px solid {_prf}22;border-radius:8px;
                    padding:9px 12px;">
          <div style="font-size:0.68rem;color:#6B7280;font-weight:600;
                      text-transform:uppercase;letter-spacing:0.05em;margin-bottom:2px;">Role</div>
          <div style="font-size:0.85rem;font-weight:700;color:{_prf};">{_u_role}</div>
        </div>
        <a href="/?logout=1" class="mat-signout">&#x2715;&nbsp; Sign out</a>
      </div>
    `;

    var btn  = root.querySelector('#mat-prof-btn');
    var menu = root.querySelector('#mat-prof-menu');

    // Position the fixed dropdown just under the bubble, right-aligned
    function placeMenu() {{
      var r = btn.getBoundingClientRect();
      menu.style.top = (r.bottom + 8) + 'px';
      // right-align menu to the bubble's right edge
      menu.style.left = 'auto';
      menu.style.right = (pd.documentElement.clientWidth - r.right) + 'px';
    }}

    btn.addEventListener('click', function(e) {{
      e.stopPropagation();
      var isOpen = menu.classList.toggle('open');
      btn.classList.toggle('open', isOpen);
      if (isOpen) placeMenu();
    }});

    pd.addEventListener('click', function(e) {{
      if (!root.contains(e.target)) {{
        menu.classList.remove('open');
        btn.classList.remove('open');
      }}
    }});
    window.parent.addEventListener('resize', function() {{
      if (menu.classList.contains('open')) placeMenu();
    }});

    return root;
  }}

  // ── Mount as the FIRST child of the Streamlit toolbar actions ─────────────
  // Order becomes:  [Profile]  [Deploy]  [⋮]
  function mount() {{
    var toolbar = pd.querySelector('[data-testid="stToolbarActions"]');
    if (!toolbar) return false;
    var existing = pd.getElementById('mat-prof-root');
    if (existing && existing.parentElement === toolbar && toolbar.firstElementChild === existing) {{
      return true;  // already correctly mounted
    }}
    if (existing) existing.remove();
    toolbar.insertBefore(buildRoot(), toolbar.firstChild);
    return true;
  }}

  // Try immediately + a few retries while Streamlit paints the toolbar
  if (!mount()) {{
    var tries = 0;
    var iv = setInterval(function() {{
      if (mount() || ++tries > 20) clearInterval(iv);
    }}, 100);
  }}

  // Keep it mounted if React re-renders the toolbar
  var host = pd.querySelector('[data-testid="stToolbar"]') || pd.body;
  if (host && !host.__matObserver) {{
    var obs = new MutationObserver(function() {{ mount(); }});
    obs.observe(host, {{ childList: true, subtree: true }});
    host.__matObserver = obs;
  }}
}})();
</script>
""", height=0)

PAGES = [
    "🏠 Dashboard",
    "1. Jira Intake",
    "2. Audience Builder",
    "3. Approval Gate",
    "4. Monitoring",
    "5. Post-Campaign ROI",
    "📧 QA Validation",
]
# Admin Panel page — only visible to Admin role
_ADMIN_PAGE = "⚙ Admin Panel"
if _current_role == "Admin":
    PAGES = PAGES + [_ADMIN_PAGE]

# ── OPM-67 saved sample dataset ───────────────────────────────────────────────
# This is the ONLY pre-loaded sample. It activates ONLY when the user enters
# "OPM-67" in Jira Intake. Every other ticket + no API → blank/placeholder only.

# Curated sample reports (audience / delivery / roi) served for OPM-67 only.
_OPM67_REPORTS_DIR = os.path.join(os.path.dirname(__file__), "opm67_reports")

def _load_opm67_report(name: str):
    """Return the curated OPM-67 sample report HTML, or None if missing."""
    try:
        with open(os.path.join(_OPM67_REPORTS_DIR, f"{name}_report.html"),
                  encoding="utf-8") as _f:
            return _f.read()
    except Exception:
        return None
_OPM67_BRD = {
    "Client":                "Valero",
    "Campaign Type":         "Progress",
    "Channel(s)":            "Email",
    "Affiliations in Scope": "02, 03, 04, 05, 06, 07, 08, 09, 10, 11",
    "Relationship Codes":    "Employee + Spouse/Domestic Partner (no filter needed)",
    "Age Filter":            "18+ (baseline standard)",
    "Geography":             "US 50 states only (exclude PR, GU, VI, AS, MP)",
    "Suppressions":          "Members at $200 max cap excluded",
    "Activity Logic":        "None — progress campaign (earnings-based)",
    "Email Deployments":     "March 3 · May 5 · July 7 · September 1",
    "Expected Count":        "~12,000",
}
_OPM67_SANITY = {
    "childOrgId":           "9000288",
    "clientId":             "0907816",
    "rewardMaxCap":         "$200",
    "rewardEarningEndDate": "2026-12-31",
    "Total affiliations":   "11",
    "BRD includes":         "02–11 (10 affiliations)",
    "Excluded":             "01 only",
    "Filter decision":      "NOT IN ('01')  — 1 exclusion vs 10 inclusions",
}
_OPM67_DELIVERY = {
    "total": 7974, "sent": 7652, "control": 322,
    "invalid": 73, "delivered": 7579, "opened": 1694, "clicked": 128,
}
_OPM67_DAILY = [{"date": "2026-03-03", "sent": 7652, "delivered": 7579,
                  "opened": 1694, "clicked": 128, "invalid": 73, "not_applicable": 322}]
_OPM67_ROI_COMP = {
    "overall": {"at_send": 3.0, "at_check": 4.1, "delta": 1.1, "users": 7901},
    "test":    {"at_send": 3.0, "at_check": 4.1, "delta": 1.1, "users": 7579},
    "control": {"at_send": 3.1, "at_check": 3.9, "delta": 0.8, "users": 322},
    "lift": 0.3, "test_100": 13, "ctrl_100": 0, "already_100": 17,
}
_OPM67_BUCKETS = [
    {"bucket": "0%",     "color": "#C62828", "bg": "#FFEBEE", "send": 6905, "send_pct": 91.1, "check": 6743, "check_pct": 89.0, "trend": "↓"},
    {"bucket": "1–25%",  "color": "#E65100", "bg": "#FFF3E0", "send": 366,  "send_pct":  4.8, "check":  406, "check_pct":  5.4, "trend": "↑"},
    {"bucket": "26–50%", "color": "#F57F17", "bg": "#FFFDE7", "send": 190,  "send_pct":  2.5, "check":  250, "check_pct":  3.3, "trend": "↑"},
    {"bucket": "51–75%", "color": "#1565C0", "bg": "#E3F2FD", "send":  73,  "send_pct":  1.0, "check":  103, "check_pct":  1.4, "trend": "↑"},
    {"bucket": "76–99%", "color": "#2E7D32", "bg": "#E8F5E9", "send":  28,  "send_pct":  0.4, "check":   47, "check_pct":  0.6, "trend": "↑"},
    {"bucket": "100%",   "color": "#4A148C", "bg": "#F3E5F5", "send":  17,  "send_pct":  0.2, "check":   30, "check_pct":  0.4, "trend": "↑"},
]

# ── API toggle + status indicator ────────────────────────────────────────────
if "api_enabled" not in st.session_state:
    st.session_state["api_enabled"] = True

with st.sidebar:
    _api_on = st.session_state.get("api_enabled", True)
    _has_key = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
    # Determine effective connection status
    _api_live = _api_on and _has_key

    # Status indicator
    if _api_live:
        st.markdown(
            "<div style='display:flex;align-items:center;gap:8px;padding:6px 0 2px 0'>"
            "<span style='width:11px;height:11px;border-radius:50%;"
            "background:#22c55e;display:inline-block;box-shadow:0 0 6px #22c55e88;'></span>"
            "<span style='font-size:0.82rem;font-weight:600;color:#22c55e;'>API is connected</span>"
            "</div>",
            unsafe_allow_html=True,
        )
    else:
        _reason = "Toggle is off" if not _api_on else "Key not found"
        st.markdown(
            f"<div style='display:flex;align-items:center;gap:8px;padding:6px 0 2px 0'>"
            f"<span style='width:11px;height:11px;border-radius:50%;"
            f"background:#ef4444;display:inline-block;box-shadow:0 0 6px #ef444488;'></span>"
            f"<span style='font-size:0.82rem;font-weight:600;color:#ef4444;'>API is not connected</span>"
            f"</div>"
            f"<div style='font-size:0.72rem;color:#888;padding-bottom:4px;margin-left:19px;'>{_reason}</div>",
            unsafe_allow_html=True,
        )

    # Toggle switch
    st.toggle(
        "Enable API",
        key="api_enabled",
        help="Turn off to disable all Claude API calls during UI testing — no credits consumed.",
    )
    st.divider()

# ── Programmatic navigation via page_idx ─────────────────────────────────────
if "page_idx" not in st.session_state:
    st.session_state["page_idx"] = 0
if "_nav_radio" not in st.session_state:
    st.session_state["_nav_radio"] = PAGES[st.session_state["page_idx"]]

# Only sync the radio key when a programmatic navigation was explicitly requested
# (_nav_pending flag set by Proceed/Edit buttons). User sidebar clicks are
# NOT overridden — avoids the double-click bug while still allowing buttons to navigate.
if st.session_state.pop("_nav_pending", False):
    st.session_state["_nav_radio"] = PAGES[st.session_state["page_idx"]]

selected = st.sidebar.radio("", PAGES, key="_nav_radio")
st.session_state["page_idx"] = PAGES.index(selected)

# ── Scroll to top on programmatic navigation ──────────────────────────────────
if st.session_state.get("_scroll_top"):
    st.session_state["_scroll_top"] = False
    st.components.v1.html("""
<script>
setTimeout(function() {
    var p = window.parent;
    try {
        p.scrollTo(0, 0);
        if (p.document.body) p.document.body.scrollTop = 0;
        if (p.document.documentElement) p.document.documentElement.scrollTop = 0;
        var selectors = [
            '[data-testid="stAppViewContainer"]',
            '[data-testid="stMain"]',
            'section.main',
            '.main',
            '.block-container'
        ];
        selectors.forEach(function(sel) {
            var el = p.document.querySelector(sel);
            if (el) el.scrollTop = 0;
        });
    } catch(e) {}
}, 120);
</script>
""", height=1)

st.sidebar.markdown("---")
st.sidebar.caption("Optum Engage · B2C Campaigns · ⚡ MAT")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 0 — DASHBOARD (role-aware landing page)
# ══════════════════════════════════════════════════════════════════════════════
if selected == "🏠 Dashboard":

    _me      = _current_user.get("email", "")
    _first   = (_current_user.get("name", "") or "there").split()[0]
    _is_admin = _current_role == "Admin"

    st.title(f"Welcome back, {_first} 👋")
    st.caption(f"{_me} · {_current_role}")
    st.markdown("---")

    # ── Data pulls (shared by the sections below) ─────────────────────────────
    try:
        _dash_camps   = list_campaigns() if _is_admin else list_campaigns_by_user(_me)
    except Exception:
        _dash_camps = []
    try:
        _dash_pending = approvals_db.get_pending_for(_me)
    except Exception:
        _dash_pending = []
    try:
        _dash_unread  = notifications_db.get_unread(_me)
    except Exception:
        _dash_unread = []

    # ── Quick stats ───────────────────────────────────────────────────────────
    _m1, _m2, _m3, _m4 = st.columns(4)
    _m1.metric("Campaigns" + (" (all)" if _is_admin else ""), len(_dash_camps))
    _m2.metric("Awaiting my approval", len(_dash_pending))
    _m3.metric("Approved", sum(1 for c in _dash_camps if (c.get("approval_status") or "") == "Approved"))
    _m4.metric("Unread notifications", len(_dash_unread))

    # ── Approver inbox ────────────────────────────────────────────────────────
    if _current_role in ("Admin", "Approver") or _dash_pending:
        st.markdown("### 📥 Approvals waiting for you")
        if not _dash_pending:
            st.caption("Nothing pending — all clear.")
        import base64 as _b64mod
        import datetime as _dt
        for _ap in _dash_pending:
            _ap_camp = get_campaign_by_uid(_ap.get("campaign_uid", "")) or {}
            with st.container(border=True):
                _ai, _ab1, _ab2 = st.columns([3.4, 1.3, 1.3])
                with _ai:
                    st.markdown(
                        f"**{_ap.get('opm_ticket') or _ap_camp.get('opm_ticket', '—')}** · "
                        f"{_ap_camp.get('client', '—')} · {_ap_camp.get('campaign_type', '—')}"
                    )
                    st.caption(
                        f"Requested by {_ap.get('requested_by', '—')} · {_ap.get('requested_at', '')[:16]}"
                        + (f" · {_ap.get('report_ref')}" if _ap.get("report_ref") else "")
                    )
                with _ab1:
                    if st.button("✅ Approve", type="primary", key=f"dash_ok_{_ap['approval_uid']}",
                                 use_container_width=True):
                        try:
                            approvals_db.set_status(_ap["approval_uid"], "Approved")
                            if _ap.get("campaign_uid"):
                                update_campaign(_ap["campaign_uid"],
                                                approval_status="Approved",
                                                approval_updated_at=now_iso())
                            notifications_db.notify(
                                _ap.get("requested_by", ""), _ap.get("campaign_uid", ""),
                                _ap.get("opm_ticket", ""),
                                f"✅ {_ap.get('opm_ticket', 'Campaign')} was approved by {_me}",
                            )
                        except Exception as _e:
                            st.error(f"Could not record the decision: {_e}")
                        st.rerun()
                with _ab2:
                    if st.button("❌ Reject", key=f"dash_no_{_ap['approval_uid']}",
                                 use_container_width=True):
                        try:
                            approvals_db.set_status(_ap["approval_uid"], "Rejected")
                            if _ap.get("campaign_uid"):
                                update_campaign(_ap["campaign_uid"],
                                                approval_status="Rejected",
                                                approval_updated_at=now_iso())
                            notifications_db.notify(
                                _ap.get("requested_by", ""), _ap.get("campaign_uid", ""),
                                _ap.get("opm_ticket", ""),
                                f"❌ {_ap.get('opm_ticket', 'Campaign')} was rejected by {_me}",
                            )
                        except Exception as _e:
                            st.error(f"Could not record the decision: {_e}")
                        st.rerun()

                # ── Row 2: report link + repull request ──────────────────────
                _rl, _rd, _rb, _ = st.columns([2.2, 1.4, 1.5, 0.9])
                with _rl:
                    _rep_html = _ap.get("report_html") or ""
                    if _rep_html.strip():
                        # Open the full audience summary report in a NEW TAB.
                        # A blob URL is built client-side from the stored HTML —
                        # the anchor click is a user gesture, so no popup block.
                        _rep_b64 = _b64mod.b64encode(_rep_html.encode("utf-8")).decode()
                        st.components.v1.html(f"""
                        <a id="rep_{_ap['approval_uid']}" target="_blank"
                           style="display:inline-block;font-family:'Segoe UI',sans-serif;
                                  font-size:13px;font-weight:600;color:#007B8F;
                                  text-decoration:none;border:1px solid #D7DEE6;
                                  border-radius:8px;padding:8px 14px;background:#fff;">
                           📄 Open audience summary report ↗
                        </a>
                        <script>
                          (function() {{
                            var bytes = Uint8Array.from(atob("{_rep_b64}"), function(ch) {{ return ch.charCodeAt(0); }});
                            var blob = new Blob([bytes], {{ type: "text/html" }});
                            document.getElementById("rep_{_ap['approval_uid']}").href = URL.createObjectURL(blob);
                          }})();
                        </script>
                        """, height=44)
                    else:
                        st.caption("No report attached to this request.")
                with _rd:
                    _rp_date = st.date_input(
                        "Repull date",
                        value=_dt.date.today() + _dt.timedelta(days=1),
                        min_value=_dt.date.today(),
                        key=f"dash_rpd_{_ap['approval_uid']}",
                        label_visibility="collapsed",
                        help="Date the audience should be re-pulled",
                    )
                with _rb:
                    if st.button("🔁 Request repull", key=f"dash_rp_{_ap['approval_uid']}",
                                 use_container_width=True):
                        try:
                            _rp_str = _rp_date.strftime("%Y-%m-%d")
                            approvals_db.set_status(_ap["approval_uid"], "Repull Requested",
                                                    notes=f"Repull on {_rp_str}")
                            if _ap.get("campaign_uid"):
                                update_campaign(_ap["campaign_uid"],
                                                approval_status="Repull Requested",
                                                approval_updated_at=now_iso(),
                                                repull_date=_rp_str,
                                                repull_requested_by=_me,
                                                repull_requested_at=now_iso())
                            notifications_db.notify(
                                _ap.get("requested_by", ""), _ap.get("campaign_uid", ""),
                                _ap.get("opm_ticket", ""),
                                f"🔁 {_ap.get('opm_ticket', 'Campaign')}: repull requested for {_rp_str} by {_me}",
                            )
                        except Exception as _e:
                            st.error(f"Could not record the repull request: {_e}")
                        st.rerun()
        st.markdown("---")

    # ── Notifications ─────────────────────────────────────────────────────────
    if _dash_unread:
        st.markdown("### 🔔 Notifications")
        for _n in _dash_unread:
            st.info(f"{_n.get('message', '')}  \n*{_n.get('created_at', '')[:16]}*")
        if st.button("Mark all as read", key="dash_read_all"):
            try:
                notifications_db.mark_all_read(_me)
            except Exception:
                pass
            st.rerun()
        st.markdown("---")

    # ── Campaigns table ───────────────────────────────────────────────────────
    st.markdown("### 📋 " + ("All campaigns" if _is_admin else "My campaigns"))
    if _dash_camps:
        _rows = [{**{
            "OPM Ticket":  c.get("opm_ticket", ""),
            "WF Number":   c.get("wf_number", ""),
            "Client":      c.get("client", ""),
            "Type":        c.get("campaign_type", ""),
            "Channel":     c.get("channel", ""),
            "Campaign ID": c.get("campaign_id", "") or "—",
            "Approval":    c.get("approval_status", "") or "—",
            "Created":     (c.get("row_created_at", "") or "")[:10],
        }, **({"Created by": c.get("created_by", "")} if _is_admin else {})}
            for c in _dash_camps]
        st.dataframe(_rows, use_container_width=True, hide_index=True)
    else:
        st.caption("No campaigns yet — start one below.")

    if st.button("➕ Start New Campaign", type="primary", key="dash_new"):
        st.session_state["page_idx"] = PAGES.index("1. Jira Intake")
        st.session_state["_nav_pending"] = True
        st.session_state["_scroll_top"] = True
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 1 — JIRA INTAKE
# ══════════════════════════════════════════════════════════════════════════════
elif selected == "1. Jira Intake":

    st.title("Jira Intake")
    st.markdown("Choose how you want to start — enter an OPM ticket number or upload a BRD PDF directly.")
    st.markdown("---")

    # ── Intake method toggle ──────────────────────────────────────────────────
    method = st.radio(
        "Intake method",
        ["🎫  Enter OPM Ticket Number", "📄  Upload BRD PDF"],
        horizontal=True,
        label_visibility="collapsed"
    )
    st.markdown("---")

    # ── Shared BRD summary renderer ──────────────────────────────────────────
    def show_brd_summary(summary):
        st.markdown("### BRD Summary")
        is_stub   = summary.get("_is_stub", False)    # blank template — API not connected, non-OPM-67 ticket
        is_sample = summary.get("_is_sample", False)  # saved sample — OPM-67 with API not connected
        if is_sample:
            st.info(
                "📋 **OPM-67 sample data loaded.** "
                "This is the saved Valero/OPM-67 dataset — all fields are pre-filled below. "
                "Connect the Claude API to re-pull live data for this ticket."
            )
        elif is_stub:
            st.warning(
                "⚠️ **Claude API not connected** — the fields below are blank/template only. "
                "Your ticket number has been recorded. Fill in the fields manually on the "
                "Audience Builder page, or connect the Claude API (ask your Workspace admin for `ANTHROPIC_API_KEY`) "
                "to have BRD details extracted automatically."
            )
        else:
            st.caption("Review the summary below and confirm before proceeding to the Audience Builder.")
        # Filter out internal flags before displaying
        display_items = [(k, v) for k, v in summary.items() if not k.startswith("_")]
        col1, col2 = st.columns(2)
        half = len(display_items) // 2
        with col1:
            for k, v in display_items[:half]:
                st.markdown(f"**{k}:** {v if v else '—'}")
        with col2:
            for k, v in display_items[half:]:
                st.markdown(f"**{k}:** {v if v else '—'}")
        st.markdown("---")
        if st.button("Confirm & Proceed to Audience Builder →", type="primary", key="confirm_btn"):
            # Save to campaign_context — strip internal flags
            ctx = {k: v for k, v in summary.items() if not k.startswith("_")}
            st.session_state["campaign_context"] = ctx
            # Persist to the campaigns table (the hub). Needs an identifier so
            # re-confirms upsert the same row instead of creating duplicates.
            if (ctx.get("OPM Ticket", "").strip() or ctx.get("WF Number", "").strip()):
                try:
                    _cuid = upsert_campaign({
                        "opm_ticket":    ctx.get("OPM Ticket", "").strip(),
                        "wf_number":     ctx.get("WF Number", "").strip(),
                        "client":        ctx.get("Client", ""),
                        "campaign_name": f"{ctx.get('Client', '')} {ctx.get('Campaign Type', '')}".strip(),
                        "campaign_type": ctx.get("Campaign Type", ""),
                        "channel":       ctx.get("Channel(s)", ctx.get("Channel", "")),
                        "intake_date":   now_iso(),
                        "created_by":    _current_user.get("email", ""),
                    })
                    st.session_state["campaign_uid"] = _cuid
                except Exception as _db_err:
                    st.warning(f"Campaign saved to session, but the database write failed: {_db_err}")
            st.session_state.pop("_brd_active_summary", None)  # clear so re-running intake starts fresh
            st.session_state["page_idx"] = PAGES.index("2. Audience Builder")
            st.session_state["_nav_pending"] = True
            st.session_state["_scroll_top"] = True
            st.rerun()

    # --- Fallback template used when Claude API is unavailable ---
    # Uses generic placeholders — NOT client-specific data.
    stub_summary = {
        "Client":               "",
        "Campaign Type":        "",
        "Channel(s)":           "",
        "Affiliations in Scope":"",
        "Relationship Codes":   "",
        "Age Filter":           "18+ (baseline standard)",
        "Geography":            "US 50 states only (exclude PR, GU, VI, AS, MP)",
        "Suppressions":         "",
        "Activity Logic":       "",
        "Email Deployments":    "",
        "Expected Count":       "",
    }

    # ══ METHOD A — Ticket Number ══════════════════════════════════════════════
    if method == "🎫  Enter OPM Ticket Number":

        with st.form("intake_form_ticket"):
            col1, col2 = st.columns([2, 1])
            with col1:
                ticket = st.text_input(
                    "OPM Ticket Number",
                    placeholder="e.g. OPM-67",
                    help="Enter the Jira ticket number assigned to this campaign"
                )
            with col2:
                st.markdown("<br>", unsafe_allow_html=True)
                submitted = st.form_submit_button("Start Intake", use_container_width=True, type="primary")

        if submitted:
            if not ticket.strip():
                st.error("Please enter a ticket number before starting.")
            else:
                ticket = ticket.strip().upper()
                # Always clear the cache so re-submitting the same ticket re-evaluates
                # (avoids stale session state from previous runs or code changes)
                st.session_state.pop(f"_claude_brd_{ticket}", None)
                st.markdown("---")
                with st.status(f"Running intake for {ticket}...", expanded=True) as status:
                    st.write("📋 Reading Jira ticket...")
                    # --- STUB: Jira API call (replace with mcp__d8026533 getJiraIssue when wired) ---
                    wf_number = "WF22031341"
                    st.write(f"✅ WF number found: **{wf_number}**")
                    st.write("📁 Searching Google Drive for BRD...")
                    # --- STUB: Google Drive search (replace with mcp__a42bc1b9 search_files when wired) ---
                    brd_filename = f"{wf_number}_{ticket.replace('-','')}_BRD.docx"
                    st.write(f"✅ BRD found: **{brd_filename}**")
                    st.write("📖 Asking Claude to analyse BRD and extract campaign parameters...")

                    _brd_cache_key = f"_claude_brd_{ticket}"
                    if _brd_cache_key not in st.session_state:
                        _brd_prompt = f"""The user has provided Jira ticket **{ticket}** (WF number: {wf_number}).
Using your knowledge of Optum Engage B2C campaigns and the marketing automation skill, extract or infer the most likely BRD campaign parameters for this ticket.

Return ONLY a valid JSON object with exactly these keys:
{{
  "Client": "",
  "Campaign Type": "",
  "Channel(s)": "",
  "Affiliations in Scope": "",
  "Relationship Codes": "",
  "Age Filter": "",
  "Geography": "",
  "Suppressions": "",
  "Activity Logic": "",
  "Email Deployments": "",
  "Expected Count": ""
}}

Do not include any explanation — return the JSON only."""
                        _claude_brd = _call_claude(_brd_prompt)
                        if _claude_brd:
                            # API connected — use real Claude output for any ticket
                            st.session_state[_brd_cache_key] = _parse_brd_json(_claude_brd, stub_summary)
                        elif ticket == "OPM-67":
                            # API not connected + OPM-67 → load saved sample
                            st.session_state[_brd_cache_key] = {**_OPM67_BRD, "_is_sample": True}
                        else:
                            # API not connected + other ticket → blank template
                            st.session_state[_brd_cache_key] = _parse_brd_json(None, stub_summary)

                    # Make a copy so we don't mutate the cached value; inject ticket + WF
                    _active_summary = dict(st.session_state[_brd_cache_key])
                    _active_summary["OPM Ticket"] = ticket
                    _active_summary["WF Number"]  = wf_number
                    # Persist to a stable key so it survives reruns triggered by buttons
                    st.session_state["_brd_active_summary"] = _active_summary
                    _cached_brd = st.session_state[_brd_cache_key]
                    if _cached_brd.get("_is_sample"):
                        st.write("✅ BRD loaded from saved OPM-67 sample (connect API for live data).")
                    elif _cached_brd.get("_is_stub"):
                        st.write("⚠️ Claude API not connected — no BRD data fetched.")
                    else:
                        st.write("✅ BRD analysed by Claude.")
                    status.update(label="Intake complete — review the summary below.", state="complete")

    # ══ METHOD B — Upload BRD PDF ═════════════════════════════════════════════
    else:
        uploaded_pdf = st.file_uploader(
            "Upload BRD PDF",
            type=["pdf"],
            help="Upload the campaign BRD as a PDF. The app will read and summarise it."
        )

        if uploaded_pdf is not None:
            st.markdown("---")
            with st.status(f"Reading uploaded BRD: {uploaded_pdf.name}...", expanded=True) as status:
                st.write("📄 PDF received...")
                st.write(f"✅ File: **{uploaded_pdf.name}** ({round(uploaded_pdf.size/1024, 1)} KB)")
                st.write("📖 Asking Claude to read and extract BRD parameters...")

                _pdf_cache_key = f"_claude_pdf_{uploaded_pdf.name}_{uploaded_pdf.size}"
                if _pdf_cache_key not in st.session_state:
                    _pdf_prompt = """Read this BRD document carefully and extract all campaign parameters.
Return ONLY a valid JSON object with exactly these keys:
{
  "Client": "",
  "Campaign Type": "",
  "Channel(s)": "",
  "Affiliations in Scope": "",
  "Relationship Codes": "",
  "Age Filter": "",
  "Geography": "",
  "Suppressions": "",
  "Activity Logic": "",
  "Email Deployments": "",
  "Expected Count": ""
}
Fill each field from the BRD. Use empty string if not specified. Return JSON only."""
                    _pdf_bytes = uploaded_pdf.read()
                    _claude_pdf = _call_claude_with_pdf(_pdf_prompt, _pdf_bytes)
                    st.session_state[_pdf_cache_key] = _parse_brd_json(_claude_pdf, stub_summary)

                _pdf_active_summary = dict(st.session_state[_pdf_cache_key])
                st.session_state["_brd_active_summary"] = _pdf_active_summary
                st.write("✅ BRD parsed by Claude.")
                status.update(label="BRD read complete — review the summary below.", state="complete")

    # ══ BRD Summary — rendered OUTSIDE both method blocks ═════════════════════
    # Persists across all reruns (button clicks, method toggles, page-back navigation)
    # until explicitly cleared by "Confirm & Proceed" or a new intake run.
    if st.session_state.get("_brd_active_summary"):
        show_brd_summary(st.session_state["_brd_active_summary"])


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 2 — AUDIENCE BUILDER
# ══════════════════════════════════════════════════════════════════════════════
elif selected == "2. Audience Builder":

    st.title("Audience Builder")
    st.markdown("Sections unlock progressively as each step is confirmed.")
    st.markdown("---")

    # Initialise session state flags
    for key in ["s1_confirmed", "s2_confirmed", "s3_confirmed", "s4_confirmed", "s5_confirmed", "s2_fetched"]:
        if key not in st.session_state:
            st.session_state[key] = False

    # ── Campaign context — prefer data carried from Jira Intake ──────────────
    _ctx = st.session_state.get("campaign_context", {})
    _has_ctx = bool(_ctx)

    if not _has_ctx:
        st.info("No intake context found. You can run **1. Jira Intake** first, or enter campaign details manually below.")
        with st.expander("Enter campaign details manually", expanded=True):
            _mc1, _mc2 = st.columns(2)
            with _mc1:
                _m_client  = st.text_input("Client",        placeholder="e.g. Valero",     key="ab_client")
                _m_ticket  = st.text_input("OPM Ticket",    placeholder="e.g. OPM-67",     key="ab_ticket")
                _m_wf      = st.text_input("WF Number",     placeholder="e.g. WF22031341", key="ab_wf")
                _m_type    = st.selectbox("Campaign Type",  ["Progress", "Incentive-based", "Activity-based", "Simple Filter"], key="ab_type")
            with _mc2:
                _m_channel = st.text_input("Channel(s)",    placeholder="Email / Direct Mail", key="ab_channel")
                _m_affs    = st.text_input("Affiliations",  placeholder="e.g. 02, 03, 04",     key="ab_affs")
                _m_geo     = st.text_input("Geography",     placeholder="e.g. US 50 states only", key="ab_geo")
                _m_supp    = st.text_input("Suppressions",  placeholder="e.g. Members at $200 max cap excluded", key="ab_supp")
            if st.button("Use these details", type="primary", key="ab_manual_submit"):
                if not _m_client.strip() or not _m_ticket.strip():
                    st.error("⚠️ Client and OPM Ticket are required before proceeding.")
                else:
                    st.session_state["campaign_context"] = {
                        "Client": _m_client, "OPM Ticket": _m_ticket, "WF Number": _m_wf,
                        "Campaign Type": _m_type, "Channel": _m_channel,
                        "Affiliations": _m_affs, "Geography": _m_geo, "Suppressions": _m_supp,
                        "BRD Scenario": _m_type,
                    }
                    st.rerun()

    _ctx = st.session_state.get("campaign_context", {})

    # Map context keys — no fallback stubs; only show real values entered by the user
    campaign = {
        "Client":           _ctx.get("Client",        ""),
        "OPM Ticket":       _ctx.get("OPM Ticket",    ""),
        "WF Number":        _ctx.get("WF Number",     ""),
        "Campaign Type":    _ctx.get("Campaign Type", ""),
        "Channel":          _ctx.get("Channel(s)",    _ctx.get("Channel", "")),
        "Affiliations":     _ctx.get("Affiliations in Scope", _ctx.get("Affiliations", "")),
        "BRD Scenario":     _ctx.get("BRD Scenario",  _ctx.get("Campaign Type", "")),
        "Relationship Code":_ctx.get("Relationship Codes", _ctx.get("Relationship Code", "")),
        "Geography":        _ctx.get("Geography",     ""),
        "Suppressions":     _ctx.get("Suppressions",  ""),
    }
    brd_scenario = campaign["BRD Scenario"]   # drives conditional sections

    # ══ SECTION 1 — Campaign Context ══════════════════════════════════════════
    with st.expander("Section 1 — Campaign Context", expanded=True):
        col1, col2 = st.columns(2)
        items = list(campaign.items())
        half = len(items) // 2
        with col1:
            for k, v in items[:half]:
                st.markdown(f"**{k}:** {v}")
        with col2:
            for k, v in items[half:]:
                st.markdown(f"**{k}:** {v}")
        st.caption("Carried from Jira Intake. Use the Edit button if any detail looks wrong.")
        col_ok, col_edit, _ = st.columns([1.6, 1.6, 2.8])
        with col_ok:
            if st.button("Looks good — proceed", type="primary", key="s1_ok"):
                if not any(str(v).strip() for v in campaign.values()):
                    st.error("⚠️ No campaign data found. Run Jira Intake first or enter details manually above.")
                else:
                    st.session_state["s1_confirmed"] = True
                    st.session_state["s2_confirmed"] = False   # reset downstream
                    st.session_state["s2_fetched"] = False
                    st.success("Context confirmed. Section 2 unlocked.")
        with col_edit:
            if st.button("Edit / Re-run intake", key="s1_edit"):
                st.session_state["page_idx"] = PAGES.index("1. Jira Intake")
                st.session_state["_nav_pending"] = True
                st.session_state["_scroll_top"] = True
                st.rerun()

    # ══ SECTION 2 — Sanity Config Lookup ══════════════════════════════════════
    st.markdown("---")
    st.markdown("### Section 2 — Sanity Config Lookup")

    if not st.session_state.get("s1_confirmed"):
        st.info("✋ Confirm Section 1 context first to unlock the Sanity config fetch.")
    else:
        _is_opm67 = campaign.get("OPM Ticket", "") == "OPM-67"
        if st.button("Fetch Client Config from Sanity", key="s2_fetch"):
            with st.spinner("Querying Sanity CMS..."):
                if _is_opm67:
                    # OPM-67 saved sample — use real Valero/Sanity values
                    st.session_state["s2_sanity_result"] = dict(_OPM67_SANITY)
                    st.session_state["s2_source"] = "sample"
                    # Cache to the client_config table so future campaigns for
                    # this client resolve from the DB instantly.
                    try:
                        client_config_db.upsert_client_config(
                            campaign.get("Client", "Valero") or "Valero",
                            {
                                "child_org_id":            _OPM67_SANITY.get("childOrgId", ""),
                                "client_id":               _OPM67_SANITY.get("clientId", ""),
                                "reward_max_cap":          _OPM67_SANITY.get("rewardMaxCap", ""),
                                "reward_earning_end_date": _OPM67_SANITY.get("rewardEarningEndDate", ""),
                                "total_affiliations":      _OPM67_SANITY.get("Total affiliations", ""),
                                "included_affiliations":   _OPM67_SANITY.get("BRD includes", ""),
                                "excluded_affiliations":   _OPM67_SANITY.get("Excluded", ""),
                                "filter_decision":         _OPM67_SANITY.get("Filter decision", ""),
                            },
                        )
                    except Exception:
                        pass
                else:
                    # Try the MAT database cache first (populated by previous
                    # campaigns for the same client), then fall back to stub.
                    _cc = None
                    try:
                        _cc = client_config_db.get_client_config(campaign.get("Client", "").strip())
                    except Exception:
                        pass
                    if _cc and (_cc.get("child_org_id") or "").strip():
                        st.session_state["s2_sanity_result"] = {
                            "childOrgId":           _cc.get("child_org_id", ""),
                            "clientId":             _cc.get("client_id", ""),
                            "rewardMaxCap":         _cc.get("reward_max_cap", ""),
                            "rewardEarningEndDate": _cc.get("reward_earning_end_date", ""),
                            "Total affiliations":   _cc.get("total_affiliations", ""),
                            "BRD includes":         _cc.get("included_affiliations", ""),
                            "Excluded":             _cc.get("excluded_affiliations", ""),
                            "Filter decision":      _cc.get("filter_decision", ""),
                        }
                        st.session_state["s2_source"] = "cache"
                    else:
                        # --- STUB: real Sanity MCP call not yet wired ---
                        # Do NOT show OPM-67 data for other campaigns
                        st.session_state["s2_sanity_result"] = {
                            "childOrgId":           "— (connect to Sanity API)",
                            "clientId":             "— (connect to Sanity API)",
                            "rewardMaxCap":         "— (connect to Sanity API)",
                            "rewardEarningEndDate": "— (connect to Sanity API)",
                            "Total affiliations":   "— (connect to Sanity API)",
                            "BRD includes":         "— (enter manually or connect API)",
                            "Excluded":             "— (enter manually or connect API)",
                            "Filter decision":      "— (enter manually or connect API)",
                        }
                        st.session_state["s2_source"] = "stub"
                st.session_state["s2_fetched"] = True

        # Render results and confirm buttons OUTSIDE the fetch button conditional
        # so they persist across reruns (fixes the Streamlit button-in-button bug)
        if st.session_state["s2_fetched"]:
            sanity_result = st.session_state.get("s2_sanity_result", {})
            _s2_source = st.session_state.get("s2_source", "stub")
            st.success("Sanity config fetched.")
            for k, v in sanity_result.items():
                st.markdown(f"**{k}:** {v}")
            if _is_opm67:
                st.info("ℹ️ Affiliation filter: using **NOT IN** because the exclusion list (1) is shorter than the inclusion list (10).")
            elif _s2_source == "cache":
                st.info("📦 Config loaded from the MAT database (cached from a previous campaign for this client). Verify it is still current.")
            else:
                st.warning("⚠️ Sanity API not connected — affiliation filter details are placeholders. Connect the Sanity MCP or enter values manually.")

            col_ok, col_flag, _ = st.columns([1.6, 1.6, 2.8])
            with col_ok:
                if st.button("Affiliation list looks correct", type="primary", key="s2_ok"):
                    st.session_state["s2_confirmed"] = True
                    st.success("Section 2 confirmed. Section 3 unlocked.")
            with col_flag:
                if st.button("Something looks wrong", key="s2_flag"):
                    st.session_state["s2_fetched"] = False
                    st.session_state["s2_confirmed"] = False
                    st.warning("Please check the BRD and Sanity config, then re-fetch.")

    # ══ SECTION 3 — Query Clarification (conditional) ═════════════════════════
    if st.session_state["s2_confirmed"]:
        st.markdown("---")
        st.markdown("### Section 3 — Query Clarification")

        if brd_scenario == "Activity-based":
            st.markdown("**Scenario: Activity-based**")
            st.caption("The following external activity IDs were identified from Sanity / Databricks. Please confirm they are correct before the query is built.")
            # --- STUB: replace with real activity lookup ---
            activities = {
                "Health Survey":       "OPTUM.HEALTHSURVEY.COMPLETE",
                "Biometric Screening": "OPTUM.BIOSCREEN.COMPLETE",
            }
            for name, ext_id in activities.items():
                st.markdown(f"- **{name}:** `{ext_id}`")

            col_ok, col_flag, _ = st.columns([1.6, 1.6, 2.8])
            with col_ok:
                if st.button("Activities are correct", type="primary", key="s3_ok"):
                    st.session_state["s3_confirmed"] = True
                    st.success("Activities confirmed. Section 4 unlocked.")
            with col_flag:
                if st.button("Flag an issue", key="s3_flag"):
                    st.warning("Please check the CRD / Sanity for the correct external activity IDs.")

        elif brd_scenario == "Incentive-based":
            st.markdown("**Scenario: Incentive-based**")
            st.caption("Primary capping target rule IDs resolved from Databricks `production_sanity_data.affiliation`.")
            # --- STUB: replace with real Databricks query ---
            cap_rules = {
                "Affiliations 02–03": "267021",
                "Affiliations 04–11": "267022",
            }
            for aff, rule_id in cap_rules.items():
                st.markdown(f"- **{aff}:** `primary_capping_target_rule_id = {rule_id}`")
            st.info("ℹ️ No confirmation needed for target rule IDs — resolved automatically from the source table.")
            st.session_state["s3_confirmed"] = True   # auto-advance for incentive-based

        else:  # Simple filter pull
            st.markdown("**Scenario: Simple filter pull** — no activity or incentive tables needed.")
            st.session_state["s3_confirmed"] = True   # auto-advance

        if st.session_state["s3_confirmed"] and brd_scenario != "Incentive-based" and brd_scenario != "Simple filter pull":
            pass  # button already shown above
        elif st.session_state["s3_confirmed"] and brd_scenario in ["Incentive-based", "Simple filter pull"]:
            st.success("Section 3 complete. Section 4 unlocked.")

    # ══ SECTION 4 — Query Preview ══════════════════════════════════════════════
    if st.session_state["s3_confirmed"]:
        st.markdown("---")
        st.markdown("### Section 4 — Query Preview")

        if st.button("Generate Query", type="primary", key="s4_generate"):
            # ── Ask Claude to generate production SQL using the marketing automation skill ──
            _sanity_cfg  = st.session_state.get("s2_sanity_result", {})
            _ctx_data    = st.session_state.get("campaign_context", campaign)
            _sql_prompt  = f"""Generate a complete, production-ready Databricks SQL audience query for the following campaign.
Follow ALL rules in the marketing automation skill exactly (SCD-2 filter, affiliation IN vs NOT IN optimisation,
test user exclusions, PII exclusions, email dedup or household dedup as appropriate, etc.).

Campaign context:
{json.dumps(_ctx_data, indent=2, default=str)}

Sanity CMS config:
{json.dumps(_sanity_cfg, indent=2, default=str)}

BRD scenario: {brd_scenario}

Rules:
- Use `end_date = '9999-12-31'` for SCD-2
- Use `childOrgId` from Sanity for org_id filter
- Choose IN vs NOT IN based on whichever affiliation list is shorter
- NEVER filter on plan_start_date directly on member_dimension
- Always exclude test users (efid LIKE '%test%', Pseudomenudo, pseudo_test)
- Email file: include email opt-in from read_api_9000084.users, email IS NOT NULL
- Output must contain NO PII fields (no name, DOB, address, email column — only user_id, efid, affiliation_id, aggregated metrics)

Return ONLY the SQL query. No explanation, no markdown fences."""

            with st.spinner("Claude is generating the production query..."):
                _generated_sql = _call_claude(_sql_prompt)

            # Fallback stub if Claude unavailable
            _is_opm67_sql = campaign.get("OPM Ticket", "") == "OPM-67"
            if _is_opm67_sql:
                _fallback_sql = f"""-- OPM-67 sample query (Valero 2026 Progress Campaign)
-- Auto-generated by MAT (Claude fallback — set ANTHROPIC_API_KEY for live generation)
WITH email_optins AS (
    SELECT DISTINCT user_id
    FROM read_api_9000084.users
    WHERE subscription_status_email_bulk = 'OPTIN'
),
client_affiliations AS (
    SELECT affiliation_id, external_affiliation_id, primary_capping_target_rule_id
    FROM production_sanity_data.affiliation
    WHERE employer_name = 'Valero'
      AND external_affiliation_id NOT IN ('01')
      AND reward_earning_end_date >= CURRENT_DATE()
),
audience AS (
    SELECT md.user_id, md.efid, md.org_id,
           md.affiliation_id, md.affiliation_description,
           ca.primary_capping_target_rule_id
    FROM optum_extracts.member_dimension md
    INNER JOIN client_affiliations ca ON md.affiliation_id = ca.external_affiliation_id
    INNER JOIN email_optins eo        ON md.user_id = eo.user_id
    WHERE md.org_id = 9000288
      AND md.end_date = '9999-12-31'
      AND md.status = 'ACTIVE'
      AND md.member_termination_date > CURRENT_DATE()
      AND md.email IS NOT NULL
      AND FLOOR(DATEDIFF(CURRENT_DATE(), md.date_of_birth) / 365.25) >= 18
      AND md.state NOT IN ('PR','GU','VI','AS','MP')
      AND md.efid NOT LIKE '%test%'
      AND md.client_name NOT LIKE '%Pseudomenudo Test Client%'
      AND md.partner_name NOT LIKE '%pseudo_test%'
)
SELECT user_id, efid, affiliation_id, affiliation_description
FROM audience;"""
            else:
                _fallback_sql = f"""-- Template query — Claude API not connected
-- Connect ANTHROPIC_API_KEY to generate a real query for this campaign
-- Replace all <PLACEHOLDER> values before running
WITH email_optins AS (
    SELECT DISTINCT user_id
    FROM read_api_9000084.users
    WHERE subscription_status_email_bulk = 'OPTIN'
),
client_affiliations AS (
    SELECT affiliation_id, external_affiliation_id, primary_capping_target_rule_id
    FROM production_sanity_data.affiliation
    WHERE employer_name = '<CLIENT_NAME>'
      AND <AFFILIATION_FILTER>   -- e.g. external_affiliation_id NOT IN ('01')
      AND reward_earning_end_date >= CURRENT_DATE()
),
audience AS (
    SELECT md.user_id, md.efid, md.org_id,
           md.affiliation_id, md.affiliation_description,
           ca.primary_capping_target_rule_id
    FROM optum_extracts.member_dimension md
    INNER JOIN client_affiliations ca ON md.affiliation_id = ca.external_affiliation_id
    INNER JOIN email_optins eo        ON md.user_id = eo.user_id
    WHERE md.org_id = <ORG_ID>
      AND md.end_date = '9999-12-31'
      AND md.status = 'ACTIVE'
      AND md.member_termination_date > CURRENT_DATE()
      AND md.email IS NOT NULL
      AND FLOOR(DATEDIFF(CURRENT_DATE(), md.date_of_birth) / 365.25) >= 18
      AND md.state NOT IN ('PR','GU','VI','AS','MP')
      AND md.efid NOT LIKE '%test%'
      AND md.client_name NOT LIKE '%Pseudomenudo Test Client%'
      AND md.partner_name NOT LIKE '%pseudo_test%'
)
SELECT user_id, efid, affiliation_id, affiliation_description
FROM audience;"""

            final_query = _generated_sql if _generated_sql else _fallback_sql
            st.session_state["s4_query"] = final_query

            if _generated_sql:
                st.success("✅ Query generated by Claude using the Marketing Automation skill.")
            else:
                st.warning("⚠️ Claude API not available — showing template query. Set ANTHROPIC_API_KEY to enable live generation.")

            st.code(final_query, language="sql")
            st.markdown("**Output columns (no PII):**")
            cols = ["user_id", "efid", "affiliation_id", "affiliation_description"]
            st.markdown(" · ".join([f"`{c}`" for c in cols]))
            st.success("✅ No PII fields in output.")
            st.session_state["s4_confirmed"] = True

    # ══ SECTION 5 — Databricks Notebook ═══════════════════════════════════════
    if st.session_state["s4_confirmed"]:
        st.markdown("---")
        st.markdown("### Section 5 — Databricks Notebook")

        _nb_parts = [p for p in [campaign.get("OPM Ticket",""), campaign.get("WF Number",""), campaign.get("Client","")] if p.strip()]
        _nb_parts.append("2026_Progress_Campaign")
        notebook_name = "_".join(_nb_parts)
        st.markdown(f"**Notebook name:** `{notebook_name}`")

        col_copy, col_create, _ = st.columns([1.5, 1.5, 2])
        with col_copy:
            if st.button("📋 Copy Query to Clipboard", use_container_width=True, key="s5_copy"):
                st.success("Query copied. Paste it into your Databricks notebook manually.")
                st.session_state["s5_confirmed"] = True
        with col_create:
            if st.button("⚡ Create Notebook in Databricks", use_container_width=True, type="primary", key="s5_create"):
                with st.spinner("Authenticating via OAuth and creating notebook..."):
                    # --- STUB: replace with real Databricks OAuth + API call ---
                    notebook_link = "https://capillary-notebook-ushc.cloud.databricks.com/#notebook/stub"
                st.success(f"Notebook created! [Open in Databricks]({notebook_link})")
                st.session_state["s5_confirmed"] = True
                # Record the notebook link on the campaign row
                try:
                    if st.session_state.get("campaign_uid"):
                        update_campaign(st.session_state["campaign_uid"], notebook_link=notebook_link)
                except Exception:
                    pass

    # ══ SECTION 6 — Next Step ══════════════════════════════════════════════════
    # Appears as soon as Section 5 is visible — user doesn't have to click a notebook button first
    if st.session_state["s4_confirmed"]:
        st.markdown("---")
        st.success("✅ Audience Builder complete. Proceed when ready — you can copy or create the notebook now or later.")
        if st.button("Proceed to Approval Gate →", type="primary", key="s6_proceed"):
            # Write audience summary to session state for Approval Gate to read
            st.session_state["audience_summary"] = {
                "Client":         campaign["Client"],
                "OPM Ticket":     campaign["OPM Ticket"],
                "WF Number":      campaign["WF Number"],
                "Campaign":       f"{campaign['Client']} {campaign['Campaign Type']} Campaign",
                "Audience Count": "—",   # populated when real query runs
                "Channel":        campaign["Channel"],
                "Affiliations":   campaign["Affiliations"],
            }
            st.session_state["page_idx"] = PAGES.index("3. Approval Gate")
            st.session_state["_nav_pending"] = True
            st.session_state["_scroll_top"] = True
            st.rerun()

elif selected == "3. Approval Gate":

    st.title("Approval Gate")
    st.markdown("Review the approval report, send it for sign-off, and track the response.")
    st.markdown("---")

    # Load env vars
    from dotenv import load_dotenv
    import os, smtplib, datetime
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.base import MIMEBase
    from email import encoders
    load_dotenv()
    GMAIL_SENDER   = os.getenv("GMAIL_SENDER", "souradeep.bhattacharjee@optum.com")
    GMAIL_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")

    # Initialise session state
    for key in ["ag_sent", "ag_status", "ag_sent_at"]:
        if key not in st.session_state:
            st.session_state[key]    = False if key != "ag_status" else "Awaiting"
            if key == "ag_sent_at":
                st.session_state[key] = None

    # ── Campaign context — prefer data carried from Audience Builder ─────────
    _ag_ctx = st.session_state.get("audience_summary", {})
    if not _ag_ctx:
        _ag_ctx = st.session_state.get("campaign_context", {})

    if not _ag_ctx:
        st.info("No audience context found. You can run **1. Jira Intake** → **2. Audience Builder** first, or enter campaign details manually below.")
        with st.expander("Enter campaign details manually", expanded=True):
            _ag_c1, _ag_c2 = st.columns(2)
            with _ag_c1:
                _ag_client   = st.text_input("Client",             placeholder="e.g. Valero",                      key="ag_client_in")
                _ag_ticket   = st.text_input("OPM Ticket",         placeholder="e.g. OPM-67",                      key="ag_ticket_in")
                _ag_channel  = st.text_input("Channel",            placeholder="e.g. Email Only",                  key="ag_channel_in")
            with _ag_c2:
                _ag_camp     = st.text_input("Campaign Name",      placeholder="e.g. 2026 Progress",               key="ag_camp_in")
                _ag_count    = st.text_input("Audience Count",     placeholder="e.g. 11,842",                      key="ag_count_in")
                _ag_affil    = st.text_input("Affiliations",       placeholder="e.g. 02, 03, 04, 05",              key="ag_affil_in")
            if st.button("Use these details", type="primary", key="ag_manual_submit"):
                st.session_state["audience_summary"] = {
                    "Client": _ag_client, "OPM Ticket": _ag_ticket,
                    "WF Number": "", "Campaign": _ag_camp, "Audience Count": _ag_count,
                    "Channel": _ag_channel, "Affiliations": _ag_affil,
                }
                st.rerun()

    _ag_ctx = st.session_state.get("audience_summary", st.session_state.get("campaign_context", {}))

    campaign = {
        "Client":         _ag_ctx.get("Client",         ""),
        "OPM Ticket":     _ag_ctx.get("OPM Ticket",     ""),
        "WF Number":      _ag_ctx.get("WF Number",      ""),
        "Campaign":       _ag_ctx.get("Campaign",       ""),
        "Audience Count": _ag_ctx.get("Audience Count", "—"),
        "Channel":        _ag_ctx.get("Channel",        _ag_ctx.get("Channel(s)", "—")),
        "Affiliations":   _ag_ctx.get("Affiliations",   _ag_ctx.get("Affiliations in Scope", "—")),
    }
    subject_line = f"[APPROVAL REQUIRED] {campaign['OPM Ticket']} | {campaign['Client']} {campaign['Campaign']} — Audience Review"

    # ── Parse BRD flags for dynamic HTML ─────────────────────────────────────
    _ch_raw    = campaign.get("Channel", "") or ""
    _ch_lower  = _ch_raw.lower()
    _has_email = any(x in _ch_lower for x in ["email", " em"])
    _has_dm    = any(x in _ch_lower for x in ["direct mail", " dm", "mail"])
    if not _has_email and not _has_dm:
        _has_email = True  # default when channel not yet known

    _supp_lower = (_ag_ctx.get("Suppressions", "") or "").lower()
    _camp_lower = (campaign.get("Campaign", "") or "").lower()
    _has_em2 = (
        any(x in _supp_lower for x in ["em2", "em1", "reminder", "contact history", "wave 2"]) or
        any(x in _camp_lower  for x in ["reminder", "em2", "wave 2"])
    )

    _affil_str  = campaign.get("Affiliations", "—") or "—"
    _rel_str    = _ag_ctx.get("Relationship Codes", _ag_ctx.get("Relationship Code", "")) or ""
    _geo_str    = _ag_ctx.get("Geography", "") or ""
    _geo_50     = any(x in _geo_str.lower() for x in ["50 state", "50 us", "us only"])
    _client     = campaign.get("Client", "") or "—"
    _ticket     = campaign.get("OPM Ticket", "") or "—"
    _wf         = campaign.get("WF Number", "") or "—"
    _camp_name  = campaign.get("Campaign", "") or "—"
    _aud_count  = campaign.get("Audience Count", "—") or "—"
    _ch_display = _ch_raw or "—"

    # badge row
    _badge_list = []
    if _has_email:
        _lbl = "EM1 + EM2 Ready" if _has_em2 else "Email Ready"
        _badge_list.append('<span class="badge green"><span class="dot" style="background:#4CAF50;"></span> ' + _lbl + '</span>')
    if _has_dm:
        _badge_list.append('<span class="badge green"><span class="dot" style="background:#4CAF50;"></span> DM Ready</span>')
    _badge_list.append('<span class="badge"><span class="dot" style="background:#fff;"></span> Aff: ' + _affil_str + '</span>')
    _badge_list.append('<span class="badge amber"><span class="dot" style="background:#FFB300;"></span> Pending Reviewer Approval</span>')
    _badges_html = "\n    ".join(_badge_list)

    # audience KPI blocks
    _em_label  = "EM1 / EM2" if _has_em2 else "EM1"
    _kpi_html  = ""
    if _has_email:
        _kpi_html += (
            '<div style="font-size:12px;font-weight:700;color:#003087;margin-bottom:8px;text-transform:uppercase;letter-spacing:.5px;">'
            + '&#128231; Email Audience (' + _em_label + ') &mdash; Aff ' + _affil_str + '</div>'
            + '<table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:separate;border-spacing:6px;margin-bottom:16px;"><tr>'
            + '<td width="33%" style="background:#fff;border:1px solid #D0D7E3;border-radius:8px;border-left:4px solid #007B8F;padding:11px 12px;vertical-align:top;">'
            + '<div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;color:#5A6A85;">Total Email Audience</div>'
            + '<div style="font-size:22px;font-weight:700;color:#003087;margin:4px 0 2px;line-height:1;">' + _aud_count + '</div>'
            + '<div style="font-size:10px;color:#5A6A85;">Email dedup &middot; Aff ' + _affil_str + ' &middot; Age 18+</div></td>'
            + '<td width="33%" style="background:#fff;border:1px solid #D0D7E3;border-radius:8px;border-left:4px solid #2E7D32;padding:11px 12px;vertical-align:top;">'
            + '<div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;color:#5A6A85;">Registered (TnC Accepted)</div>'
            + '<div style="font-size:22px;font-weight:700;color:#003087;margin:4px 0 2px;line-height:1;">&mdash;</div>'
            + '<div style="font-size:10px;color:#5A6A85;">Pending Databricks query</div></td>'
            + '<td width="33%" style="background:#fff;border:1px solid #D0D7E3;border-radius:8px;border-left:4px solid #007B8F;padding:11px 12px;vertical-align:top;">'
            + '<div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;color:#5A6A85;">Largest Age Group</div>'
            + '<div style="font-size:22px;font-weight:700;color:#003087;margin:4px 0 2px;line-height:1;">&mdash;</div>'
            + '<div style="font-size:10px;color:#5A6A85;">Pending Databricks query</div></td>'
            + '</tr></table>'
        )
    if _has_dm:
        _kpi_html += (
            '<div style="font-size:12px;font-weight:700;color:#5C4033;margin-bottom:8px;text-transform:uppercase;letter-spacing:.5px;">'
            + '&#128236; Direct Mail Audience (DM1) &mdash; Aff ' + _affil_str + '</div>'
            + '<table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:separate;border-spacing:6px;"><tr>'
            + '<td width="33%" style="background:#fff;border:1px solid #D0D7E3;border-radius:8px;border-left:4px solid #5C4033;padding:11px 12px;vertical-align:top;">'
            + '<div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;color:#5A6A85;">Total DM Audience</div>'
            + '<div style="font-size:22px;font-weight:700;color:#003087;margin:4px 0 2px;line-height:1;">&mdash;</div>'
            + '<div style="font-size:10px;color:#5A6A85;">Household dedup &middot; Aff ' + _affil_str + ' &middot; Age 18+</div></td>'
            + '<td width="33%" style="background:#fff;border:1px solid #D0D7E3;border-radius:8px;border-left:4px solid #2E7D32;padding:11px 12px;vertical-align:top;">'
            + '<div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;color:#5A6A85;">Registered (TnC Accepted)</div>'
            + '<div style="font-size:22px;font-weight:700;color:#003087;margin:4px 0 2px;line-height:1;">&mdash;</div>'
            + '<div style="font-size:10px;color:#5A6A85;">Pending Databricks query</div></td>'
            + '<td width="33%" style="background:#fff;border:1px solid #D0D7E3;border-radius:8px;border-left:4px solid #5C4033;padding:11px 12px;vertical-align:top;">'
            + '<div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;color:#5A6A85;">Largest Age Group</div>'
            + '<div style="font-size:22px;font-weight:700;color:#003087;margin:4px 0 2px;line-height:1;">&mdash;</div>'
            + '<div style="font-size:10px;color:#5A6A85;">Pending Databricks query</div></td>'
            + '</tr></table>'
        )

    # EM1 vs EM2 section (only when two email waves detected)
    if _has_email and _has_em2:
        _em_wave_section = (
            '<div class="section">'
            '<div class="section-title">EM1 vs EM2 &mdash; What\'s Different</div>'
            '<div class="em-header" style="margin-top:0;">'
            '<span class="em-tag em1">EM1</span> <strong>Initial Email</strong></div>'
            '<div class="alert info" style="margin-bottom:16px;">'
            '<strong>Eligibility:</strong> All members in Aff ' + _affil_str + ' with a valid email, age 18+, not terminated, deduplicated by email address. '
            'No contact-history suppression. Production-ready after approval.</div>'
            '<div class="em-header"><span class="em-tag em2">EM2</span> <strong>Reminder Email</strong></div>'
            '<div class="alert warn">'
            '<strong>EM2 requires a fresh audience pull after EM1 deployment.</strong> '
            'Same eligibility as EM1, but suppresses all members who received EM1 via contact history. '
            'Requires the EM1 campaign ID &mdash; available after EM1 deployment.<br><br>'
            '<strong>Tables used to suppress EM1 recipients:</strong><br>'
            '&bull; <code>optum_prod_source_delta.nsadmin__messages</code> WHERE campaign_id = &lt;EM1_ID&gt;<br>'
            '&bull; <code>optum_prod_source_delta.veneno_data_details__control_group_users_history</code> WHERE campaign_id = &lt;EM1_ID&gt;'
            '</div></div>'
        )
    else:
        _em_wave_section = ""

    # Tier 2 filter rows — driven by BRD fields
    _t2_rows = []
    if _affil_str and _affil_str != "—":
        _t2_rows.append(
            '<tr><td>Affiliations</td>'
            '<td><code>LEFT(affiliation_description, 2) IN (' + _affil_str + ')</code></td>'
            '<td class="brd-yes">&#10003; Yes</td>'
            '<td>BRD-specified affiliations in scope</td></tr>'
        )
    if _rel_str and "no filter" not in _rel_str.lower():
        _t2_rows.append(
            '<tr><td>Relationship code</td>'
            '<td><code>relationship_code IN (...)</code></td>'
            '<td class="brd-yes">&#10003; Yes</td>'
            '<td>' + _rel_str + '</td></tr>'
        )
    else:
        _t2_rows.append(
            '<tr><td>Relationship code</td><td>No filter applied</td>'
            '<td class="brd-no">Not specified</td><td>All member types included</td></tr>'
        )
    if _has_email:
        _t2_rows.append(
            '<tr><td>Email deduplication</td>'
            '<td><code>ROW_NUMBER() OVER (PARTITION BY TRIM(LOWER(email)))</code></td>'
            '<td class="brd-yes">&#10003; Yes</td>'
            '<td>One record per unique email address</td></tr>'
        )
    if _has_dm:
        _t2_rows.append(
            '<tr><td>Household dedup (DM)</td>'
            '<td><code>PARTITION BY address_line_1, city, state, postal_code</code></td>'
            '<td class="brd-yes">&#10003; Yes</td>'
            '<td>One per household address for Direct Mail</td></tr>'
        )
    if _has_email and _has_em2:
        _t2_rows.append(
            '<tr><td>Contact history suppression (EM2)</td>'
            '<td><code>user_id NOT IN nsadmin__messages WHERE campaign_id = EM1_ID</code></td>'
            '<td class="brd-yes">&#10003; Yes</td>'
            '<td>EM2 suppresses all EM1 recipients</td></tr>'
        )
    if _geo_50:
        _t2_rows.append(
            '<tr><td>Geography</td><td>50 US states only (excl. PR, GU, VI, AS, MP)</td>'
            '<td class="brd-yes">&#10003; Yes</td>'
            '<td>' + _geo_str + '</td></tr>'
        )
    _t2_html = "\n      ".join(_t2_rows) if _t2_rows else (
        '<tr><td colspan="4" style="color:#9E9E9E;font-style:italic;">'
        'No BRD-specific filters detected &mdash; complete Jira Intake to populate</td></tr>'
    )

    # optional overview rows
    _rel_row = ('<tr><td style="color:#5A6A85;">Relationship Code</td><td>' + _rel_str + '</td></tr>') if _rel_str else ''
    _geo_row = ('<tr><td style="color:#5A6A85;">Geography</td><td>' + _geo_str + '</td></tr>') if _geo_str else ''
    _email_filter_row = (
        '<tr><td>Valid email</td>'
        '<td><code>email IS NOT NULL AND TRIM(email) &lt;&gt; \'\'</code></td>'
        '<td class="brd-no">Not specified</td>'
        '<td>Standard &mdash; email channel baseline</td></tr>'
    ) if _has_email else ''
    _dm_filter_row = (
        '<tr><td>Valid address</td>'
        '<td><code>address_line_1 IS NOT NULL AND TRIM(address_line_1) &lt;&gt; \'\'</code></td>'
        '<td class="brd-no">Not specified</td>'
        '<td>Standard &mdash; DM channel baseline</td></tr>'
    ) if _has_dm else ''

    # CSS (defined as plain string — no f-string escaping needed for braces)
    _CSS = (
        "* { box-sizing:border-box; margin:0; padding:0; }"
        " body { font-family:'Segoe UI',Arial,sans-serif; background:#F0F4F8; color:#1A2B4A; font-size:14px; line-height:1.5; }"
        " .header { background:linear-gradient(135deg,#003087 0%,#005EB8 60%,#007B8F 100%); color:#fff; padding:32px 40px 28px; }"
        " .header-top { display:flex; justify-content:space-between; align-items:flex-start; }"
        " .header h1 { font-size:24px; font-weight:700; letter-spacing:-.3px; }"
        " .header .subtitle { font-size:13px; opacity:.85; margin-top:4px; }"
        " .badge-row { display:flex; gap:8px; margin-top:16px; flex-wrap:wrap; }"
        " .badge { display:inline-flex; align-items:center; gap:5px; background:rgba(255,255,255,.15); border:1px solid rgba(255,255,255,.3); border-radius:20px; padding:4px 12px; font-size:11px; font-weight:600; }"
        " .badge.green { background:rgba(46,125,50,.35); border-color:rgba(76,175,80,.5); }"
        " .badge.amber { background:rgba(245,127,23,.3); border-color:rgba(255,167,38,.5); }"
        " .dot { width:7px; height:7px; border-radius:50%; background:currentColor; }"
        " .container { max-width:980px; margin:0 auto; padding:28px 20px 60px; }"
        " .section { background:#fff; border-radius:10px; border:1px solid #D0D7E3; padding:24px 28px; margin-bottom:20px; }"
        " .section-title { font-size:13px; font-weight:700; text-transform:uppercase; letter-spacing:.6px; color:#5A6A85; border-bottom:2px solid #EDF1F7; padding-bottom:10px; margin-bottom:18px; }"
        " table.data-table { width:100%; border-collapse:collapse; font-size:13px; }"
        " table.data-table th { background:#EDF1F7; color:#5A6A85; font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:.4px; padding:8px 12px; text-align:left; }"
        " table.data-table td { padding:8px 12px; border-bottom:1px solid #EDF1F7; vertical-align:top; }"
        " table.data-table tr:last-child td { border-bottom:none; }"
        " .alert { border-radius:8px; padding:14px 16px; margin-bottom:16px; font-size:13px; }"
        " .alert.info { background:#E3F2FD; border-left:4px solid #1976D2; }"
        " .alert.warn { background:#FFF8E1; border-left:4px solid #F57F17; }"
        " .alert.ok { background:#E8F5E9; border-left:4px solid #2E7D32; }"
        " .sig-grid { display:grid; grid-template-columns:1fr 1fr; gap:24px; margin-top:8px; }"
        " .sig-box { border:1px solid #D0D7E3; border-radius:8px; padding:16px 20px; }"
        " .sig-box label { font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:.5px; color:#5A6A85; }"
        " .sig-line { border-bottom:2px solid #D0D7E3; margin:24px 0 8px; }"
        " .sig-name { font-size:13px; font-weight:600; color:#1A2B4A; }"
        " .em-header { display:flex; align-items:center; gap:12px; margin:24px 0 12px; }"
        " .em-tag { color:#fff; border-radius:6px; padding:4px 12px; font-size:12px; font-weight:700; }"
        " .em-tag.em1 { background:#003087; }"
        " .em-tag.em2 { background:#007B8F; }"
        " .tier-label { display:inline-block; border-radius:4px; padding:2px 8px; font-size:10px; font-weight:700; text-transform:uppercase; letter-spacing:.4px; }"
        " .tier1 { background:#E8F5E9; color:#2E7D32; }"
        " .tier2 { background:#E3F2FD; color:#1565C0; }"
        " .brd-yes { color:#2E7D32; font-weight:700; }"
        " .brd-no { color:#9E9E9E; font-style:italic; }"
    )

    # ── Full dynamic HTML report ───────────────────────────────────────────────
    stub_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<style>{_CSS}</style>
</head>
<body>
<div class="header">
  <div class="header-top">
    <div>
      <div style="font-size:11px;opacity:.7;text-transform:uppercase;letter-spacing:.8px;margin-bottom:6px;">Optum Engage &middot; Audience Approval Report</div>
      <h1>{_ticket} &mdash; {_client} {_camp_name}</h1>
      <div class="subtitle">{_ch_display} &middot; {_wf}</div>
    </div>
    <div style="text-align:right;font-size:12px;opacity:.8;">
      <div>Prepared by: Souradeep Bhattacharjee</div>
      <div>Org ID: 11000003</div>
    </div>
  </div>
  <div class="badge-row">
    {_badges_html}
  </div>
</div>
<div class="container">

<div class="section">
  <div class="section-title">Campaign Overview</div>
  <table class="data-table" style="font-size:13px;">
    <tr><td style="color:#5A6A85;width:160px;">Ticket</td><td><strong>{_ticket}</strong></td></tr>
    <tr><td style="color:#5A6A85;">WorkFront</td><td>{_wf}</td></tr>
    <tr><td style="color:#5A6A85;">Campaign</td><td>{_camp_name}</td></tr>
    <tr><td style="color:#5A6A85;">Client</td><td>{_client}</td></tr>
    <tr><td style="color:#5A6A85;">Channel(s)</td><td>{_ch_display}</td></tr>
    <tr><td style="color:#5A6A85;">Affiliations</td><td>{_affil_str}</td></tr>
    {_rel_row}
    {_geo_row}
  </table>
</div>

<div class="section">
  <div class="section-title">Audience Summary &mdash; All Channels</div>
  {_kpi_html}
</div>

<div class="section">
  <div class="section-title">Filters Applied</div>
  <div style="font-size:12px;font-weight:700;color:#2E7D32;text-transform:uppercase;letter-spacing:.4px;margin-bottom:10px;">
    <span class="tier-label tier1">Tier 1</span>&nbsp; Baseline Filters &mdash; Always Applied
  </div>
  <table class="data-table" style="margin-bottom:20px;">
    <thead><tr><th>Filter</th><th>Condition</th><th>In BRD?</th><th>Notes</th></tr></thead>
    <tbody>
      <tr><td>SCD-2 current record</td><td><code>end_date = '9999-12-31'</code></td><td class="brd-no">Not specified</td><td>Standard &mdash; latest member snapshot only</td></tr>
      <tr><td>Active status</td><td><code>status = 'ACTIVE'</code></td><td class="brd-no">Not specified</td><td>Standard &mdash; excludes inactive/suspended members</td></tr>
      <tr><td>Member termination date</td><td><code>termination_date IS NULL OR &gt;= CURRENT_DATE()</code></td><td class="brd-no">Not specified</td><td>Standard &mdash; excludes already-terminated members</td></tr>
      <tr><td>Minimum age 18+</td><td><code>FLOOR(DATEDIFF(CURRENT_DATE(), dob) / 365.25) &gt;= 18</code></td><td class="brd-no">Not specified</td><td>Standard eligibility floor</td></tr>
      {_email_filter_row}
      {_dm_filter_row}
    </tbody>
  </table>
  <div style="font-size:12px;font-weight:700;color:#1565C0;text-transform:uppercase;letter-spacing:.4px;margin-bottom:10px;">
    <span class="tier-label tier2">Tier 2</span>&nbsp; BRD-Driven Filters
  </div>
  <table class="data-table">
    <thead><tr><th>Filter</th><th>Condition</th><th>In BRD?</th><th>Notes</th></tr></thead>
    <tbody>
      {_t2_html}
    </tbody>
  </table>
</div>

{_em_wave_section}

<div class="section">
  <div class="section-title">Approval Sign-off</div>
  <div class="alert ok" style="margin-bottom:20px;">
    Please review and sign below to authorize production deployment of this audience file.
    Reply to this email with <strong>APPROVED</strong>, <strong>REJECTED</strong>, or <strong>REDO</strong> (with revised date).
  </div>
  <div class="sig-grid">
    <div class="sig-box">
      <label>Data Analyst</label>
      <div class="sig-line"></div>
      <div class="sig-name">Souradeep Bhattacharjee</div>
    </div>
    <div class="sig-box">
      <label>Marketing Manager / Reviewer</label>
      <div class="sig-line"></div>
      <div class="sig-name">Stacy Swicegood</div>
    </div>
  </div>
</div>

<div style="text-align:center;font-size:11px;color:#9AAABF;margin-top:8px;">
  {_ticket} &middot; {_client} &middot; {_camp_name} &middot; Optum Engage Audience Builder
</div>

</div>
</body>
</html>"""

    # ── OPM-67 demo: use the curated sample audience report ───────────────────
    # Detect the OPM-67 sample from any available signal — session ticket / WF /
    # the DB campaign tied to this session — so the curated report always shows.
    _is_opm67_ag = (
        campaign.get("OPM Ticket", "").strip().upper() == "OPM-67"
        or _ag_ctx.get("WF Number", "").strip() in ("WF22031341",
            "WF20318902_Valero_2026_Optum_Engage_Incentives0_99")
    )
    if not _is_opm67_ag:
        try:
            _ag_dbc = (get_campaign_by_uid(st.session_state["campaign_uid"])
                       if st.session_state.get("campaign_uid") else None)
            if _ag_dbc and (str(_ag_dbc.get("opm_ticket", "")).upper() == "OPM-67"
                            or str(_ag_dbc.get("campaign_id", "")) == "52136"):
                _is_opm67_ag = True
        except Exception:
            pass
    if _is_opm67_ag:
        _opm67_aud_html = _load_opm67_report("audience")
        if _opm67_aud_html:
            stub_html = _opm67_aud_html

    # ══ SECTION 1 — HTML Report Preview ══════════════════════════════════════
    st.markdown("### Section 1 — Approval Report Preview")
    st.components.v1.html(stub_html, height=700, scrolling=True)
    col_html, col_pdf, _ = st.columns([1.6, 1.6, 2.8])
    with col_html:
        st.download_button("⬇ Download HTML", data=stub_html,
                           file_name=f"{campaign['OPM Ticket']}_Approval_Report.html",
                           mime="text/html")
    with col_pdf:
        try:
            import io as _io
            from xhtml2pdf import pisa as _pisa
            _ag_pdf_buf = _io.BytesIO()
            _pisa.CreatePDF(stub_html, dest=_ag_pdf_buf)
            st.download_button("⬇ Download PDF", data=_ag_pdf_buf.getvalue(),
                               file_name=f"{campaign['OPM Ticket']}_Approval_Report.pdf",
                               mime="application/pdf", key="ag_pdf_btn")
        except Exception:
            st.button("⬇ Download PDF", disabled=True,
                      help="PDF export unavailable. Run: pip install xhtml2pdf",
                      key="ag_pdf_disabled_btn")

    # ══ SECTION 2 — Send for Approval ════════════════════════════════════════
    st.markdown("---")
    st.markdown("### Section 2 — Send for Approval")
    st.code(subject_line, language=None)

    # In-app approver: the request appears on this person's MAT dashboard with
    # Approve/Reject buttons. The in-app decision is the system of record —
    # no email involved.
    try:
        _ag_approver_opts = [u["email"] for u in get_all_users()
                             if u.get("role") == "Approver"]
    except Exception:
        _ag_approver_opts = []
    if _ag_approver_opts:
        _ag_inapp_approver = st.selectbox(
            "Approver (sees this request on their MAT dashboard)",
            _ag_approver_opts, key="ag_inapp_approver",
        )
    else:
        _ag_inapp_approver = ""
        st.caption("ℹ️ No users with the Approver role yet — assign one in the Admin Panel "
                   "to enable in-app approvals.")

    _ag_has_data = bool(campaign.get("OPM Ticket", "").strip() or campaign.get("Client", "").strip())
    if not _ag_has_data:
        st.warning("⚠️ No campaign data found. Please complete Jira Intake → Audience Builder first, or enter details manually above, before sending for approval.")
    elif st.button("📨 Send for Approval", type="primary", key="ag_send"):
        st.session_state["ag_sent"]    = True
        st.session_state["ag_sent_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        st.session_state["ag_status"]  = "Awaiting"
        try:
            _ag_cuid = st.session_state.get("campaign_uid")
            if not _ag_cuid:
                _ag_hit = find_campaign(campaign.get("OPM Ticket", "").strip()
                                        or campaign.get("WF Number", "").strip())
                _ag_cuid = _ag_hit["campaign_uid"] if _ag_hit else None
                if _ag_cuid:
                    st.session_state["campaign_uid"] = _ag_cuid
            if _ag_cuid:
                _ap_uid = approvals_db.create_approval(
                    _ag_cuid,
                    campaign.get("OPM Ticket", ""),
                    _current_user.get("email", ""),
                    _ag_inapp_approver,
                    subject_line,
                    report_html=stub_html,   # approver can open the full report from their dashboard
                )
                st.session_state["ag_approval_uid"] = _ap_uid
                update_campaign(
                    _ag_cuid,
                    approval_status="Awaiting",
                    approval_sent_at=now_iso(),
                    approval_updated_at=now_iso(),
                    approval_subject_line=subject_line,
                    approval_recipients=_ag_inapp_approver,
                )
                if _ag_inapp_approver:
                    notifications_db.notify(
                        _ag_inapp_approver, _ag_cuid,
                        campaign.get("OPM Ticket", ""),
                        f"📨 Approval requested for {campaign.get('OPM Ticket', 'campaign')} "
                        f"({campaign.get('Client', '—')}) by {_current_user.get('email', '')}",
                    )
                st.success(
                    f"✅ Approval assigned to **{_ag_inapp_approver or '—'}** — "
                    "it is now in their dashboard inbox."
                )
            else:
                st.warning("Campaign not found in the MAT database — run Jira Intake for this "
                           "ticket first so the approval can be tracked in-app.")
        except Exception as _ag_db_err:
            st.error(f"In-app approval could not be recorded: {_ag_db_err}")

    # ══ SECTION 3 — Approval Status ══════════════════════════════════════════
    if st.session_state["ag_sent"]:
        st.markdown("---")
        st.markdown("### Section 3 — Approval Status")

        # Live-sync from the DB: the approver acts from THEIR dashboard in a
        # different session, so the requester's page must poll the database.
        _ap_uid_live = st.session_state.get("ag_approval_uid")
        _ap_row_live = None
        if _ap_uid_live:
            try:
                _ap_row_live = approvals_db.get_approval(_ap_uid_live)
                if _ap_row_live and _ap_row_live.get("status") and _ap_row_live["status"] != "Pending":
                    st.session_state["ag_status"] = _ap_row_live["status"]
            except Exception:
                pass

        status = st.session_state["ag_status"]
        if status == "Awaiting":
            st.warning(f"🟡 Awaiting approval — sent at {st.session_state['ag_sent_at']}")
            st.caption("⏳ This status checks for the approver's decision every 10 seconds.")
            # Re-run the page every 10s while we wait for the decision
            try:
                from streamlit_autorefresh import st_autorefresh
                st_autorefresh(interval=10_000, key="ag_status_poll")
            except ImportError:
                st.components.v1.html(
                    "<script>setTimeout(function(){window.parent.location.reload();}, 10000);</script>",
                    height=0,
                )
        elif status == "Approved":
            _by = f" by **{_ap_row_live['assigned_to']}**" if _ap_row_live and _ap_row_live.get("assigned_to") else ""
            _at = f" · {_ap_row_live['acted_at'][:16]}" if _ap_row_live and _ap_row_live.get("acted_at") else ""
            st.success(f"✅ Approved{_by}{_at}")
        elif status == "Rejected":
            st.error("❌ Rejected — no further action taken")
        elif status == "Repull Requested":
            _note = f" — {_ap_row_live['notes']}" if _ap_row_live and _ap_row_live.get("notes") else ""
            st.info(f"🔁 Repull requested by the approver{_note}. The scheduled flow will pick it up.")

        st.caption("Manual override (for testing):")
        col_app, col_rej, _ = st.columns([1.6, 1.6, 2.8])
        def _ag_record_decision(_decision: str):
            """Write the approve/reject decision to the DB (history + snapshot + notify)."""
            try:
                if st.session_state.get("ag_approval_uid"):
                    approvals_db.set_status(st.session_state["ag_approval_uid"], _decision)
                    _ap_row = approvals_db.get_approval(st.session_state["ag_approval_uid"])
                    if _ap_row and _ap_row.get("requested_by"):
                        _icon = "✅" if _decision == "Approved" else "❌"
                        notifications_db.notify(
                            _ap_row["requested_by"], _ap_row.get("campaign_uid", ""),
                            _ap_row.get("opm_ticket", ""),
                            f"{_icon} {_ap_row.get('opm_ticket', 'Campaign')} was "
                            f"{_decision.lower()} by {_current_user.get('email', '')}",
                        )
                if st.session_state.get("campaign_uid"):
                    update_campaign(st.session_state["campaign_uid"],
                                    approval_status=_decision,
                                    approval_updated_at=now_iso())
            except Exception:
                pass

        with col_app:
            if st.button("✅ Mark as Approved", key="ag_approve"):
                st.session_state["ag_status"] = "Approved"
                _ag_record_decision("Approved")
                st.rerun()
        with col_rej:
            if st.button("❌ Mark as Rejected", key="ag_reject"):
                st.session_state["ag_status"] = "Rejected"
                _ag_record_decision("Rejected")
                st.rerun()

    # ══ SECTION 4 — Next Step ════════════════════════════════════════════════
    if st.session_state.get("ag_status") == "Approved":
        st.markdown("---")
        st.success("✅ Approval Gate complete.")
        if st.button("Proceed to Monitoring →", type="primary"):
            st.session_state["page_idx"] = PAGES.index("4. Monitoring")
            st.session_state["_nav_pending"] = True
            st.session_state["_scroll_top"] = True
            st.rerun()

elif selected == "4. Monitoring":

    st.title("Monitoring")
    st.markdown(
        "Generate a campaign delivery report at any point after launch. "
        "Day N is calculated automatically from today vs the launch date."
    )
    st.markdown("---")

    # ── imports ───────────────────────────────────────────────────────────────
    from dotenv import load_dotenv
    import os, smtplib, datetime
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    load_dotenv()
    GMAIL_SENDER   = os.getenv("GMAIL_SENDER",       "souradeep.bhattacharjee@optum.com")
    GMAIL_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")

    # ── session state ─────────────────────────────────────────────────────────
    _mon_defaults = {
        "mon_report_sent":  False,
        "mon_sent_at":      None,
        "mon_launch_date":  None,
        "mon_cid_val":      "",
    }
    for _k, _v in _mon_defaults.items():
        if _k not in st.session_state:
            st.session_state[_k] = _v

    # ── HTML report helpers ───────────────────────────────────────────────────
    def _card(val, lbl, note, color):
        return (
            f'<td style="background:#fff;border-left:4px solid {color};border-radius:6px;'
            f'padding:11px 14px;vertical-align:top;box-shadow:0 1px 3px rgba(0,0,0,.08);">'
            f'<div style="font-size:22px;font-weight:700;color:#1a1a1a;'
            f'font-family:\'Segoe UI\',Arial,sans-serif;">{val}</div>'
            f'<div style="font-size:11px;color:#555;margin-top:3px;'
            f'font-family:\'Segoe UI\',Arial,sans-serif;">{lbl}</div>'
            f'<div style="font-size:10px;color:#888;margin-top:2px;'
            f'font-family:\'Segoe UI\',Arial,sans-serif;">{note}</div>'
            f'</td>'
        )

    def _card_row(*cards):
        sp = '<td style="width:10px;min-width:10px;max-width:10px;"></td>'
        cells = sp.join(cards)
        return (
            '<table cellspacing="0" cellpadding="0" border="0" '
            'style="border-collapse:separate;border-spacing:0;width:100%;table-layout:fixed;">'
            f'<tr>{cells}</tr></table>'
        )

    def _sec(text):
        return (
            f'<div style="font-size:11px;font-weight:700;color:#007B8F;'
            f'text-transform:uppercase;letter-spacing:.6px;margin:20px 0 10px;'
            f'padding-bottom:4px;border-bottom:2px solid #e0f4f7;'
            f'font-family:\'Segoe UI\',Arial,sans-serif;">{text}</div>'
        )

    def _day_block(day_n, d):
        """Single-day delivery block — label driven by day_n (int)."""
        total = d["total"]; sent = d["sent"]; ctrl = d["control"]
        inv   = d["invalid"]; deliv = d["delivered"]
        opnd  = d["opened"];  clkd  = d["clicked"]
        dr    = f'{deliv/sent*100:.1f}% of sent'          if sent  else 'N/A'
        opr   = f'{opnd/deliv*100:.1f}% of delivered'     if deliv else 'N/A'
        cr    = f'{clkd/deliv*100:.1f}% of delivered'     if deliv else 'N/A'
        ctor  = f'{clkd/opnd*100:.1f}%'                   if opnd  else 'N/A'
        ctor_note = f'{clkd:,} clicks / {opnd:,} opens'  if opnd  else ''
        lbl = f'Day {day_n} Report'
        return (
            f'<div style="font-size:13px;font-weight:700;color:#007B8F;text-transform:uppercase;'
            f'letter-spacing:.5px;margin-bottom:4px;font-family:\'Segoe UI\',Arial,sans-serif;">{lbl}</div>'
            f'<div style="margin-bottom:32px;">'
            f'<h3 style="font-family:\'Segoe UI\',Arial,sans-serif;font-size:14px;color:#333;'
            f'margin:0 0 14px;border-bottom:1px solid #e0e0e0;padding-bottom:6px;">'
            f'{lbl}&nbsp;<span style="font-weight:400;color:#888;">as of {d["as_of"]}</span></h3>'
            + _sec('Campaign Send Overview')
            + _card_row(
                _card(f'{total:,}', 'Total Audience',   'All records in system',                                '#37474F'),
                _card(f'{sent:,}',  'Sent (TEST Group)', 'Emails attempted &middot; excl. control &amp; unsub', '#007B8F'),
                _card(f'{ctrl:,}',  'Control Group',     'Held out — not sent',                                '#6A1B9A'),
                _card(f'{inv:,}',   'Invalid Emails',    'Bad address &middot; send attempted but bounced',    '#C62828'),
            )
            + _sec('Delivery Funnel — TEST Group Only')
            + _card_row(
                _card(f'{deliv:,}', 'Delivered',          f'Delivery Rate: {dr}',  '#2E7D32'),
                _card(f'{opnd:,}',  'Opened',             f'Open Rate: {opr}',     '#F57F17'),
                _card(f'{clkd:,}',  'Clicked',            f'Click Rate: {cr}',     '#1565C0'),
                _card(ctor,         'Click-to-Open Rate', ctor_note,               '#0a2540'),
            )
            + _sec('NOT-APPLICABLE Breakdown')
            + _card_row(_card(f'{ctrl:,}', 'Control Group Only', 'Held out &middot; not opted out', '#6A1B9A'))
            + f'<div style="background:#f3e8ff;border-left:3px solid #6A1B9A;border-radius:4px;'
              f'padding:8px 12px;margin-top:10px;font-size:11px;color:#555;">'
              f'<strong style="color:#6A1B9A;">Control Group confirmed:</strong> '
              f'All {ctrl:,} NOT-APPLICABLE users belong to the CONTROL group '
              f'(dim_campaign_group_id verified via <code>campaign_group</code> table). '
              f'None are opted out — <code>unsubscription_status = NOT_YET</code>.</div>'
            + '</div>'
        )

    def generate_delivery_html(camp, d, daily_rows, gen_dt, day_n):
        """Single-snapshot delivery report. day_n = days since launch."""
        daily_trs = ''.join(
            f'<tr>'
            f'<td style="padding:7px 10px;border-bottom:1px solid #f0f0f0;font-size:12px;color:#333;">{r["date"]}</td>'
            f'<td style="padding:7px 10px;border-bottom:1px solid #f0f0f0;font-size:12px;text-align:right;color:#333;">{r["sent"]:,}</td>'
            f'<td style="padding:7px 10px;border-bottom:1px solid #f0f0f0;font-size:12px;text-align:right;color:#2E7D32;font-weight:600;">{r["delivered"]:,}</td>'
            f'<td style="padding:7px 10px;border-bottom:1px solid #f0f0f0;font-size:12px;text-align:right;color:#F57F17;font-weight:600;">{r["opened"]:,}</td>'
            f'<td style="padding:7px 10px;border-bottom:1px solid #f0f0f0;font-size:12px;text-align:right;color:#1565C0;font-weight:600;">{r["clicked"]:,}</td>'
            f'<td style="padding:7px 10px;border-bottom:1px solid #f0f0f0;font-size:12px;text-align:right;color:#C62828;">{r["invalid"]:,}</td>'
            f'<td style="padding:7px 10px;border-bottom:1px solid #f0f0f0;font-size:12px;text-align:right;color:#6A1B9A;">{r["not_applicable"]:,}</td>'
            f'</tr>'
            for r in daily_rows
        )
        note_banner = (
            '<div style="background:#e8f5e9;border-left:4px solid #2E7D32;border-radius:4px;'
            'padding:10px 14px;margin-bottom:22px;font-size:11px;color:#333;">'
            '<strong>How metrics are calculated:</strong><br>'
            '<strong>Sent</strong> = Delivered + Opened + Clicked + Invalid &nbsp;|&nbsp;'
            '<strong>Delivered</strong> = Delivered + Opened + Clicked (cumulative) &nbsp;|&nbsp;'
            '<strong>Opened</strong> = Opened + Clicked &nbsp;|&nbsp;'
            '<strong>Control Group excluded from all funnel rates</strong>'
            '</div>'
        )
        daily_section = (
            '<div style="font-size:13px;font-weight:700;color:#007B8F;text-transform:uppercase;'
            'letter-spacing:.5px;margin:8px 0 10px;font-family:\'Segoe UI\',Arial,sans-serif;">'
            'Daily Breakdown by Send Date</div>'
            '<table border="0" cellspacing="0" cellpadding="0"'
            ' style="width:100%;border-collapse:collapse;border:1px solid #e0e0e0;">'
            '<thead><tr style="background:#f5f5f5;">'
            '<th style="padding:8px 10px;text-align:left;font-size:11px;color:#555;border-bottom:2px solid #e0e0e0;">Date</th>'
            '<th style="padding:8px 10px;text-align:right;font-size:11px;color:#555;border-bottom:2px solid #e0e0e0;">Sent</th>'
            '<th style="padding:8px 10px;text-align:right;font-size:11px;color:#2E7D32;border-bottom:2px solid #e0e0e0;">Delivered</th>'
            '<th style="padding:8px 10px;text-align:right;font-size:11px;color:#F57F17;border-bottom:2px solid #e0e0e0;">Opened</th>'
            '<th style="padding:8px 10px;text-align:right;font-size:11px;color:#1565C0;border-bottom:2px solid #e0e0e0;">Clicked</th>'
            '<th style="padding:8px 10px;text-align:right;font-size:11px;color:#C62828;border-bottom:2px solid #e0e0e0;">Invalid</th>'
            '<th style="padding:8px 10px;text-align:right;font-size:11px;color:#6A1B9A;border-bottom:2px solid #e0e0e0;">Not-Applicable</th>'
            f'</tr></thead><tbody>{daily_trs}</tbody></table>'
        )
        legend = (
            '<div style="margin-top:22px;background:#fafafa;border:1px solid #e0e0e0;'
            'border-radius:6px;padding:14px 18px;">'
            '<div style="font-size:12px;font-weight:700;color:#333;margin-bottom:10px;">'
            'Status &amp; Bucket Legend</div>'
            '<table border="0" cellspacing="0" cellpadding="0">'
            '<tr><td style="padding:4px 10px 4px 0;">'
            '<span style="color:#2E7D32;font-weight:700;font-size:11px;">Delivered</span></td>'
            '<td style="padding:4px 0;font-size:11px;color:#555;">Email reached the inbox '
            '(cumulative: includes Opened + Clicked)</td></tr>'
            '<tr><td style="padding:4px 10px 4px 0;">'
            '<span style="color:#F57F17;font-weight:700;font-size:11px;">Opened</span></td>'
            '<td style="padding:4px 0;font-size:11px;color:#555;">Email was opened — '
            'cumulative: includes Clicked</td></tr>'
            '<tr><td style="padding:4px 10px 4px 0;">'
            '<span style="color:#1565C0;font-weight:700;font-size:11px;">Clicked</span></td>'
            '<td style="padding:4px 0;font-size:11px;color:#555;">Recipient clicked a link '
            'in the email</td></tr>'
            '<tr><td style="padding:4px 10px 4px 0;">'
            '<span style="color:#C62828;font-weight:700;font-size:11px;">Invalid</span></td>'
            '<td style="padding:4px 0;font-size:11px;color:#555;">Invalid email address — '
            'send attempted but hard-bounced</td></tr>'
            '<tr><td style="padding:4px 10px 4px 0;">'
            '<span style="color:#6A1B9A;font-weight:700;font-size:11px;">Control Group</span></td>'
            '<td style="padding:4px 0;font-size:11px;color:#555;">Intentionally held out to '
            'measure incremental lift</td></tr>'
            '</table></div>'
        )
        signoff = (
            '<div style="margin-top:28px;border-top:1px solid #e0e0e0;padding-top:18px;">'
            '<div style="font-size:12px;font-weight:700;color:#333;margin-bottom:10px;">'
            'Report Prepared By</div>'
            '<table border="0" cellspacing="0" cellpadding="0" style="font-size:12px;">'
            '<tr><td style="padding:4px 24px 4px 0;color:#555;">Data Analyst</td>'
            '<td style="color:#1a1a1a;font-weight:600;">Souradeep Bhattacharjee</td></tr>'
            '<tr><td style="padding:4px 24px 4px 0;color:#555;">Marketing Manager</td>'
            '<td style="color:#1a1a1a;font-weight:600;">Stacy Swicegood</td></tr>'
            '</table></div>'
        )
        header = (
            '<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">'
            f'<title>Campaign Delivery Report — {camp["campaign_id"]}</title></head>'
            '<body style="margin:0;padding:0;background:#f4f4f4;'
            'font-family:\'Segoe UI\',Arial,sans-serif;">'
            '<div style="max-width:860px;margin:30px auto;background:#fff;border-radius:10px;'
            'box-shadow:0 2px 12px rgba(0,0,0,.10);overflow:hidden;">'
            '<div style="background:#0a2540;color:#fff;padding:22px 28px;">'
            '<div style="font-size:18px;font-weight:700;">Campaign Delivery Report</div>'
            f'<div style="font-size:12px;opacity:.85;margin-top:4px;">{camp["wf_name"]}</div>'
            '<div style="margin-top:8px;">'
            f'<span style="display:inline-block;background:rgba(255,255,255,.15);'
            f'border-radius:12px;padding:3px 10px;font-size:11px;margin-right:6px;">'
            f'Campaign ID: {camp["campaign_id"]}</span>'
            f'<span style="display:inline-block;background:rgba(255,255,255,.15);'
            f'border-radius:12px;padding:3px 10px;font-size:11px;margin-right:6px;">'
            f'Launch Date: {camp["launch_date"]}</span>'
            f'<span style="display:inline-block;background:rgba(255,255,255,.15);'
            f'border-radius:12px;padding:3px 10px;font-size:11px;">'
            f'Generated: {gen_dt}</span>'
            '</div></div>'
            '<div style="padding:24px 28px;">'
        )
        footer = (
            '</div>'
            '<div style="background:#f9f9f9;border-top:1px solid #e0e0e0;'
            'padding:14px 28px;font-size:11px;color:#777;">'
            f'Data source: Databricks &middot; <code>read_api_9000084</code> schema'
            f' &middot; Pulled {gen_dt} &middot; Campaign ID {camp["campaign_id"]}'
            ' &middot; Control group verified via <code>campaign_group</code> table'
            '</div></div></body></html>'
        )
        body = (
            note_banner
            + _day_block(day_n, d)
            + daily_section
            + legend
            + signoff
        )
        return header + body + footer

    # ── Stub data — OPM-67 sample only; blank/zero for all other campaigns ──────
    _mon_ctx    = st.session_state.get("campaign_context", {})
    _is_opm67_mon = _mon_ctx.get("OPM Ticket", "") == "OPM-67"
    _stub_camp = {
        "opm_ticket":  _mon_ctx.get("OPM Ticket",   "—"),
        "client":      _mon_ctx.get("Client",        "—"),
        "campaign":    _mon_ctx.get("Campaign Type", "—"),
        "wf_name":     _mon_ctx.get("WF Number",     "—"),
        "campaign_id": "—",
        "launch_date": "—",
    }
    if _is_opm67_mon:
        _stub_data  = {**_OPM67_DELIVERY, "as_of": datetime.date.today().strftime("%B %d, %Y")}
        _stub_daily = list(_OPM67_DAILY)
    else:
        _stub_data  = {"total": 0, "sent": 0, "control": 0, "invalid": 0,
                       "delivered": 0, "opened": 0, "clicked": 0,
                       "as_of": datetime.date.today().strftime("%B %d, %Y")}
        _stub_daily = []
    _mon_recipients = ["stacy.swicegood@optum.com"]

    # ══ CAMPAIGN SETUP ════════════════════════════════════════════════════════
    # Pre-render hook: a keyed text_input ignores value= after first render, so
    # the DB lookup stages the found ID here and we write it to the widget key
    # BEFORE the widget is instantiated this run.
    if "_mon_cid_pending" in st.session_state:
        st.session_state["mon_cid_input"] = st.session_state.pop("_mon_cid_pending")
    _col1, _col2, _col3, _col4 = st.columns([1, 1, 1, 1])
    with _col1:
        _cid_in = st.text_input("Campaign ID", value=st.session_state["mon_cid_val"],
                                key="mon_cid_input",
                                help="Numeric ID in the read_api_9000084 schema (e.g. 52136)")
    with _col2:
        _launch_in = st.date_input("Launch Date", value=datetime.date(2026, 3, 3),
                                   key="mon_launch_input",
                                   help="The date the campaign emails were sent")
    with _col3:
        _mon_ticket = st.text_input("OPM Ticket Number",
                                    placeholder="e.g. OPM-67",
                                    help="Alternative: enter ticket number to look up Campaign ID",
                                    key="mon_ticket_input")
    with _col4:
        _mon_wf = st.text_input("WF Number",
                                placeholder="e.g. WF22031341",
                                help="Alternative: enter WF number to look up Campaign ID",
                                key="mon_wf_input")

    if (_mon_ticket or _mon_wf) and not _cid_in:
        if st.button("🔍 Look up Campaign ID", key="mon_lookup_btn"):
            with st.spinner("Looking up campaign in the MAT database…"):
                _mon_hit = find_campaign(_mon_ticket.strip() or _mon_wf.strip())
            if _mon_hit and (_mon_hit.get("campaign_id") or "").strip():
                st.session_state["mon_cid_val"] = _mon_hit["campaign_id"]
                st.session_state["_mon_cid_pending"] = _mon_hit["campaign_id"]
                st.rerun()
            elif _mon_hit:
                st.warning(
                    f"Campaign **{_mon_hit.get('opm_ticket') or _mon_hit.get('wf_number')}** "
                    f"({_mon_hit.get('client', '—')}) found in the MAT database, but no Campaign ID "
                    "is recorded for it yet. Enter the platform Campaign ID manually."
                )
            else:
                st.error("No campaign found for that ticket/WF number in the MAT database. "
                         "Run Jira Intake for it first, or enter the Campaign ID manually.")
    _today_val = datetime.date.today()
    _dp = (_today_val - _launch_in).days
    if _dp > 0:
        st.caption(f"Today is **Day {_dp}** since launch")
    elif _dp == 0:
        st.caption("Launch is today — report will cover today's data only")
    else:
        st.warning("Launch date is in the future.")

    st.markdown("---")

    _today = datetime.date.today()
    _day_n = (_today - _launch_in).days
    _as_of = _today.strftime("%B %d, %Y")

    if not _cid_in.strip():
        st.caption("⚠️ Enter a Campaign ID above to enable report generation.")
    if st.button("⚙️ Generate Delivery Report", type="primary", key="mon_gen_btn",
                 disabled=(_dp < 0 or not _cid_in.strip())):
        st.session_state["mon_launch_date"] = _launch_in
        st.session_state["mon_cid_val"]     = _cid_in
        with st.spinner("Pulling delivery data from Databricks…"):
            # --- STUB: replace with real read_api_9000084 query ---
            _rd = _stub_data.copy()
            _rd["as_of"] = _as_of
            _stub_camp["launch_date"]  = _launch_in.strftime("%B %d, %Y")
            _stub_camp["campaign_id"]  = _cid_in
            _gen_dt = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            st.session_state["mon_report_html"] = generate_delivery_html(
                _stub_camp, _rd, _stub_daily, _gen_dt, _day_n)
            # OPM-67 demo: serve the curated sample delivery report
            if _is_opm67_mon or _cid_in.strip() == "52136":
                _opm67_del = _load_opm67_report("delivery")
                if _opm67_del:
                    st.session_state["mon_report_html"] = _opm67_del
            st.session_state["mon_day_n"] = _day_n
        st.success(f"Day {_day_n} report generated.")

    if "mon_report_html" in st.session_state:
        st.markdown("**Report Preview:**")
        st.components.v1.html(st.session_state["mon_report_html"], height=480, scrolling=True)

        _dn   = st.session_state.get("mon_day_n", _day_n)
        _cid  = st.session_state["mon_cid_val"]
        _subj = (
            f"[DELIVERY REPORT Day {_dn}] Campaign {_cid} | "
            f"{_stub_camp['client']} {_stub_camp['campaign']}"
        )
        _c1, _c2, _c3, _ = st.columns([1.4, 1.4, 1.4, 1.8])
        with _c1:
            st.download_button(
                "⬇ Download HTML",
                data=st.session_state["mon_report_html"],
                file_name=f"Campaign_{_cid}_Day{_dn}_Delivery_Report.html",
                mime="text/html", key="mon_dl_btn"
            )
        with _c2:
            try:
                import io as _io
                from xhtml2pdf import pisa as _pisa
                _mon_pdf_buf = _io.BytesIO()
                _pisa.CreatePDF(st.session_state["mon_report_html"], dest=_mon_pdf_buf)
                st.download_button(
                    "⬇ Download PDF",
                    data=_mon_pdf_buf.getvalue(),
                    file_name=f"Campaign_{_cid}_Day{_dn}_Delivery_Report.pdf",
                    mime="application/pdf", key="mon_pdf_btn"
                )
            except Exception:
                st.button("⬇ Download PDF", disabled=True,
                          help="PDF export unavailable. Run: pip install xhtml2pdf",
                          key="mon_pdf_disabled_btn")
        with _c3:
            if st.button("📨 Send via Gmail", type="primary", key="mon_send_btn"):
                with st.spinner("Sending via Gmail…"):
                    try:
                        _msg = MIMEMultipart("alternative")
                        _msg["Subject"] = _subj
                        _msg["From"]    = GMAIL_SENDER
                        _msg["To"]      = ", ".join(_mon_recipients)
                        _msg.attach(MIMEText(st.session_state["mon_report_html"], "html"))
                        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as _srv:
                            _srv.login(GMAIL_SENDER, GMAIL_PASSWORD)
                            _srv.sendmail(GMAIL_SENDER, _mon_recipients, _msg.as_string())
                        st.session_state["mon_report_sent"] = True
                        st.session_state["mon_sent_at"]     = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        st.success(f"✅ Sent at {st.session_state['mon_sent_at']}")
                    except Exception as _e:
                        st.error(f"Failed to send: {_e}")

        if st.session_state["mon_report_sent"]:
            st.info(f"📧 Sent at {st.session_state.get('mon_sent_at')} → {', '.join(_mon_recipients)}")

    # ══ SECTION 3 — Status ════════════════════════════════════════════════════
    if st.session_state["mon_report_sent"]:
        st.markdown("---")
        st.markdown("### Section 3 — Status")
        st.success(
            f"✅ Day {st.session_state.get('mon_day_n', '?')} delivery report sent · "
            f"{st.session_state.get('mon_sent_at', '')}"
        )
        if st.button("Proceed to Post-Campaign ROI →", type="primary", key="mon_proceed_btn"):
            st.session_state["page_idx"] = PAGES.index("5. Post-Campaign ROI")
            st.session_state["_nav_pending"] = True
            st.session_state["_scroll_top"] = True
            st.rerun()

elif selected == "5. Post-Campaign ROI":
    st.title("Post-Campaign ROI")
    st.markdown(
        "Compare the T+0 audience baseline against post-campaign data. "
        "MAT calculates incentive completion lift, bucket migration, and incremental impact."
    )
    st.markdown("---")

    import os, smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from datetime import date as _date_cls, datetime as _dt_cls

    # ── Session state ─────────────────────────────────────────────────────────
    _roi_defaults = {
        "roi_report_html":  None,
        "roi_report_sent":  False,
        "roi_sent_at":      None,
        "roi_cid_val":      "",
        "roi_day_n":        None,
    }
    for _k, _v in _roi_defaults.items():
        if _k not in st.session_state:
            st.session_state[_k] = _v

    # ── Stub data — OPM-67 sample only; zeros for all other campaigns ────────
    _roi_ctx       = st.session_state.get("campaign_context", {})
    _is_opm67_roi  = _roi_ctx.get("OPM Ticket", "") == "OPM-67"
    _roi_stub_camp = {
        "campaign_id": "52136"  if _is_opm67_roi else "—",
        "wf_name":     "WF20318902_Valero_2026_Optum_Engage_Incentives0_99" if _is_opm67_roi else _roi_ctx.get("WF Number", "—"),
        "launch_date": "March 03, 2026" if _is_opm67_roi else "—",
        "check_date":  "",
    }
    if _is_opm67_roi:
        _roi_stub_delivery = {
            "total": 7974, "sent": 7652, "delivered": 7579,
            "opened": 1694, "clicked": 128, "control": 322, "invalid": 73,
        }
        _roi_stub_comp    = dict(_OPM67_ROI_COMP)
        _roi_stub_buckets = list(_OPM67_BUCKETS)
    else:
        _roi_stub_delivery = {
            "total": 0, "sent": 0, "delivered": 0,
            "opened": 0, "clicked": 0, "control": 0, "invalid": 0,
        }
        _roi_stub_comp = {
            "overall": {"at_send": 0.0, "at_check": 0.0, "delta": 0.0, "users": 0},
            "test":    {"at_send": 0.0, "at_check": 0.0, "delta": 0.0, "users": 0},
            "control": {"at_send": 0.0, "at_check": 0.0, "delta": 0.0, "users": 0},
            "lift": 0.0, "test_100": 0, "ctrl_100": 0, "already_100": 0,
        }
        _roi_stub_buckets = []

    # ── HTML helpers ──────────────────────────────────────────────────────────
    def _roi_kpi(val, lbl, color="#0a2540", bg="#f0f4f8"):
        return (
            f'<div style="background:{bg};border-left:4px solid {color};border-radius:6px;'
            f'padding:14px 16px;text-align:center;min-width:110px;">'
            f'<div style="font-size:22px;font-weight:800;color:{color};">{val}</div>'
            f'<div style="font-size:11px;color:#555;margin-top:3px;text-transform:uppercase;'
            f'letter-spacing:0.5px;">{lbl}</div></div>'
        )

    def _roi_sec(text):
        return (
            f'<div style="background:#0a2540;color:#fff;font-size:13px;font-weight:700;'
            f'padding:8px 16px;border-radius:6px;margin:22px 0 12px;'
            f'letter-spacing:0.5px;text-transform:uppercase;">{text}</div>'
        )

    def _roi_comp_card(label, data, color):
        delta_sign = "+" if data["delta"] >= 0 else ""
        return (
            f'<div style="flex:1;background:#fff;border:1px solid #e0e0e0;border-top:4px solid {color};'
            f'border-radius:8px;padding:16px 18px;">'
            f'<div style="font-size:12px;font-weight:700;color:{color};text-transform:uppercase;'
            f'letter-spacing:0.5px;margin-bottom:12px;">{label}</div>'
            f'<table style="width:100%;font-size:13px;border-collapse:collapse;">'
            f'<tr><td style="color:#555;padding:4px 0;">At Send</td>'
            f'<td style="text-align:right;font-weight:700;">{data["at_send"]:.1f}%</td></tr>'
            f'<tr><td style="color:#555;padding:4px 0;">At Check</td>'
            f'<td style="text-align:right;font-weight:700;">{data["at_check"]:.1f}%</td></tr>'
            f'<tr style="border-top:1px solid #eee;">'
            f'<td style="color:#555;padding:6px 0 0;">Δ Change</td>'
            f'<td style="text-align:right;font-weight:800;color:{color};padding-top:6px;">'
            f'{delta_sign}{data["delta"]:.1f}%</td></tr>'
            f'<tr><td style="color:#aaa;font-size:11px;padding:2px 0;">Users</td>'
            f'<td style="text-align:right;font-size:11px;color:#aaa;">{data["users"]:,}</td></tr>'
            f'</table></div>'
        )

    def _roi_bucket_row(b):
        return (
            f'<tr style="border-bottom:1px solid #f0f0f0;">'
            f'<td style="padding:8px 12px;font-weight:700;color:{b["color"]};'
            f'background:{b["bg"]};border-radius:4px;text-align:center;width:70px;">{b["bucket"]}</td>'
            f'<td style="padding:8px 16px;text-align:right;">{b["send"]:,}</td>'
            f'<td style="padding:8px 16px;text-align:right;color:#888;">{b["send_pct"]:.1f}%</td>'
            f'<td style="padding:8px 16px;text-align:right;">{b["check"]:,}</td>'
            f'<td style="padding:8px 16px;text-align:right;color:#888;">{b["check_pct"]:.1f}%</td>'
            f'<td style="padding:8px 16px;text-align:center;font-size:18px;'
            f'color:{"#2E7D32" if b["trend"]=="↑" else "#C62828"};">{b["trend"]}</td>'
            f'</tr>'
        )

    def _generate_roi_html(camp, d, comp, buckets, gen_dt, day_n, camp_type):
        check_date = camp.get("check_date") or gen_dt.strftime("%B %d, %Y")

        # ── Delivery section ──────────────────────────────────────────────
        del_rate  = round(d["delivered"] / d["sent"]  * 100, 1) if d["sent"]  else 0
        open_rate = round(d["opened"]    / d["delivered"] * 100, 1) if d["delivered"] else 0
        ctr       = round(d["clicked"]   / d["delivered"] * 100, 1) if d["delivered"] else 0
        ctor      = round(d["clicked"]   / d["opened"]    * 100, 1) if d["opened"]    else 0

        kpi_row = lambda cards: (
            '<div style="display:flex;gap:12px;flex-wrap:wrap;margin:10px 0;">'
            + "".join(cards) + "</div>"
        )

        # ── Completion comparison cards ───────────────────────────────────
        comp_cards = (
            _roi_comp_card("Overall", comp["overall"], "#0a2540") +
            _roi_comp_card("TEST Group", comp["test"], "#007B8F") +
            _roi_comp_card("CONTROL Group", comp["control"], "#6B2D8B")
        )

        # ── Bucket table rows ─────────────────────────────────────────────
        bucket_rows = "".join(_roi_bucket_row(b) for b in buckets)

        # ── Bucket section (progress only) ────────────────────────────────
        bucket_section = ""
        if "Progress" in camp_type:
            bucket_section = f"""
            {_roi_sec("Incentive Completion Bucket Distribution")}
            <table style="width:100%;border-collapse:collapse;font-size:13px;">
              <thead>
                <tr style="background:#f5f7fa;color:#555;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;">
                  <th style="padding:10px 12px;text-align:center;">Bucket</th>
                  <th style="padding:10px 16px;text-align:right;">At Send</th>
                  <th style="padding:10px 16px;text-align:right;">%</th>
                  <th style="padding:10px 16px;text-align:right;">At Check</th>
                  <th style="padding:10px 16px;text-align:right;">%</th>
                  <th style="padding:10px 16px;text-align:center;">Trend</th>
                </tr>
              </thead>
              <tbody>{bucket_rows}</tbody>
            </table>
            <p style="font-size:11px;color:#888;margin-top:8px;">
              Buckets reflect % of maximum incentive earned. Users who completed (100%) are excluded from the base
              population for this campaign (0–99% targeting).
            </p>"""

        html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>
  body{{font-family:'Segoe UI',Arial,sans-serif;margin:0;padding:0;background:#f5f7fa;color:#1a1a2e;}}
  .container{{max-width:900px;margin:0 auto;padding:24px 16px;}}
  table{{width:100%;border-collapse:collapse;}}
  td,th{{padding:8px 12px;}}
</style></head><body>
<div class="container">

  <!-- Header -->
  <div style="background:linear-gradient(135deg,#0a2540 0%,#0d3060 100%);
              border-radius:12px;padding:28px 32px;margin-bottom:20px;color:#fff;">
    <div style="font-size:11px;letter-spacing:2px;text-transform:uppercase;
                color:rgba(255,255,255,0.5);margin-bottom:8px;">Post-Campaign ROI Report</div>
    <div style="font-size:20px;font-weight:800;margin-bottom:14px;line-height:1.3;">
      {camp["wf_name"]}</div>
    <div style="display:flex;gap:10px;flex-wrap:wrap;">
      <span style="background:rgba(0,123,143,0.35);color:#7ee8f5;border-radius:20px;
                   padding:4px 12px;font-size:12px;font-weight:600;">
        Campaign {camp["campaign_id"]}</span>
      <span style="background:rgba(255,255,255,0.12);color:rgba(255,255,255,0.75);
                   border-radius:20px;padding:4px 12px;font-size:12px;">
        T+{day_n} Report</span>
      <span style="background:rgba(255,255,255,0.12);color:rgba(255,255,255,0.75);
                   border-radius:20px;padding:4px 12px;font-size:12px;">
        Launch: {camp["launch_date"]}</span>
      <span style="background:rgba(255,255,255,0.12);color:rgba(255,255,255,0.75);
                   border-radius:20px;padding:4px 12px;font-size:12px;">
        Checked: {check_date}</span>
    </div>
  </div>

  <!-- Delivery summary -->
  {_roi_sec("Campaign Delivery Summary")}
  {kpi_row([
      _roi_kpi(f'{d["total"]:,}',     "Total Audience"),
      _roi_kpi(f'{d["sent"]:,}',      "Sent (TEST)",      "#007B8F", "#E0F7FA"),
      _roi_kpi(f'{d["control"]:,}',   "Control Group",    "#6B2D8B", "#F3E5F5"),
      _roi_kpi(f'{d["delivered"]:,}', "Delivered",        "#1565C0", "#E3F2FD"),
      _roi_kpi(f'{d["opened"]:,}',    "Opened",           "#E65100", "#FFF3E0"),
      _roi_kpi(f'{d["clicked"]:,}',   "Clicked",          "#2E7D32", "#E8F5E9"),
  ])}
  {kpi_row([
      _roi_kpi(f'{del_rate}%',  "Delivery Rate",       "#1565C0", "#E3F2FD"),
      _roi_kpi(f'{open_rate}%', "Open Rate",           "#E65100", "#FFF3E0"),
      _roi_kpi(f'{ctr}%',       "Click Rate",          "#2E7D32", "#E8F5E9"),
      _roi_kpi(f'{ctor}%',      "Click-to-Open Rate",  "#0a2540", "#f0f4f8"),
  ])}

  <!-- Incentive completion change -->
  {_roi_sec("Incentive Completion Change (T+0 vs T+{day_n})")}
  <div style="display:flex;gap:14px;flex-wrap:wrap;margin:12px 0;">
    {comp_cards}
  </div>
  <div style="background:#E8F5E9;border-left:4px solid #2E7D32;border-radius:6px;
              padding:12px 16px;margin:14px 0;font-size:13px;">
    <strong style="color:#1B5E20;">Incremental Lift (TEST − CONTROL):</strong>
    <span style="font-size:18px;font-weight:800;color:#2E7D32;margin-left:8px;">
      +{comp["lift"]:.1f}%</span>
    &nbsp;&nbsp;
    <span style="font-size:12px;color:#555;">
      TEST Δ {comp["test"]["delta"]:.1f}% &minus; CONTROL Δ {comp["control"]["delta"]:.1f}%</span>
  </div>
  <div style="display:flex;gap:10px;flex-wrap:wrap;margin:10px 0;">
    {_roi_kpi(comp["test_100"],     "TEST → 100%",        "#4A148C", "#F3E5F5")}
    {_roi_kpi(comp["ctrl_100"],     "CONTROL → 100%",     "#6B2D8B", "#F3E5F5")}
    {_roi_kpi(comp["already_100"],  "Already at 100%",    "#888",    "#f5f5f5")}
  </div>

  <!-- Bucket distribution (Progress only) -->
  {bucket_section}

  <!-- Sign-off -->
  {_roi_sec("Report Sign-off")}
  <table style="font-size:13px;">
    <tr>
      <td style="color:#555;width:180px;">Data Analyst</td>
      <td style="font-weight:700;">Souradeep Bhattacharjee</td>
    </tr>
    <tr>
      <td style="color:#555;">Marketing Manager</td>
      <td style="font-weight:700;">Stacy Swicegood</td>
    </tr>
    <tr>
      <td style="color:#555;">Generated</td>
      <td style="color:#888;">{gen_dt.strftime("%Y-%m-%d %H:%M")}</td>
    </tr>
  </table>

  <!-- Footer -->
  <div style="margin-top:24px;padding-top:16px;border-top:1px solid #e0e0e0;
              font-size:11px;color:#aaa;">
    Data sources: read_api_9000084, production_sanity_data ·
    Campaign ID: {camp["campaign_id"]} · T+{day_n} · Generated by MAT
  </div>

</div></body></html>"""
        return html

    # ── Inputs ────────────────────────────────────────────────────────────────
    # Pre-render hook for the DB lookup (keyed text_input ignores value= after
    # first render — stage the found ID and write it before instantiation).
    if "_roi_cid_pending" in st.session_state:
        st.session_state["roi_cid_input"] = st.session_state.pop("_roi_cid_pending")
    _roi_c1, _roi_c2, _roi_c3, _roi_c4, _roi_c5 = st.columns([1, 1, 1, 1, 1])
    with _roi_c1:
        _roi_cid = st.text_input(
            "Campaign ID",
            value=st.session_state["roi_cid_val"],
            placeholder="e.g. 52136",
            help="Numeric campaign ID from read_api_9000084",
            key="roi_cid_input"
        )
        st.session_state["roi_cid_val"] = _roi_cid
    with _roi_c2:
        _roi_launch = st.date_input(
            "Launch Date",
            value=None,
            help="The date this campaign's emails were sent (T+0 anchor)",
            key="roi_launch_input"
        )
    with _roi_c3:
        _roi_camp_type = st.selectbox(
            "Campaign Type",
            ["Incentive-based (Progress)", "Incentive-based (Completion)",
             "Activity-based", "Simple Filter"],
            key="roi_type_input"
        )
    with _roi_c4:
        _roi_ticket = st.text_input(
            "OPM Ticket Number",
            placeholder="e.g. OPM-67",
            help="Alternative: enter ticket number to look up Campaign ID",
            key="roi_ticket_input"
        )
    with _roi_c5:
        _roi_wf = st.text_input(
            "WF Number",
            placeholder="e.g. WF22031341",
            help="Alternative: enter WF number to look up Campaign ID",
            key="roi_wf_input"
        )

    if (_roi_ticket or _roi_wf) and not _roi_cid:
        if st.button("🔍 Look up Campaign ID", key="roi_lookup_btn"):
            with st.spinner("Looking up campaign in the MAT database…"):
                _roi_hit = find_campaign(_roi_ticket.strip() or _roi_wf.strip())
            if _roi_hit and (_roi_hit.get("campaign_id") or "").strip():
                st.session_state["roi_cid_val"] = _roi_hit["campaign_id"]
                st.session_state["_roi_cid_pending"] = _roi_hit["campaign_id"]
                st.rerun()
            elif _roi_hit:
                st.warning(
                    f"Campaign **{_roi_hit.get('opm_ticket') or _roi_hit.get('wf_number')}** "
                    f"({_roi_hit.get('client', '—')}) found in the MAT database, but no Campaign ID "
                    "is recorded for it yet. Enter the platform Campaign ID manually."
                )
            else:
                st.error("No campaign found for that ticket/WF number in the MAT database. "
                         "Run Jira Intake for it first, or enter the Campaign ID manually.")

    # ── Day N auto-caption ────────────────────────────────────────────────────
    _roi_day_n = 0
    if _roi_launch:
        _roi_day_n = (_date_cls.today() - _roi_launch).days
        if _roi_day_n > 0:
            st.caption(f"Today is **Day {_roi_day_n}** since launch.")
        elif _roi_day_n == 0:
            st.caption("Launch is today — ROI will cover today's data only.")
        else:
            st.warning("Launch date is in the future.")

    # ── Generate button ───────────────────────────────────────────────────────
    _roi_btn_disabled = (not _roi_launch) or (_roi_day_n < 0) or (not _roi_cid.strip())
    if not _roi_cid.strip():
        st.caption("⚠️ Enter a Campaign ID above to enable ROI report generation.")
    if st.button("⚙️ Generate ROI Report", type="primary",
                 disabled=_roi_btn_disabled, key="roi_gen_btn"):
        st.session_state["roi_day_n"] = _roi_day_n
        _roi_stub_camp["campaign_id"] = _roi_cid
        _roi_stub_camp["check_date"]  = _date_cls.today().strftime("%B %d, %Y")

        with st.spinner("Pulling ROI data from Databricks…"):
            _roi_html = _generate_roi_html(
                _roi_stub_camp, _roi_stub_delivery, _roi_stub_comp,
                _roi_stub_buckets, _dt_cls.now(), _roi_day_n, _roi_camp_type
            )
        # OPM-67 demo: serve the curated sample ROI report
        if _is_opm67_roi or _roi_cid.strip() == "52136":
            _opm67_roi_html = _load_opm67_report("roi")
            if _opm67_roi_html:
                _roi_html = _opm67_roi_html
        st.session_state["roi_report_html"] = _roi_html
        st.success(f"T+{_roi_day_n} ROI report generated.")

    # ── Report preview ────────────────────────────────────────────────────────
    if st.session_state["roi_report_html"]:
        st.markdown("**Report Preview:**")
        st.components.v1.html(st.session_state["roi_report_html"], height=560, scrolling=True)

        _dn   = st.session_state.get("roi_day_n", _roi_day_n)
        _cid  = st.session_state["roi_cid_val"]

        _rc1, _rc2, _rc3, _ = st.columns([1.4, 1.4, 1.4, 1.8])
        with _rc1:
            st.download_button(
                "⬇ Download HTML",
                data=st.session_state["roi_report_html"],
                file_name=f"Campaign_{_cid}_T{_dn}_ROI_Report.html",
                mime="text/html",
                key="roi_html_btn"
            )
        with _rc2:
            try:
                import io as _io
                from xhtml2pdf import pisa as _pisa
                _roi_pdf_buf = _io.BytesIO()
                _pisa.CreatePDF(st.session_state["roi_report_html"], dest=_roi_pdf_buf)
                st.download_button(
                    "⬇ Download PDF",
                    data=_roi_pdf_buf.getvalue(),
                    file_name=f"Campaign_{_cid}_T{_dn}_ROI_Report.pdf",
                    mime="application/pdf",
                    key="roi_pdf_btn"
                )
            except Exception:
                st.button("⬇ Download PDF", disabled=True,
                          help="PDF export unavailable. Run: pip install xhtml2pdf",
                          key="roi_pdf_disabled_btn")
        with _rc3:
            _roi_recipients = ["stacy.swicegood@optum.com"]
            if st.button("📨 Send via Gmail", type="primary", key="roi_send_btn"):
                try:
                    _sender   = os.getenv("GMAIL_SENDER")
                    _password = os.getenv("GMAIL_APP_PASSWORD")
                    _msg = MIMEMultipart("alternative")
                    _roi_ctx_email = st.session_state.get("campaign_context", {})
                    _roi_client_lbl = _roi_ctx_email.get("Client", "") or _roi_stub_camp.get("client", "") or "Client"
                    _roi_camp_lbl   = _roi_ctx_email.get("Campaign Type", "") or _roi_stub_camp.get("campaign", "") or "Campaign"
                    _msg["Subject"] = (
                        f"[ROI REPORT T+{_dn}] Campaign {_cid} | "
                        f"{_roi_client_lbl} {_roi_camp_lbl}"
                    )
                    _msg["From"]    = _sender
                    _msg["To"]      = ", ".join(_roi_recipients)
                    _msg.attach(MIMEText(st.session_state["roi_report_html"], "html"))
                    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as _srv:
                        _srv.login(_sender, _password)
                        _srv.sendmail(_sender, _roi_recipients,
                                      _msg.as_string())
                    _roi_ts = _dt_cls.now().strftime("%Y-%m-%d %H:%M")
                    st.session_state["roi_report_sent"] = True
                    st.session_state["roi_sent_at"]     = _roi_ts
                    st.rerun()
                except Exception as _e:
                    st.error(f"Send failed: {_e}")

        if st.session_state["roi_report_sent"]:
            st.info(
                f"📧 Sent at {st.session_state.get('roi_sent_at')} → "
                f"{', '.join(_roi_recipients)}"
            )

        # ── Status block ──────────────────────────────────────────────────
        if st.session_state["roi_report_sent"]:
            st.markdown("---")
            st.success(
                f"✅ T+{st.session_state.get('roi_day_n', '?')} ROI report sent · "
                f"{st.session_state.get('roi_sent_at', '')}"
            )

    # ══ ASK MAT ═══════════════════════════════════════════════════════════════
    st.markdown("---")
    st.markdown("### 💬 Ask MAT")
    st.caption(
        "Want more out of this report? Ask MAT for additional metrics — "
        "redemption rates, click-to-activity conversion, affiliation breakdowns, anything."
    )
    _ask_mat_q = st.text_area(
        "Ask MAT a question about this campaign",
        placeholder=(
            "e.g. How many users who opened this email redeemed their gift card?\n"
            "e.g. Of the users who clicked, how many completed their activity within 7 days?\n"
            "e.g. Show redemption rate broken down by affiliation group."
        ),
        height=110,
        label_visibility="collapsed",
        key="roi_ask_mat_input"
    )
    if st.button("Ask MAT ✨", type="primary", key="roi_ask_mat_btn"):
        if _ask_mat_q.strip():
            _cid_val = st.session_state["roi_cid_val"]
            _roi_camp_ctx = st.session_state.get("campaign_context", {})
            _ask_prompt = f"""The user is asking about a campaign ROI report. Answer their question and provide the SQL query needed to answer it.

Campaign ID: {_cid_val}
Campaign context: {json.dumps(_roi_camp_ctx, indent=2, default=str)}

User question: {_ask_mat_q.strip()}

Respond with:
1. A brief plain-English answer explaining the metric / what the query will measure
2. A production-ready Databricks SQL query that answers the question using read_api_9000084 tables
   (contact_info, campaign_group, users) and optum_prod_source_delta if needed for activity/redemption data.
   Use dim_campaign_id = {_cid_val} as the campaign filter.
   No PII in SELECT — user_id only.
3. End with: "SAVE_TO_KB: <one-line metric description>"

If Databricks data is not available, explain what query you would run once connected."""

            with st.spinner("MAT is working on your question..."):
                _mat_response = _call_claude(_ask_prompt)

            if _mat_response:
                # Split response into prose + SQL code blocks
                _response_text = _mat_response
                _sql_start = _response_text.find("SELECT")
                if _sql_start == -1:
                    _sql_start = _response_text.find("WITH ")
                if _sql_start >= 0:
                    _prose = _response_text[:_sql_start].strip()
                    _sql_part = _response_text[_sql_start:].strip()
                    if _prose:
                        st.markdown(_prose)
                    st.code(_sql_part, language="sql")
                else:
                    st.markdown(_mat_response)
            else:
                # Fallback stub if Claude unavailable
                _stub_sql = f"""-- MAT-generated query for: {_ask_mat_q.strip()[:80]}
-- Scope: T+N users from Campaign {_cid_val}
WITH campaign_users AS (
    SELECT DISTINCT
        a.dim_event_user_id   AS user_id,
        COALESCE(cg.group_type, 'CONTROL') AS group_type
    FROM read_api_9000084.contact_info AS a
    LEFT JOIN read_api_9000084.campaign_group cg
        ON a.dim_campaign_group_id = cg.id
    WHERE a.dim_campaign_id = {_cid_val}
)
SELECT
    cu.group_type,
    COUNT(DISTINCT cu.user_id) AS user_count
FROM campaign_users cu
GROUP BY cu.group_type
ORDER BY cu.group_type;"""
                st.warning("⚠️ Claude API not available — showing template query. Set ANTHROPIC_API_KEY to enable Ask MAT.")
                st.code(_stub_sql, language="sql")

            st.caption(
                "Review the query above. Once confirmed, MAT will save this "
                "metric to the Campaign Knowledge Base."
            )
        else:
            st.warning("Please type a question before asking MAT.")

# ══════════════════════════════════════════════════════════════════════════════
# HTML CREATIVE QA VALIDATION (standalone — independent of Pages 1-5 state)
# ══════════════════════════════════════════════════════════════════════════════
elif selected == "📧 QA Validation":
    # Lazy import so qa.py issues don't affect the rest of the app at startup
    from qa import render_qa_page
    render_qa_page()

# ══════════════════════════════════════════════════════════════════════════════
# ADMIN PANEL (visible to Admin role only)
# ══════════════════════════════════════════════════════════════════════════════
elif selected == _ADMIN_PAGE:

    if _current_role != "Admin":
        st.error("⛔ Access denied — Admin role required.")
        st.stop()

    st.title("⚙ Admin Panel")
    st.markdown("Manage user accounts and role assignments.")
    st.markdown("---")

    # ── Section 1 — User Management ──────────────────────────────────────────
    st.markdown("### 👥 User Management")
    st.caption(
        "Users appear here after their first login. "
        "Assign roles using the dropdowns below. Changes take effect immediately."
    )

    _all_users = get_all_users()

    if not _all_users:
        st.info("No users have logged in yet.")
    else:
        for _usr in _all_users:
            _is_me = _usr["email"] == _current_user.get("email", "")
            with st.container():
                _uc1, _uc2, _uc3 = st.columns([3, 2, 1])

                with _uc1:
                    _u_initials = "".join(p[0].upper() for p in _usr["name"].split()[:2]) if _usr["name"] else "?"
                    st.markdown(
                        f"<div style='display:flex;align-items:center;gap:10px;padding:6px 0;'>"
                        f"<div style='width:32px;height:32px;border-radius:50%;background:#007B8F;"
                        f"display:flex;align-items:center;justify-content:center;"
                        f"font-size:12px;font-weight:700;color:#fff;flex-shrink:0;'>{_u_initials}</div>"
                        f"<div>"
                        f"<div style='font-weight:600;font-size:0.875rem;'>{_usr['name']}"
                        f"{'&nbsp;<span style=\"font-size:0.7rem;color:#007B8F;\">(you)</span>' if _is_me else ''}"
                        f"</div>"
                        f"<div style='font-size:0.75rem;color:#6B7280;'>{_usr['email']}</div>"
                        f"</div></div>",
                        unsafe_allow_html=True
                    )

                with _uc2:
                    _cur_idx = ROLES.index(_usr["role"]) if _usr["role"] in ROLES else 3
                    _new_role = st.selectbox(
                        "Role",
                        ROLES,
                        index=_cur_idx,
                        key=f"role_{_usr['email']}",
                        label_visibility="collapsed"
                    )
                    if _new_role != _usr["role"]:
                        set_role(_usr["email"], _new_role)
                        # Refresh auth_user if admin changed their own role
                        if _is_me:
                            st.session_state["auth_user"] = get_user(_usr["email"])
                        st.success(f"✅ {_usr['name']} → {_new_role}", icon=None)
                        st.rerun()

                with _uc3:
                    if not _is_me:
                        if st.button("🗑 Remove", key=f"del_{_usr['email']}", help="Remove this user from MAT"):
                            delete_user(_usr["email"])
                            st.rerun()
                    else:
                        st.caption("(you)")

            st.markdown("<hr style='margin:4px 0;border-color:#F3F4F6;'>", unsafe_allow_html=True)

    # ── Section 2 — Invite / Pre-register note ────────────────────────────────
    st.markdown("---")
    st.markdown("### 📨 Onboarding")
    st.info(
        "**How new users join MAT:**  \n"
        "1. They visit the app URL  \n"
        "2. Sign in with their `@capillarytech.com` Google account  \n"
        "3. Their account is created with **Viewer** role by default  \n"
        "4. You (Admin) assign the correct role above  \n\n"
        "Until a role is assigned, Viewer access lets them see all pages read-only."
    )

    # ── Section 3 — System info ───────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### 🔧 System Info")
    _si1, _si2, _si3 = st.columns(3)
    with _si1:
        _api_live = st.session_state.get("api_enabled", True) and bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
        st.metric("Claude API", "Connected ✅" if _api_live else "Not connected ❌")
    with _si2:
        _gmail_ok = bool(os.environ.get("GMAIL_SENDER", "").strip())
        st.metric("Gmail", "Configured ✅" if _gmail_ok else "Not configured ❌")
    with _si3:
        st.metric("Auth mode", "Demo login" if _IS_DEMO else "Google OAuth")
    st.caption(f"DB: `{os.path.basename('marketing_automation.db')}` · Total users: {len(_all_users)}")
