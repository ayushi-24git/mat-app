#!/usr/bin/env node
// scripts/pre-flight.mjs
// Composite pre-flight stage — runs style-audit, compat-check, link-check in parallel
// and emits a unified qa/pre-flight.json with aggregated findings + applied suppressions.
//
// Usage:
//   node scripts/pre-flight.mjs <campaign-dir>

import { existsSync, readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";

const __filename = fileURLToPath(import.meta.url);
const SCRIPTS_DIR = dirname(__filename);

// ────────────────────── Suppression patterns ──────────────────────
// Findings matching any of these patterns are dropped from the aggregated output
// because they represent known false positives (e.g. ESP-side tokens we deliberately
// don't validate). See history: view_in_browser was removed per Ayushi's policy.
const ALWAYS_SUPPRESS = [
  /view_in_browser/i,
];

function isSuppressed(finding) {
  const text = [
    finding.description,
    finding.suggested_fix,
    finding.matched_text,
    finding.snippet,
    finding.rule_id,
    finding.name,
  ].filter(Boolean).join(" ");
  return ALWAYS_SUPPRESS.some((re) => re.test(text));
}

// ────────────────────── Sub-stage runner ──────────────────────
function runScript(name, scriptPath, args) {
  return new Promise((res) => {
    const proc = spawn("node", [scriptPath, ...args], { stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "", stderr = "";
    proc.stdout.on("data", (d) => stdout += d.toString());
    proc.stderr.on("data", (d) => stderr += d.toString());
    const t0 = Date.now();
    proc.on("close", (code) => {
      res({ name, code, ms: Date.now() - t0, stdout: stdout.trim(), stderr: stderr.trim() });
    });
    proc.on("error", (err) => {
      res({ name, code: -1, ms: Date.now() - t0, stdout: "", stderr: err.message });
    });
  });
}

// ────────────────────── Main ──────────────────────
async function main() {
  const campaignDir = process.argv[2];
  if (!campaignDir || !existsSync(campaignDir)) {
    console.error(`Usage: node scripts/pre-flight.mjs <campaign-dir>`);
    process.exit(2);
  }
  const qaDir = join(campaignDir, "qa");
  mkdirSync(qaDir, { recursive: true });

  const t0 = Date.now();
  console.log("[pre-flight] running style-audit + compat-check + link-check in parallel...");

  // Run all three in parallel
  const [styleResult, compatResult, linkResult] = await Promise.all([
    runScript("style-audit", join(SCRIPTS_DIR, "style-audit.mjs"), [campaignDir]),
    runScript("compat-check", join(SCRIPTS_DIR, "compat-check.mjs"), [campaignDir]),
    runScript("link-check",   join(SCRIPTS_DIR, "link-check.mjs"),   [campaignDir]),
  ]);

  const subStages = { "style-audit": styleResult, "compat-check": compatResult, "link-check": linkResult };
  const failures = [];
  for (const [name, r] of Object.entries(subStages)) {
    if (r.code !== 0) {
      failures.push({ stage: name, code: r.code, stderr: r.stderr || r.stdout.slice(-200) });
      console.error(`[pre-flight] ${name} FAILED (code ${r.code}): ${r.stderr || "(no stderr)"}`);
    } else {
      console.log(`[pre-flight] ${name} ok (${r.ms}ms)`);
    }
  }

  // Read JSON outputs (fail-soft if any are missing)
  const safeRead = (p) => {
    try { return JSON.parse(readFileSync(p, "utf8")); }
    catch { return null; }
  };
  const styleAudit  = safeRead(join(qaDir, "style-audit.json"))  ?? { findings: [] };
  const compatCheck = safeRead(join(qaDir, "compat-check.json")) ?? { findings: [] };
  const linkCheck   = safeRead(join(qaDir, "link-check.json"))   ?? { findings: [] };

  // Tag each finding with its source stage AND normalize the description field.
  // The three sub-scripts use different conventions:
  //   style-audit:  message
  //   compat-check: description
  //   link-check:   message
  // Downstream report-builders and the master skill expect a single field. We
  // populate both `description` and `message` from whichever was set so callers
  // reading either field continue to work.
  const normalize = (f, source) => {
    const text = f.description || f.message || f.name || "";
    return { ...f, source, description: text, message: text };
  };
  const tagged = [
    ...(styleAudit.findings  || []).map((f) => normalize(f, "style-audit")),
    ...(compatCheck.findings || []).map((f) => normalize(f, "compat-check")),
    ...(linkCheck.findings   || []).map((f) => normalize(f, "link-check")),
  ];

  // Apply suppressions
  const suppressed = tagged.filter(isSuppressed);
  const kept = tagged.filter((f) => !isSuppressed(f));

  // Aggregate counts
  const counts = {
    critical: kept.filter((f) => f.severity === "critical").length,
    major:    kept.filter((f) => f.severity === "major").length,
    minor:    kept.filter((f) => f.severity === "minor").length,
    total:    kept.length,
    suppressed: suppressed.length,
  };
  const bySource = {
    "style-audit":  kept.filter((f) => f.source === "style-audit").length,
    "compat-check": kept.filter((f) => f.source === "compat-check").length,
    "link-check":   kept.filter((f) => f.source === "link-check").length,
  };

  // Aggregated report
  const report = {
    generated_at: new Date().toISOString(),
    total_ms: Date.now() - t0,
    sub_stages: {
      "style-audit":  { code: styleResult.code,  ms: styleResult.ms },
      "compat-check": { code: compatResult.code, ms: compatResult.ms },
      "link-check":   { code: linkResult.code,   ms: linkResult.ms },
    },
    failures,
    counts,
    counts_by_source: bySource,
    findings: kept,
    suppressed_findings: suppressed.map((f) => ({
      source: f.source,
      severity: f.severity,
      description: f.description,
      matched_pattern: ALWAYS_SUPPRESS.find((re) => re.test(JSON.stringify(f)))?.source,
    })),
  };

  writeFileSync(join(qaDir, "pre-flight.json"), JSON.stringify(report, null, 2));

  // Single-line summary for the orchestrator
  console.log(
    `[pre-flight] DONE in ${report.total_ms}ms: ` +
    `${counts.critical} critical · ${counts.major} major · ${counts.minor} minor ` +
    `(suppressed ${counts.suppressed}; failures ${failures.length})`
  );

  // Exit code reflects sub-stage success, not finding counts
  process.exit(failures.length === 0 ? 0 : 1);
}

main().catch((err) => {
  console.error(`[pre-flight] FATAL: ${err.message}`);
  process.exit(2);
});
