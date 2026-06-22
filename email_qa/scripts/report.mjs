#!/usr/bin/env node
// scripts/report.mjs
// Build a marketer-readable QA report from the JSON artifacts produced by
// pre-flight + visual-validation. Emits qa/report.html (standalone, embedded CSS)
// and prints a summary JSON to stdout for the orchestrator skill to consume.
//
// Usage:
//   node scripts/report.mjs <campaign-dir>

import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join, basename, resolve } from "node:path";

// ────────────────────── CLI ──────────────────────
const campaignDir = resolve(process.argv[2] ?? "");
if (!campaignDir || !existsSync(campaignDir)) {
  console.error("Usage: node scripts/report.mjs <campaign-dir>");
  process.exit(2);
}

const qaDir = join(campaignDir, "qa");
const slug = basename(campaignDir);

const safeRead = (p) => {
  try { return JSON.parse(readFileSync(p, "utf8")); }
  catch { return null; }
};

// ────────────────────── Read artifacts ──────────────────────
const preFlight        = safeRead(join(qaDir, "pre-flight.json"));
const visualValidation = safeRead(join(qaDir, "visual-validation.json"));
const styleAudit       = safeRead(join(qaDir, "style-audit.json"))  ?? { findings: [] };
const compatCheck      = safeRead(join(qaDir, "compat-check.json")) ?? { findings: [] };
const linkCheck        = safeRead(join(qaDir, "link-check.json"))   ?? { findings: [] };
const visionReview     = safeRead(join(qaDir, "vision-review.json")) ?? { findings: [] };
const diffStats        = safeRead(join(qaDir, "diff-stats.json"))   ?? {};
const meta             = safeRead(join(campaignDir, "input", "campaign-meta.json")) ?? {};

// ────────────────────── Aggregate findings ──────────────────────
const preFlightFindings = (preFlight?.findings) || [];
const visualFindings    = (visualValidation?.findings) || [];
const allFindings = [...preFlightFindings, ...visualFindings];

const counts = {
  critical: allFindings.filter((f) => f.severity === "critical").length,
  major:    allFindings.filter((f) => f.severity === "major").length,
  minor:    allFindings.filter((f) => f.severity === "minor").length,
};

// ────────────────────── Decision ──────────────────────
let decision = "READY TO SEND";
if (counts.critical > 0) decision = "MUST FIX";
else if (counts.major > 0 || counts.minor > 0) decision = "READY WITH WARNINGS";

const decisionClass = decision === "READY TO SEND" ? "ready"
  : decision === "MUST FIX" ? "fix" : "warnings";

// ────────────────────── HTML helpers ──────────────────────
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

const findingsRow = (f) => `<tr>
  <td><span class="sev sev-${f.severity}">${f.severity}</span></td>
  <td>${escapeHtml(f.category ?? "")}</td>
  <td>${escapeHtml((f.description ?? f.message ?? f.name ?? "").slice(0, 400))}</td>
</tr>`;

const visionFindingRow = (f) => `<tr>
  <td><span class="sev sev-${f.severity}">${f.severity}</span></td>
  <td>${escapeHtml(f.viewport ?? "")} · ${escapeHtml(f.section ?? "")}</td>
  <td>${escapeHtml(f.category ?? "")}</td>
  <td>${escapeHtml((f.description ?? "").slice(0, 500))}${f.suggested_fix ? `<br/><em>Fix:</em> ${escapeHtml(f.suggested_fix.slice(0, 300))}` : ""}</td>
</tr>`;

// Embedded CSS — keeps the report self-contained (no external link needed
// when the file is uploaded to a different host like mat-app's opmXX_reports/)
const EMBEDDED_CSS = `
  body.report { font-family: -apple-system, BlinkMacSystemFont, Segoe UI, Helvetica, Arial, sans-serif; max-width: 1100px; margin: 24px auto; padding: 0 24px; color: #222; line-height: 1.5; }
  h1 { margin: 0 0 8px 0; font-size: 24px; }
  h2 { margin-top: 32px; padding-top: 12px; border-top: 1px solid #e5e5e6; font-size: 18px; }
  h4 { margin: 4px 0; font-size: 13px; color: #555; }
  .decision-banner { display: inline-block; padding: 10px 18px; border-radius: 6px; font-weight: 700; font-size: 16px; margin: 10px 0 18px 0; }
  .decision-banner.ready    { background: #d4edda; color: #155724; }
  .decision-banner.warnings { background: #fff3cd; color: #856404; }
  .decision-banner.fix      { background: #f8d7da; color: #721c24; }
  .meta { color: #666; font-size: 13px; }
  table.findings, table.visual-grid { border-collapse: collapse; width: 100%; margin: 12px 0; }
  table.findings th, table.findings td { border: 1px solid #e5e5e6; padding: 8px 10px; vertical-align: top; text-align: left; font-size: 13px; }
  table.findings th { background: #f5f5f5; }
  .sev { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 700; text-transform: uppercase; }
  .sev-critical { background: #f8d7da; color: #721c24; }
  .sev-major    { background: #fff3cd; color: #856404; }
  .sev-minor    { background: #e2e3e5; color: #383d41; }
  table.visual-grid td { vertical-align: top; padding: 6px; width: 33%; }
  table.visual-grid img { max-width: 100%; height: auto; border: 1px solid #e5e5e6; }
  .pipeline-issues { background: #fff3cd; padding: 12px 18px; border-left: 4px solid #f0ad4e; margin: 16px 0; }
`;

// ────────────────────── Render report HTML ──────────────────────
const pipelineFailures = [
  ...(preFlight?.failures || []),
  ...(visualValidation?.failures || []),
];

const failuresSection = pipelineFailures.length === 0 ? "" : `
  <div class="pipeline-issues">
    <h2>⚠️ Pipeline issues — ${pipelineFailures.length} stage(s) failed</h2>
    <p>Findings below are from the stages that succeeded. Retry the failed stages before treating this run as authoritative.</p>
    <table class="findings"><thead><tr><th>Stage</th><th>Error</th></tr></thead><tbody>
    ${pipelineFailures.map((f) => `<tr><td><strong>${escapeHtml(f.stage || f.name || "?")}</strong></td><td><code style="font-size:11px;white-space:pre-wrap;word-break:break-word;">${escapeHtml((f.stderr || f.error || "(no message)").slice(0, 500))}</code></td></tr>`).join("")}
    </tbody></table>
  </div>`;

const desktopMismatch = diffStats.desktop?.mismatch_pct ?? null;
const mobileMismatch  = diffStats.mobile?.mismatch_pct  ?? null;

const reportBody = `
  <h1>QA Report — ${escapeHtml(meta.program || slug)}</h1>
  <p class="meta">
    ${meta.client ? `<strong>${escapeHtml(meta.client)}</strong> · ` : ""}
    ${meta.w_code ? `${escapeHtml(meta.w_code)} · ` : ""}
    ${meta.source?.jira_ticket ? `Jira ${escapeHtml(meta.source.jira_ticket)} · ` : ""}
    Generated ${new Date().toISOString().slice(0, 19).replace("T", " ")}
  </p>
  <p class="decision-banner ${decisionClass}">Decision: ${decision}${pipelineFailures.length > 0 ? " · ⚠️ partial run" : ""}</p>
  <p>
    <strong>${counts.critical}</strong> critical · <strong>${counts.major}</strong> major · <strong>${counts.minor}</strong> minor &nbsp;|&nbsp;
    Pixel mismatch: desktop ${desktopMismatch ?? "—"}% · mobile ${mobileMismatch ?? "—"}%
  </p>
  ${visionReview.pairs_reviewed != null ? `<p class="meta">Vision review: ${visionReview.pairs_reviewed} section pairs · cost \$${(visionReview.total_cost_usd ?? 0).toFixed(2)}</p>` : ""}
  ${failuresSection}

  <h2>Full-page visual comparison</h2>
  <table class="visual-grid"><tr>
    <td><h4>Rendered desktop</h4><img src="rendered-desktop.png" loading="lazy"></td>
    <td><h4>Customer PDF (desktop)</h4><img src="../extracted/pdf-desktop.png" loading="lazy"></td>
    <td><h4>Diff</h4><img src="diff-desktop.png" loading="lazy"></td>
  </tr><tr>
    <td><h4>Rendered mobile</h4><img src="rendered-mobile.png" loading="lazy"></td>
    <td><h4>Customer PDF (mobile)</h4><img src="../extracted/pdf-mobile.png" loading="lazy"></td>
    <td><h4>Diff</h4><img src="diff-mobile.png" loading="lazy"></td>
  </tr></table>

  <h2>Findings — Vision review (${visionReview.pairs_reviewed ?? "?"} section pairs)</h2>
  ${visionReview.findings.length === 0 ? "<p>No findings.</p>" : `<table class="findings"><thead><tr><th>Severity</th><th>Viewport · Section</th><th>Category</th><th>Description + suggested fix</th></tr></thead><tbody>${visionReview.findings.map(visionFindingRow).join("")}</tbody></table>`}

  <h2>Findings — Style audit</h2>
  ${styleAudit.findings.length === 0 ? "<p>No findings.</p>" : `<table class="findings"><thead><tr><th>Severity</th><th>Category</th><th>Message</th></tr></thead><tbody>${styleAudit.findings.map(findingsRow).join("")}</tbody></table>`}

  <h2>Findings — Compat check (${compatCheck.rules_evaluated ?? "?"} rules)</h2>
  ${compatCheck.findings.length === 0 ? "<p>No findings.</p>" : `<table class="findings"><thead><tr><th>Severity</th><th>Category</th><th>Message</th></tr></thead><tbody>${compatCheck.findings.map(findingsRow).join("")}</tbody></table>`}

  <h2>Findings — Link QA (${linkCheck.links_found ?? "?"} links analysed)</h2>
  ${linkCheck.findings.length === 0 ? "<p>No findings.</p>" : `<table class="findings"><thead><tr><th>Severity</th><th>Category</th><th>Message</th></tr></thead><tbody>${linkCheck.findings.map(findingsRow).join("")}</tbody></table>`}

  <p class="meta">Auto-generated from JSON artifacts.</p>
`;

const fullHtml = `<!doctype html>
<html><head>
  <meta charset="utf-8">
  <title>QA Report — ${escapeHtml(slug)}</title>
  <style>${EMBEDDED_CSS}</style>
</head><body class="report">${reportBody}</body></html>`;

writeFileSync(join(qaDir, "report.html"), fullHtml);

// ────────────────────── Emit summary JSON for orchestrator ──────────────────────
const summary = {
  slug,
  decision,
  counts,
  pixel_diff: {
    desktop_mismatch_pct: desktopMismatch,
    mobile_mismatch_pct: mobileMismatch,
  },
  vision: {
    sections_reviewed: visionReview.pairs_reviewed ?? null,
    cost_usd: visionReview.total_cost_usd ?? null,
  },
  pipeline_failures: pipelineFailures.length,
  report_path: join(qaDir, "report.html"),
  campaign_meta: {
    client: meta.client,
    w_code: meta.w_code,
    jira_ticket: meta.source?.jira_ticket || null,
    program: meta.program,
  },
};

console.log(JSON.stringify(summary, null, 2));
