// Stage 2 — Client rendering compat-check.
// Static analysis of output/email.html against rule packs for Outlook Classic,
// Gmail, and Outlook New. Emits qa/compat-check.json with classified findings.
//
// Usage: node scripts/compat-check.mjs <campaign-dir>

import { readFileSync, writeFileSync, mkdirSync, existsSync } from "node:fs";
import { resolve, join } from "node:path";

import outlookClassic from "./compat-rules/outlook-classic.mjs";
import gmail from "./compat-rules/gmail.mjs";
import outlookNew from "./compat-rules/outlook-new.mjs";

const allRules = [...outlookClassic, ...gmail, ...outlookNew];

const campaignDir = resolve(process.argv[2] ?? ".");
const htmlPath = join(campaignDir, "output", "email.html");
if (!existsSync(htmlPath)) {
  console.error(`output/email.html not found at ${htmlPath}`);
  process.exit(1);
}
const html = readFileSync(htmlPath, "utf8");
const sizeKb = Buffer.byteLength(html, "utf8") / 1024;
const qaDir = join(campaignDir, "qa");
mkdirSync(qaDir, { recursive: true });

const findings = [];
for (const rule of allRules) {
  const matches = applyRule(rule, html, { sizeKb });
  if (matches !== null && matches.length > 0) {
    findings.push({
      rule_id: rule.id,
      client: rule.client,
      severity: rule.severity,
      name: rule.name,
      description: rule.description,
      suggested_fix: rule.suggested_fix,
      doc_url: rule.doc_url ?? null,
      total_matches: matches.length,
      sample_matches: matches.slice(0, 5).map((m) => truncate(typeof m === "string" ? m : (m[0] ?? String(m)), 200)),
    });
  }
}

// Group findings by client for the report
const byClient = {};
for (const f of findings) {
  byClient[f.client] ??= { critical: 0, major: 0, minor: 0, findings: [] };
  byClient[f.client][f.severity]++;
  byClient[f.client].findings.push(f);
}

const report = {
  campaign: campaignDir.split("/").pop(),
  generated_at: new Date().toISOString(),
  html_size_kb: parseFloat(sizeKb.toFixed(2)),
  rules_evaluated: allRules.length,
  totals: {
    critical: findings.filter((f) => f.severity === "critical").length,
    major: findings.filter((f) => f.severity === "major").length,
    minor: findings.filter((f) => f.severity === "minor").length,
  },
  by_client: byClient,
  findings,
};

writeFileSync(join(qaDir, "compat-check.json"), JSON.stringify(report, null, 2));
console.log(`Compat-check: ${findings.length} findings across ${Object.keys(byClient).length} clients (${report.totals.critical} critical, ${report.totals.major} major, ${report.totals.minor} minor) — rules evaluated: ${allRules.length}`);
console.log(`Written to ${join(qaDir, "compat-check.json")}`);

// ─────────────────────── helpers ───────────────────────
function applyRule(rule, html, context) {
  const d = rule.detect;
  if (!d) return [];
  if (d.type === "regex") {
    return [...html.matchAll(d.pattern)].map((m) => m[0]);
  }
  if (d.type === "regex_absent") {
    return html.match(d.pattern) ? [] : [`Required pattern not found: ${d.pattern.source ?? d.pattern}`];
  }
  if (d.type === "size") {
    const v = context.sizeKb;
    if (d.comparator === ">" && v > d.limit_kb) return [`HTML is ${v.toFixed(1)} KB (limit ${d.limit_kb} KB)`];
    return [];
  }
  if (d.type === "custom") {
    try { return d.fn(html, context) ?? []; } catch (e) { return [`(rule errored: ${e.message})`]; }
  }
  return [];
}

function truncate(s, n) {
  s = s.replace(/\s+/g, " ").trim();
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}
