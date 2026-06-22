---
description: Report-and-writeback composite QA stage — builds the marketer-readable HTML report from QA artifacts, then posts the QA result back to Jira as a comment and transitions the ticket status. Final stage of the email QA pipeline.
argument-hint: <campaign-dir-path>
---

# /report-and-writeback — build report, post to Jira, transition status

You are the final stage of the email QA pipeline. Your job: turn the JSON artifacts produced by `pre-flight` and `visual-validation` into (a) a self-contained HTML report and (b) a comment + status transition on the linked Jira ticket.

## Input

| Argument | Required | Notes |
|---|---|---|
| `$1` — campaign directory | Yes | Absolute path to `campaigns/<slug>/`. Must contain `input/campaign-meta.json`. |

The campaign-meta.json was synthesized by the ingest skill. Its `source.jira_ticket` field tells us whether to perform Jira writeback (skip silently if null).

## Step 1 — Build the report

Run:

```bash
node scripts/report.mjs $1
```

The runner:

1. Reads aggregated findings from `qa/pre-flight.json` + `qa/visual-validation.json`
2. Reads pixel diff stats from `qa/diff-stats.json`
3. Reads vision review summary from `qa/vision-review.json`
4. Reads campaign metadata from `input/campaign-meta.json`
5. Aggregates counts (critical / major / minor)
6. Decides: **MUST FIX** / **READY WITH WARNINGS** / **READY TO SEND**
7. Renders `qa/report.html` (standalone, embedded CSS, self-contained)
8. Prints summary JSON to stdout

Capture the stdout JSON. Example:

```json
{
  "slug": "2026-06-19-WF21826321-eaton",
  "decision": "READY WITH WARNINGS",
  "counts": { "critical": 0, "major": 11, "minor": 3 },
  "pixel_diff": { "desktop_mismatch_pct": 16.28, "mobile_mismatch_pct": 16.92 },
  "vision": { "sections_reviewed": 16, "cost_usd": 0.34 },
  "pipeline_failures": 0,
  "report_path": "/path/to/campaigns/.../qa/report.html",
  "campaign_meta": {
    "client": "Eaton",
    "w_code": "WF21826321",
    "jira_ticket": "OPM-73",
    "program": "WF21826321 Eaton 2026 Optum Engage Healthy Incentives Campaign"
  }
}
```

## Step 2 — Decide whether to write back to Jira

Inspect `summary.campaign_meta.jira_ticket`:

- If `null` → skip Steps 3-4. Report-only run. Print: `WRITEBACK_SKIPPED reason=no_jira_ticket`
- If a key like `OPM-73` → proceed to Step 3

## Step 3 — Post a QA-summary comment to Jira

Use the Atlassian MCP tool `addCommentToJiraIssue` with:

- `cloudId`: `69031ea7-8347-4ec3-a63d-9c7289f8dc4f`
- `issueIdOrKey`: from `summary.campaign_meta.jira_ticket`
- `commentBody`: a structured ADF block (or markdown — Atlassian accepts both)

### Comment body format

The comment should be scannable by a marketer in 5 seconds. Use this structure exactly:

```
🤖 Email QA — <DECISION>

Counts: <critical> critical · <major> major · <minor> minor
Pixel diff: desktop <X>% · mobile <Y>%
<if vision ran>Vision: <N> sections reviewed · $<cost></if>

<if decision == MUST FIX>
🚨 Blocking issues — must be fixed before send:
<list top 3 critical findings, one per line, format: • [<source>] <description>>
</if>

<if decision == READY WITH WARNINGS>
⚠️ Non-blocking issues — review before send:
<list top 3 major findings, one per line>
</if>

<if decision == READY TO SEND>
✓ No issues found. Cleared to send.
</if>

📋 Full report: <link to qa/report.html, or note that it's hosted at opmXX_reports/qa_report.html when integrated with mat-app>
```

Keep it under ~500 characters of body text. The full detail is in the report.html.

## Step 4 — Transition the Jira ticket status

Use the Atlassian MCP tool `transitionJiraIssue` with:

- `cloudId`: `69031ea7-8347-4ec3-a63d-9c7289f8dc4f`
- `issueIdOrKey`: from `summary.campaign_meta.jira_ticket`
- `transition.id`: per the mapping below

### Decision → transition mapping (OPM project)

| QA decision | Transition ID | Target status | Rationale |
|---|---|---|---|
| **READY TO SEND** | `6` | QA Approved for Release | All clear — ready for the next workflow step |
| **READY WITH WARNINGS** | `6` | QA Approved for Release | Non-blocking; team can review the warnings in the comment |
| **MUST FIX** | `2` | In Dev | Sends back to the developer; the comment lists what to fix |

These transition IDs are project-specific. If we extend to other projects later (not just OPM), we'll need to map per-project. For now, hardcode to the OPM mapping.

### If the ticket is already in the target status

`transitionJiraIssue` may fail if the workflow doesn't allow the transition from the current state (e.g. ticket is in "Closed"). In that case:

1. Catch the error
2. Add a follow-up comment: `⚠️ Unable to auto-transition status from <current> to <target>. Please move manually.`
3. Continue — do not halt the pipeline

## Step 5 — Report back

Print the final summary line(s) so the master skill can capture it:

```
REPORT_OK report=<absolute path to qa/report.html>
WRITEBACK_OK ticket=<JIRA-KEY> decision=<DECISION> transition=<TRANSITION_ID>
```

OR if writeback was skipped:

```
REPORT_OK report=<absolute path to qa/report.html>
WRITEBACK_SKIPPED reason=no_jira_ticket
```

## Failure modes

| Failure | Behaviour |
|---|---|
| `qa/pre-flight.json` AND `qa/visual-validation.json` both missing | Halt — nothing to report |
| `report.mjs` fails | Halt — without a report there's nothing useful to write back |
| `input/campaign-meta.json` missing | Continue with degraded meta (use slug only); writeback impossible without jira_ticket field |
| `addCommentToJiraIssue` fails | Continue — try transition anyway. Print warning. |
| `transitionJiraIssue` fails | Continue — comment may have already posted. Print warning + clean error reason. Do NOT retry the comment. |
| Both MCP calls fail (e.g. token expired) | Print `WRITEBACK_FAILED reason=<error>` but still emit `REPORT_OK` |

## Constraints

- The report is read-only on QA artifacts — it consumes JSONs but doesn't modify them
- The Jira comment is posted ONCE per pipeline run. If the same ticket gets QA'd twice (re-run after a fix), a second comment is added — don't try to edit the previous one
- Status transition is idempotent at the Jira API level — if the ticket is already in the target status, the API returns success and nothing changes
- Never transition to `Closed` from this skill — that's a human-approval decision
- Comments use plain markdown / ADF — no images or large blocks. The full report lives at `qa/report.html` (or `opmXX_reports/qa_report.html` once integrated with mat-app)

## Why composite

1. **Single source of truth** — report content and Jira comment content are derived from the same summary JSON, can never drift
2. **Atomic writeback** — comment + transition happen together so the ticket always reflects the report's state
3. **One MCP-touching skill** — keeps Atlassian dependencies localized to one file, simpler to audit and to swap for REST when going to CI
