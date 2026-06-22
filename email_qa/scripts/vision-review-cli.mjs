// Per-section vision review via the local Claude Code CLI — parallel execution.
//
// For each section pair (rendered + PDF crop), spawns `claude --print` with a focused
// comparison prompt. Calls run in parallel (controlled concurrency) so 12 pairs complete
// in ~25-40 seconds instead of 5 minutes sequentially.
//
// Uses the user's Claude Code seat / Enterprise plan quota — no separate Anthropic API billing.
//
// Usage:  node scripts/vision-review-cli.mjs <campaign-dir>
// Output: qa/vision-review.json
//
// Env knobs:
//   VISION_CONCURRENCY=8   # max parallel claude processes (default 8)
//   VISION_MODEL=haiku     # model alias (haiku|sonnet|opus)

import { spawn } from "node:child_process";
import { existsSync, readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { resolve, join } from "node:path";

const campaignDir = resolve(process.argv[2] ?? ".");
const qaDir = join(campaignDir, "qa");
mkdirSync(qaDir, { recursive: true });

const CONCURRENCY = parseInt(process.env.VISION_CONCURRENCY ?? "8", 10);
const MODEL = process.env.VISION_MODEL ?? "haiku";

const tokens = (() => {
  try { return JSON.parse(readFileSync(join(campaignDir, "extracted", "design-tokens.json"), "utf8")); }
  catch { return { section_order: [] }; }
})();

const STRATEGIC = ["logos_header", "headline", "hero", "task_1_with_cta", "get_app_panel", "footer"];
const FALLBACK_PANELS = ["panel_1", "panel_2", "panel_3", "panel_4", "panel_5", "panel_6"];

// Pick the section list intelligently:
//   1) If design-tokens.json has section_order matching our STRATEGIC list, use those.
//   2) Otherwise, look for panel_N crops that crop-section.mjs may have emitted in fallback mode.
//   3) Last resort: try STRATEGIC names anyway (most will be missing — that's fine, the runner skips them).
function pickSections() {
  if (Array.isArray(tokens.section_order)) {
    const overlap = tokens.section_order.filter((s) => STRATEGIC.includes(s));
    if (overlap.length > 0) return { sections: overlap, fallback: false };
  }
  // Look for fallback crops on disk.
  const cropsDir = join(campaignDir, "qa", "crops");
  const desktopFallbackExists = FALLBACK_PANELS.some(
    (p) => existsSync(join(cropsDir, `desktop-${p}-rendered.png`))
  );
  if (desktopFallbackExists) return { sections: FALLBACK_PANELS, fallback: true };
  return { sections: STRATEGIC, fallback: false };
}
const { sections: sectionsToReview, fallback: usingFallback } = pickSections();

const BASE_RULES = `
- ONLY return JSON. No prose, no markdown commentary.
- SEVERITY thresholds:
  * critical = section collapsed (zero/near-zero height), broken/missing image, wrong CTA target, unresolved template directive (e.g. "{% else %}", "{% if %}", "{{var}}") rendering literally in body text, content duplicated due to unresolved conditionals
  * major = spacing/padding off by >5 px, wrong image variant, wrong shape (filled vs outlined, elongated vs square), color drift >ΔE 10, copy substantively differs, element clearly mis-positioned (edge vs centre)
  * minor = spacing/padding off by ≤5 px, color drift ≤ΔE 10, headline wrap differs, sub-pixel typography drift
- SKIP these (do NOT emit findings):
  * JPEG compression artifacts in the PDF reference
  * Sub-pixel font rendering differences
  * Color shift from PDF→sRGB rendering (~5 ΔE darker is normal)
  * Anti-aliasing on small text
  * A few pixels of crop alignment drift at section edges
  * The literal text "{{view_in_browser}}" anywhere in the rendered HTML — this is a known ESP token that Capillary's pipeline does not validate. Treat any "{{view_in_browser}}" occurrence as expected; do NOT flag it as an unresolved template directive or unresolved variable.
- Be conservative: only flag clearly visible side-by-side differences.
`.trim();

const FALLBACK_PANEL_GUIDANCE = `
- IMPORTANT — These crops are equal-height PANELS, NOT semantic sections. The rendered HTML and the PDF reference have DIFFERENT total heights, so the same panel index samples different vertical content. Content that appears in PDF "panel_N" may be present in the rendered HTML's panel_(N-1) or panel_(N+1) — that is NOT missing content, it's panel boundary drift.
- DO NOT emit "missing_content", "extra_content", or "wrong_image" findings based on whether a piece of text/element is "absent" from the other crop. The element is almost certainly elsewhere in the page.
- ONLY emit findings for issues VISIBLE INSIDE the visible portion of BOTH crops simultaneously: color drift on shared content, font weight changes on shared text, spacing/padding within shared elements, aspect-ratio drift of shared shapes, and unresolved template directives (e.g. literal "{% else %}" text in the rendered HTML).
- If the two crops show entirely unrelated content (different sections rendered at different y), emit NO findings — return an empty array.
`.trim();

const SYSTEM_PROMPT_NAMED = `
You are an email-QA vision reviewer. Compare a rendered HTML section crop against the customer-approved PDF reference crop for the SAME section, then emit findings as JSON.

Rules:
${BASE_RULES}
`.trim();

const SYSTEM_PROMPT_FALLBACK = `
You are an email-QA vision reviewer. Compare a rendered HTML panel crop against the customer-approved PDF reference panel crop, then emit findings as JSON.

Rules:
${BASE_RULES}

Additional rules for PANEL mode:
${FALLBACK_PANEL_GUIDANCE}
`.trim();

const SYSTEM_PROMPT = usingFallback ? SYSTEM_PROMPT_FALLBACK : SYSTEM_PROMPT_NAMED;

function buildPrompt(viewport, section, renderedPath, pdfPath) {
  return `
Use the Read tool to load both images, then compare them.

Rendered HTML crop (viewport=${viewport}, section=${section}): ${renderedPath}
PDF reference (same section): ${pdfPath}

Walk through the checklist (per the system prompt) and emit findings only for clear differences.

Return ONLY: {"findings": [{"viewport":"${viewport}","section":"${section}","severity":"...","category":"...","description":"...","suggested_fix":"..."}]}
If no findings, return {"findings": []}.
`.trim();
}

function extractJsonFromMarkdown(s) {
  const m = s.match(/\`\`\`(?:json)?\s*([\s\S]*?)\`\`\`/);
  return (m ? m[1] : s).trim();
}

function spawnClaude(prompt) {
  return new Promise((ok, fail) => {
    const args = [
      "--print",
      "--output-format", "json",
      "--no-session-persistence",
      "--model", MODEL,
      "--allowed-tools", "Read",
      "--system-prompt", SYSTEM_PROMPT,
      "--max-budget-usd", "0.20",
      prompt,
    ];
    const child = spawn("claude", args, { cwd: "/tmp", stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "", stderr = "";
    child.stdout.on("data", (d) => (stdout += d.toString()));
    child.stderr.on("data", (d) => (stderr += d.toString()));
    child.on("close", (code) => {
      try {
        const wrapper = JSON.parse(stdout);
        if (wrapper.is_error) {
          ok({ findings: [], _error: wrapper.subtype || `exit ${code}`, _cost: wrapper.total_cost_usd ?? 0 });
          return;
        }
        const raw = wrapper.result ?? "";
        const jsonStr = extractJsonFromMarkdown(raw);
        if (!jsonStr) {
          ok({ findings: [], _error: "empty result", _cost: wrapper.total_cost_usd ?? 0 });
          return;
        }
        const parsed = JSON.parse(jsonStr);
        ok({
          findings: parsed.findings ?? [],
          _cost: wrapper.total_cost_usd ?? 0,
          _duration_ms: wrapper.duration_ms,
        });
      } catch (err) {
        ok({ findings: [], _error: `parse: ${err.message}` });
      }
    });
    child.on("error", (err) => fail(err));
  });
}

async function reviewSection(viewport, section) {
  const renderedPath = join(qaDir, "crops", `${viewport}-${section}-rendered.png`);
  const pdfPath = join(qaDir, "crops", `${viewport}-${section}-pdf.png`);
  if (!existsSync(renderedPath) || !existsSync(pdfPath)) {
    return { viewport, section, findings: [], _error: "crops missing" };
  }
  const r = await spawnClaude(buildPrompt(viewport, section, renderedPath, pdfPath));
  return { viewport, section, ...r };
}

// Run tasks with a max-in-flight concurrency limit.
async function runWithConcurrency(tasks, limit) {
  const results = new Array(tasks.length);
  let next = 0;
  async function worker() {
    while (true) {
      const i = next++;
      if (i >= tasks.length) return;
      results[i] = await tasks[i]();
    }
  }
  const workers = Array.from({ length: Math.min(limit, tasks.length) }, () => worker());
  await Promise.all(workers);
  return results;
}

// ---- main ----
const pairs = [];
for (const viewport of ["desktop", "mobile"]) {
  for (const section of sectionsToReview) {
    pairs.push({ viewport, section });
  }
}

console.log(`Vision review (CLI, parallel ×${CONCURRENCY}, model=${MODEL}): ${pairs.length} section pairs`);
console.log(`Sections: ${sectionsToReview.join(", ")}`);
const startedAt = Date.now();

const tasks = pairs.map(({ viewport, section }) => () => reviewSection(viewport, section));
const results = await runWithConcurrency(tasks, CONCURRENCY);

// When running in fallback panel mode, certain finding categories are inherently
// unreliable because panels are equal-height slices, not semantic sections. The
// vision model may still emit them despite the prompt; we filter them out here
// as a safety net.
const FALLBACK_SUPPRESSED_CATEGORIES = new Set(["missing_content", "extra_content", "wrong_image"]);

// Findings about specific known-but-not-validated tokens are also suppressed regardless
// of mode. These are templating placeholders Capillary's ESP supports / ignores by design.
const ALWAYS_SUPPRESS_TOKEN_PATTERNS = [/view_in_browser/i];
function isSuppressedToken(finding) {
  const blob = `${finding?.description ?? ""}  ${finding?.suggested_fix ?? ""}  ${finding?.category ?? ""}`;
  return ALWAYS_SUPPRESS_TOKEN_PATTERNS.some((rx) => rx.test(blob));
}

const allFindings = [];
const perSection = [];
let totalCost = 0;
let suppressedCount = 0;
let tokenSuppressedCount = 0;
for (const r of results) {
  const cost = r._cost ?? 0;
  totalCost += cost;
  if (r._error) {
    perSection.push({ viewport: r.viewport, section: r.section, error: r._error, findings: 0, cost_usd: cost });
    console.log(`  ${r.viewport}/${r.section}: SKIP (${r._error.slice(0, 60)})`);
  } else {
    let kept = r.findings;
    let suppressedHere = 0;
    let tokenSuppressedHere = 0;
    // First pass: drop always-suppressed token-related findings (view_in_browser, etc.)
    kept = kept.filter((f) => {
      if (isSuppressedToken(f)) { tokenSuppressedHere++; return false; }
      return true;
    });
    tokenSuppressedCount += tokenSuppressedHere;
    // Second pass: drop fallback-mode-only categories
    if (usingFallback) {
      kept = kept.filter((f) => {
        const drop = FALLBACK_SUPPRESSED_CATEGORIES.has(f.category);
        if (drop) suppressedHere++;
        return !drop;
      });
      suppressedCount += suppressedHere;
    }
    perSection.push({
      viewport: r.viewport,
      section: r.section,
      findings: kept.length,
      suppressed_panel_mode: suppressedHere || undefined,
      suppressed_known_tokens: tokenSuppressedHere || undefined,
      cost_usd: cost,
      duration_ms: r._duration_ms,
    });
    const notes = [];
    if (suppressedHere) notes.push(`${suppressedHere} panel-mode`);
    if (tokenSuppressedHere) notes.push(`${tokenSuppressedHere} known-token`);
    const noteSuppressed = notes.length ? ` · suppressed: ${notes.join(", ")}` : "";
    console.log(`  ${r.viewport}/${r.section}: ${kept.length} finding(s)${noteSuppressed} · $${cost.toFixed(4)} · ${r._duration_ms ?? 0} ms`);
    for (const f of kept) {
      if (!f.viewport) f.viewport = r.viewport;
      if (!f.section)  f.section  = r.section;
      allFindings.push(f);
    }
  }
}

// When fallback panels detected ≥2 "missing_content"-class findings (now suppressed),
// surface a single Minor as a signal that the HTML and PDF have different vertical
// proportions. Actionable hint: provide design-tokens.json for precise per-section review.
if (usingFallback && suppressedCount >= 2) {
  allFindings.push({
    viewport: "both",
    section: "global",
    severity: "minor",
    category: "panel_boundary_drift",
    description: `Vision review suppressed ${suppressedCount} panel-mode "missing content" finding(s). This indicates the rendered HTML and the PDF reference have different overall vertical proportions — content lands at different y-positions between the two. Not a defect per se, but a signal worth investigating.`,
    suggested_fix: "Provide a design-tokens.json with section_bounds for this campaign so per-section vision review can crop semantically aligned regions instead of equal-height panels.",
  });
}

const totals = {
  critical: allFindings.filter((f) => f.severity === "critical").length,
  major: allFindings.filter((f) => f.severity === "major").length,
  minor: allFindings.filter((f) => f.severity === "minor").length,
};

const out = {
  campaign: campaignDir.split("/").pop(),
  generated_at: new Date().toISOString(),
  method: usingFallback ? "claude_code_cli_panel_fallback_parallel" : "claude_code_cli_per_section_parallel",
  model: MODEL,
  concurrency: CONCURRENCY,
  using_fallback_panels: usingFallback,
  sections_reviewed: sectionsToReview,
  pairs_reviewed: perSection.length,
  suppressed_fallback_findings: suppressedCount,
  suppressed_known_token_findings: tokenSuppressedCount,
  duration_ms: Date.now() - startedAt,
  total_cost_usd: parseFloat(totalCost.toFixed(4)),
  totals,
  per_section: perSection,
  findings: allFindings,
};

writeFileSync(join(qaDir, "vision-review.json"), JSON.stringify(out, null, 2));
console.log(`\nVision review complete: ${allFindings.length} findings (${totals.critical}c/${totals.major}M/${totals.minor}m)`);
console.log(`Total cost: $${totalCost.toFixed(4)} · Wall time: ${((Date.now() - startedAt) / 1000).toFixed(1)}s`);
console.log(`Written to ${join(qaDir, "vision-review.json")}`);
