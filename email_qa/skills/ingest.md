---
description: Ingest a campaign for QA validation — assemble the canonical campaigns/<slug>/ structure from an uploaded HTML, a selected Drive assets subfolder, and an optional Jira ticket. Resolves W-code, fetches Jira context (MCP), pulls Drive folder (rclone), and classifies files into the expected layout.
argument-hint: <html-path> <drive-folder-id> [jira-ticket-key]
---

# /ingest — assemble a campaign folder from HTML + Drive + Jira inputs

You are the ingest stage of the email QA pipeline. Your job: take three inputs (one HTML file, one Drive assets-subfolder ID, optionally one Jira ticket key) and produce a `campaigns/<slug>/` folder with the canonical layout that downstream pipeline stages expect.

## Inputs

| Input | Required | Source |
|---|---|---|
| `$1` — HTML file path | Yes | Uploaded by marketer (Streamlit, UI, or CLI) |
| `$2` — Drive folder ID | Yes | Picked by marketer from the dropdown (one subfolder under `<BASE_DRIVE>/assets/`) |
| `$3` — Jira ticket key | No | E.g. `OPM-73`. If provided, enriches campaign-meta.json and enables writeback. Skip silently if omitted. |

The Drive folder ID refers to the **specific campaign subfolder**, not the base. The marketer's UI has already resolved which one to use.

## Step 1 — Parse W-code (used for slug + Jira lookup hint)

Run: `node scripts/ingest.mjs --parse-w-code $1`

Captures `{ w_code, source }`. If no W-code found in filename or body, the slug falls back to `<date>-no-wcode-<client>`; not fatal.

## Step 2 — Fetch Jira context (only if $3 provided)

If a ticket key is provided, use the Atlassian MCP tool `getJiraIssue` to fetch:

- `cloudId`: `69031ea7-8347-4ec3-a63d-9c7289f8dc4f` (capillarytech.atlassian.net)
- `issueIdOrKey`: `$3`
- `fields`: `["summary", "status", "issuetype", "priority", "assignee", "reporter", "duedate", "customfield_12310", "attachment"]`

Extract:

- `summary` → campaign program name
- `status.name` → current Jira status (informs writeback later)
- `assignee.displayName`, `reporter.displayName`
- `duedate`
- `customfield_12310` → ClientName (e.g. "Eaton")
- `attachment[]` → list of filenames (for fallback only — Drive folder is the canonical source)

Write the extracted context to `/tmp/jira-context-<slug>.json` as a flat object:

```json
{
  "key": "OPM-73",
  "summary": "WF21826321 Eaton 2026 ...",
  "status": "In Configuration",
  "client": "Eaton",
  "assignee": "Ayushi Rastogi",
  "reporter": "Rochan Tripathi",
  "duedate": "2026-06-16",
  "drive_folder_name": null
}
```

If the MCP call fails or the ticket doesn't exist, write `null` to that path and continue — Jira is optional context, not a blocker.

## Step 3 — Pull Drive folder into staging

Create a staging directory: `/tmp/ingest-staging-<slug>/`

Run rclone to copy the campaign subfolder:

```bash
rclone copy gdrive: /tmp/ingest-staging-<slug>/ \
  --drive-root-folder-id $2 \
  --no-traverse \
  --transfers=4 \
  --checkers=8
```

If rclone fails (no remote configured, no internet, folder not accessible), do not halt — write a marker file `/tmp/ingest-staging-<slug>/.drive-fetch-failed` containing the error, and continue with empty staging. The classifier handles missing Drive content gracefully (pipeline will run with only the uploaded HTML; vision review falls back to panel mode).

## Step 4 — Classify and lay down

Run the ingest runner:

```bash
node scripts/ingest.mjs --classify \
  --staging-dir /tmp/ingest-staging-<slug>/ \
  --html-path $1 \
  --w-code <from Step 1, or omit if null> \
  --jira-context /tmp/jira-context-<slug>.json
```

The runner:

1. Derives the slug as `<YYYY-MM-DD>-<W-code>-<client>` (or `<date>-no-wcode-campaign` if no W-code/client)
2. Creates `campaigns/<slug>/{input,output,extracted,qa}/` layout
3. Copies the uploaded HTML to `output/email.html`
4. Classifies each staged file by name pattern and moves it to the right target:

| Pattern | Target |
|---|---|
| `desktop-*.pdf` | `input/desktop.pdf` |
| `mobile-*.pdf` | `input/mobile.pdf` |
| `*_Details*.pdf` | `input/brd.pdf` |
| `*.pdf` (other) | `input/reference.pdf` (combined-page assumed) |
| `*.fig` | `input/<original-name>` |
| `links.json` / `link-mapping.json` | `input/link-mapping.json` |
| `*Logo*.png` / `*logo-*.png` | `input/assets/<name>` |
| `Hero*.png` | `input/assets/<name>` |
| `Icon_*.png` | `input/assets/<name>` |
| `CTA_*.png` | `input/assets/<name>` |
| `GettyImages-*` | `input/assets/<name>` |
| Other images | `input/assets/<name>` |

5. Writes `input/campaign-meta.json` with W-code, Jira context, classification summary

The runner prints a JSON result to stdout — capture it.

## Step 5 — Verify minimum inputs and report

Inspect the runner's classification result. The downstream pipeline requires at minimum:

- `output/email.html` ✓ (always present since marketer uploaded it)
- At least one PDF — either `input/desktop.pdf` + `input/mobile.pdf` OR `input/reference.pdf`

If neither PDF path is populated, warn the user the pipeline will skip pixel-diff + vision review but will still run style/compat/link checks. Do not halt.

## Step 6 — Return campaign_dir

Print the final campaign directory path on stdout in this exact format (downstream stages read it):

```
INGEST_OK campaign_dir=<absolute-path-to-campaigns/<slug>>
```

## Constraints

- Do not modify the uploaded HTML — copy it as-is to `output/email.html`
- Drive and Jira are both best-effort; pipeline must run even if one or both are unavailable
- Never overwrite an existing `campaigns/<slug>/` without clearing it first (the runner handles this via `rmSync`)
- If `$3` (Jira ticket) is omitted, do not invent one — leave Jira context as null and skip writeback later

## Failure modes — how to behave

| Failure | Behaviour |
|---|---|
| W-code not parseable | Continue with `no-wcode` slug fragment |
| Jira ticket not found | Continue with null Jira context |
| Drive folder empty or inaccessible | Continue with HTML-only; warn user |
| Both Drive and Jira fail | Continue with HTML-only ingestion; warn user; pipeline still runs the parts it can |
| Uploaded HTML missing or unreadable | Halt — this is the only hard requirement |
