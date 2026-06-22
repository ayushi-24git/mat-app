#!/usr/bin/env node
// scripts/visual-validation.mjs
// Composite visual-validation stage — orchestrates the full visual QA pipeline:
//
//   Stage A (parallel):  pdf-to-png  +  render
//   Stage B (parallel):  diff  +  crop-section[desktop]  +  crop-section[mobile]
//   Stage C (serial):    vision-review (Claude CLI ×N parallel internally)
//
// Emits qa/visual-validation.json with aggregated findings + diff stats.
//
// Usage:
//   node scripts/visual-validation.mjs <campaign-dir> [--skip-vision] [--vision-concurrency=12]

import { existsSync, readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";

const __filename = fileURLToPath(import.meta.url);
const SCRIPTS_DIR = dirname(__filename);

// ────────────────────── CLI args ──────────────────────
const args = process.argv.slice(2);
const campaignDir = args.find((a) => !a.startsWith("--"));
const skipVision = args.includes("--skip-vision");
const visionConcurrencyArg = args.find((a) => a.startsWith("--vision-concurrency="));
const visionConcurrency = visionConcurrencyArg
  ? visionConcurrencyArg.split("=")[1]
  : "12";

if (!campaignDir || !existsSync(campaignDir)) {
  console.error("Usage: node scripts/visual-validation.mjs <campaign-dir> [--skip-vision] [--vision-concurrency=N]");
  process.exit(2);
}

// ────────────────────── Sub-stage runner ──────────────────────
function runScript(name, scriptPath, args, env = {}) {
  return new Promise((res) => {
    const proc = spawn("node", [scriptPath, ...args], {
      stdio: ["ignore", "pipe", "pipe"],
      env: { ...process.env, ...env },
    });
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

// ────────────────────── Stage helpers ──────────────────────
async function stageParallel(label, tasks) {
  console.log(`[visual] ${label} — running ${tasks.length} tasks in parallel...`);
  const results = await Promise.all(tasks);
  for (const r of results) {
    if (r.code === 0) {
      console.log(`[visual]   ✓ ${r.name} (${r.ms}ms)`);
    } else {
      console.error(`[visual]   ✗ ${r.name} (code ${r.code}, ${r.ms}ms): ${(r.stderr || r.stdout).slice(-200)}`);
    }
  }
  return results;
}

// ────────────────────── Main ──────────────────────
async function main() {
  const qaDir = join(campaignDir, "qa");
  mkdirSync(qaDir, { recursive: true });

  const t0 = Date.now();
  const allResults = [];
  const failures = [];

  // ── STAGE A: pdf-to-png + render (parallel — neither depends on the other) ──
  const stageA = await stageParallel("STAGE A: pdf-to-png + render", [
    runScript("pdf-to-png", join(SCRIPTS_DIR, "pdf-to-png.mjs"), [campaignDir]),
    runScript("render",     join(SCRIPTS_DIR, "render.mjs"),     [campaignDir]),
  ]);
  allResults.push(...stageA);
  for (const r of stageA) if (r.code !== 0) failures.push({ stage: r.name, code: r.code, stderr: r.stderr.slice(-200) });

  // Hard gate: if pdf-to-png or render failed, downstream stages cannot run.
  // Emit a degraded report and exit non-zero.
  const pdfOk = stageA.find((r) => r.name === "pdf-to-png")?.code === 0;
  const renderOk = stageA.find((r) => r.name === "render")?.code === 0;
  if (!pdfOk || !renderOk) {
    const report = {
      generated_at: new Date().toISOString(),
      total_ms: Date.now() - t0,
      degraded: true,
      reason: `STAGE A failed — pdf-to-png:${pdfOk ? "ok" : "FAILED"} render:${renderOk ? "ok" : "FAILED"}`,
      sub_stages: Object.fromEntries(allResults.map((r) => [r.name, { code: r.code, ms: r.ms }])),
      failures,
      findings: [],
      counts: { critical: 0, major: 0, minor: 0, total: 0 },
    };
    writeFileSync(join(qaDir, "visual-validation.json"), JSON.stringify(report, null, 2));
    console.error(`[visual] DEGRADED: cannot continue past STAGE A`);
    process.exit(1);
  }

  // ── STAGE B: diff + crop-section[desktop] + crop-section[mobile] (parallel) ──
  const stageB = await stageParallel("STAGE B: diff + crop[desktop] + crop[mobile]", [
    runScript("diff",            join(SCRIPTS_DIR, "diff.mjs"),         [campaignDir]),
    runScript("crop[desktop]",   join(SCRIPTS_DIR, "crop-section.mjs"), [campaignDir, "desktop", "--all-sections"]),
    runScript("crop[mobile]",    join(SCRIPTS_DIR, "crop-section.mjs"), [campaignDir, "mobile",  "--all-sections"]),
  ]);
  allResults.push(...stageB);
  for (const r of stageB) if (r.code !== 0) failures.push({ stage: r.name, code: r.code, stderr: r.stderr.slice(-200) });

  // ── STAGE C: vision-review (heavy — Claude CLI ×N parallel inside) ──
  let visionResult = null;
  if (skipVision) {
    console.log(`[visual] STAGE C: vision-review SKIPPED (--skip-vision flag set)`);
  } else {
    visionResult = await runScript(
      "vision-review",
      join(SCRIPTS_DIR, "vision-review-cli.mjs"),
      [campaignDir],
      { VISION_CONCURRENCY: visionConcurrency }
    );
    allResults.push(visionResult);
    if (visionResult.code === 0) {
      console.log(`[visual]   ✓ vision-review (${visionResult.ms}ms)`);
    } else {
      console.error(`[visual]   ✗ vision-review (code ${visionResult.code}, ${visionResult.ms}ms): ${(visionResult.stderr || visionResult.stdout).slice(-300)}`);
      failures.push({ stage: "vision-review", code: visionResult.code, stderr: visionResult.stderr.slice(-200) });
    }
  }

  // ────────────────────── Aggregate findings ──────────────────────
  const safeRead = (p) => {
    try { return JSON.parse(readFileSync(p, "utf8")); }
    catch { return null; }
  };
  const diffStats   = safeRead(join(qaDir, "diff-stats.json"))    ?? {};
  const visionData  = safeRead(join(qaDir, "vision-review.json")) ?? { findings: [] };

  // Vision findings already have description; just tag source for consistency with pre-flight.
  const visionFindings = (visionData.findings || []).map((f) => ({ ...f, source: "vision-review" }));

  const counts = {
    critical: visionFindings.filter((f) => f.severity === "critical").length,
    major:    visionFindings.filter((f) => f.severity === "major").length,
    minor:    visionFindings.filter((f) => f.severity === "minor").length,
    total:    visionFindings.length,
  };

  const report = {
    generated_at: new Date().toISOString(),
    total_ms: Date.now() - t0,
    degraded: false,
    skip_vision: skipVision,
    sub_stages: Object.fromEntries(allResults.map((r) => [r.name, { code: r.code, ms: r.ms }])),
    failures,
    pixel_diff: {
      desktop_mismatch_pct: diffStats.desktop?.mismatch_pct ?? null,
      mobile_mismatch_pct:  diffStats.mobile?.mismatch_pct ?? null,
    },
    vision: {
      sections_reviewed: visionData.pairs_reviewed ?? null,
      total_cost_usd:    visionData.total_cost_usd ?? null,
      mode:              visionData.mode ?? null,
    },
    counts,
    findings: visionFindings,
  };
  writeFileSync(join(qaDir, "visual-validation.json"), JSON.stringify(report, null, 2));

  // Summary
  const diff = report.pixel_diff;
  console.log(
    `[visual] DONE in ${report.total_ms}ms: ` +
    `${counts.critical} critical · ${counts.major} major · ${counts.minor} minor ` +
    `| pixel diff: desktop=${diff.desktop_mismatch_pct ?? "—"}% mobile=${diff.mobile_mismatch_pct ?? "—"}%`
  );

  process.exit(failures.length === 0 ? 0 : 1);
}

main().catch((err) => {
  console.error(`[visual] FATAL: ${err.message}`);
  process.exit(2);
});
