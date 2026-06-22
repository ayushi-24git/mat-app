#!/usr/bin/env node
// scripts/ingest.mjs
// Assemble a canonical campaigns/<slug>/ structure from:
//   - an uploaded HTML file
//   - a staged Drive folder (rclone-copied beforehand by the orchestrator)
//   - optional Jira ticket context (fetched by the orchestrator via MCP)
//
// Modes:
//   node scripts/ingest.mjs --parse-w-code <html-path>
//     → prints { w_code, source } to stdout
//
//   node scripts/ingest.mjs --classify
//     --staging-dir <path>     where rclone dumped Drive files
//     --html-path <path>       uploaded HTML
//     --slug <slug>            target campaign slug
//     [--w-code <code>]        detected/manual W-code
//     [--jira-context <path>]  JSON file with Jira fields (summary, client, etc.)
//     → lays down campaigns/<slug>/, prints { campaign_dir, classification, meta_path }

import {
  existsSync, readFileSync, writeFileSync, mkdirSync,
  renameSync, rmSync, readdirSync, statSync,
} from "node:fs";
import { join, basename, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

// ────────────────────── Constants ──────────────────────
const __filename = fileURLToPath(import.meta.url);
const PIPELINE_ROOT = resolve(dirname(__filename), "..");
const CAMPAIGNS_DIR = process.env.CAMPAIGNS_DIR || join(PIPELINE_ROOT, "campaigns");
const W_CODE_REGEX = /WF\d{7,9}/;

// ────────────────────── CLI parsing ──────────────────────
const args = process.argv.slice(2);
function arg(name) {
  const i = args.indexOf(name);
  return i === -1 ? null : args[i + 1];
}
function flag(name) {
  return args.includes(name);
}

// ────────────────────── Utility: parse W-code ──────────────────────
export function parseWCode(htmlPath) {
  if (!htmlPath || !existsSync(htmlPath)) {
    return { w_code: null, source: null };
  }
  const filename = basename(htmlPath);
  const fromFilename = filename.match(W_CODE_REGEX);
  if (fromFilename) return { w_code: fromFilename[0], source: "filename" };

  const content = readFileSync(htmlPath, "utf8");
  const fromContent = content.match(W_CODE_REGEX);
  if (fromContent) return { w_code: fromContent[0], source: "body" };

  return { w_code: null, source: null };
}

// ────────────────────── File classification ──────────────────────
// Returns { kind, target } per filename or null if it should be discarded.
// Order matters — first match wins.
function classifyFile(filename) {
  const n = filename.toLowerCase();
  const ext = n.slice(n.lastIndexOf("."));

  // PDFs — explicit desktop-/mobile- prefix takes priority
  if (n.startsWith("desktop-") && ext === ".pdf") {
    return { kind: "pdf_desktop", target: "input/desktop.pdf" };
  }
  if (n.startsWith("mobile-") && ext === ".pdf") {
    return { kind: "pdf_mobile", target: "input/mobile.pdf" };
  }
  // BRD-style "_Details" PDF
  if (ext === ".pdf" && /_details/i.test(n)) {
    return { kind: "brd", target: "input/brd.pdf" };
  }
  // Fallback: any other PDF → treat as combined reference (page 1 desktop, page 2 mobile)
  if (ext === ".pdf") {
    return { kind: "pdf_combined", target: "input/reference.pdf" };
  }

  // Figma source
  if (ext === ".fig") {
    return { kind: "figma", target: `input/${filename}` };
  }

  // HTML — only if it ends up in the staging from Drive (uploaded HTML is handled separately)
  if (ext === ".html" || ext === ".htm") {
    return { kind: "html_from_drive", target: "output/email.html" };
  }

  // Link mapping
  if (filename === "links.json" || filename === "link-mapping.json") {
    return { kind: "link_mapping", target: "input/link-mapping.json" };
  }

  // Images — classify by filename pattern
  if (/\.(png|jpg|jpeg|gif|webp)$/i.test(filename)) {
    if (/logo/i.test(filename)) {
      return { kind: "logo", target: `input/assets/${filename}` };
    }
    if (/^hero/i.test(filename)) {
      return { kind: "hero", target: `input/assets/${filename}` };
    }
    if (/^icon[_-]/i.test(filename)) {
      return { kind: "icon", target: `input/assets/${filename}` };
    }
    if (/^cta[_-]/i.test(filename)) {
      return { kind: "cta", target: `input/assets/${filename}` };
    }
    if (/^gettyimages/i.test(filename)) {
      return { kind: "photo", target: `input/assets/${filename}` };
    }
    return { kind: "image", target: `input/assets/${filename}` };
  }

  // Anything else (txt, etc.) — preserve under input/
  if (ext) return { kind: "other", target: `input/${filename}` };

  return null;
}

// ────────────────────── Slug derivation ──────────────────────
function deriveSlug({ wCode, jiraContext }) {
  const today = new Date().toISOString().slice(0, 10);
  const client = (jiraContext?.client || "campaign")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
  const codeFragment = wCode || "no-wcode";
  return `${today}-${codeFragment}-${client}`;
}

// ────────────────────── Meta synthesis ──────────────────────
function synthesizeMeta({ slug, wCode, jiraContext, classification }) {
  return {
    slug,
    w_code: wCode,
    client: jiraContext?.client || null,
    program: jiraContext?.summary || null,
    audience: jiraContext?.audience || "",
    source: {
      type: "ingest_v2",
      ingested_at: new Date().toISOString(),
      jira_ticket: jiraContext?.key || null,
      jira_status: jiraContext?.status || null,
      drive_folder_name: jiraContext?.drive_folder_name || null,
    },
    assignee: jiraContext?.assignee || null,
    reporter: jiraContext?.reporter || null,
    duedate: jiraContext?.duedate || null,
    classification: {
      pdf_desktop: classification.pdf_desktop || null,
      pdf_mobile: classification.pdf_mobile || null,
      pdf_combined: classification.pdf_combined || null,
      brd: classification.brd || null,
      figma: classification.figma || null,
      link_mapping: classification.link_mapping || null,
      assets_count: classification.assets_count || 0,
    },
  };
}

// ────────────────────── Main: classify-and-layout ──────────────────────
function runClassify() {
  const stagingDir = arg("--staging-dir");
  const htmlPath = arg("--html-path");
  const explicitSlug = arg("--slug");
  let wCode = arg("--w-code");
  const jiraContextPath = arg("--jira-context");

  if (!stagingDir || !existsSync(stagingDir)) {
    throw new Error(`Missing or invalid --staging-dir: ${stagingDir}`);
  }
  if (!htmlPath || !existsSync(htmlPath)) {
    throw new Error(`Missing or invalid --html-path: ${htmlPath}`);
  }

  // Try W-code parse if not explicitly provided
  if (!wCode) {
    const parsed = parseWCode(htmlPath);
    wCode = parsed.w_code;
  }

  // Load Jira context if provided
  let jiraContext = null;
  if (jiraContextPath && existsSync(jiraContextPath)) {
    try { jiraContext = JSON.parse(readFileSync(jiraContextPath, "utf8")); }
    catch (e) { console.error(`[ingest] failed to parse jira context: ${e.message}`); }
  }

  // Derive slug
  const slug = explicitSlug || deriveSlug({ wCode, jiraContext });
  const campaignDir = join(CAMPAIGNS_DIR, slug);

  // Fresh layout
  rmSync(campaignDir, { recursive: true, force: true });
  mkdirSync(join(campaignDir, "input", "assets"), { recursive: true });
  mkdirSync(join(campaignDir, "output"), { recursive: true });
  mkdirSync(join(campaignDir, "extracted"), { recursive: true });
  mkdirSync(join(campaignDir, "qa"), { recursive: true });

  // Place the uploaded HTML
  const htmlTarget = join(campaignDir, "output", "email.html");
  writeFileSync(htmlTarget, readFileSync(htmlPath));

  // Classify staged files
  const classification = {};
  const staged = readdirSync(stagingDir);
  for (const file of staged) {
    const src = join(stagingDir, file);
    if (statSync(src).isDirectory()) continue;
    const c = classifyFile(file);
    if (!c) continue;
    const dest = join(campaignDir, c.target);
    mkdirSync(dirname(dest), { recursive: true });
    renameSync(src, dest);

    // Record in classification report
    if (c.kind.startsWith("pdf") || ["figma", "brd", "link_mapping"].includes(c.kind)) {
      classification[c.kind] = c.target;
    } else if (["logo", "hero", "icon", "cta", "photo", "image"].includes(c.kind)) {
      classification.assets_count = (classification.assets_count || 0) + 1;
    }
  }

  // PDF reconciliation: if we got desktop + mobile, ALSO write a unified reference.pdf
  // pointing at desktop (pdf-to-png handles both shapes via dedicated paths).
  // If only combined, the existing pipeline treats it as page1=desktop / page2=mobile.

  // Write meta
  const meta = synthesizeMeta({ slug, wCode, jiraContext, classification });
  const metaPath = join(campaignDir, "input", "campaign-meta.json");
  writeFileSync(metaPath, JSON.stringify(meta, null, 2));

  // Emit result
  const result = {
    campaign_dir: campaignDir,
    slug,
    w_code: wCode,
    classification,
    meta_path: metaPath,
    html_path: htmlTarget,
  };
  console.log(JSON.stringify(result, null, 2));
}

// ────────────────────── Entry ──────────────────────
if (arg("--parse-w-code")) {
  const result = parseWCode(arg("--parse-w-code"));
  console.log(JSON.stringify(result));
  process.exit(result.w_code ? 0 : 1);
} else if (flag("--classify")) {
  try {
    runClassify();
  } catch (err) {
    console.error(`[ingest] FAILED: ${err.message}`);
    process.exit(1);
  }
} else {
  console.error("Usage:");
  console.error("  node scripts/ingest.mjs --parse-w-code <html-path>");
  console.error("  node scripts/ingest.mjs --classify --staging-dir <dir> --html-path <file> [--slug <s>] [--w-code <c>] [--jira-context <path>]");
  process.exit(2);
}
