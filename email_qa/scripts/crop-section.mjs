// Crop a named section out of the rendered HTML screenshot and the PDF reference,
// producing one PNG per source. Used by /email-qa for per-finding visuals and per-section vision review.
//
// Section bounds are read from <campaign>/extracted/design-tokens.json/section_bounds,
// expressed in Figma design-space y/h and scaled proportionally to each PNG's actual height.
//
// Usage:
//   node scripts/crop-section.mjs <campaign-dir> <viewport> <section> [end-section] [--out-name <basename>]
//   node scripts/crop-section.mjs <campaign-dir> <viewport> --all-sections
//
//   <viewport>      desktop | mobile
//   <section>       a key in design-tokens.json/section_bounds/<viewport>
//   <end-section>   optional — crop from start-section's top through end-section's bottom (inclusive)
//   --all-sections  produce a crop pair for every section in section_order

import sharp from "sharp";
import { mkdirSync, existsSync, readFileSync } from "node:fs";
import { resolve, join } from "node:path";

// ---- argument parsing -----------------------------------------------------
const args = process.argv.slice(2);
const positional = [];
let outName = null;
let allSections = false;
for (let i = 0; i < args.length; i++) {
  if (args[i] === "--out-name") { outName = args[++i]; continue; }
  if (args[i] === "--all-sections") { allSections = true; continue; }
  positional.push(args[i]);
}
const [campaignDirArg, viewport, sectionStart, sectionEnd] = positional;
if (!campaignDirArg || !viewport) {
  console.error("Usage: node scripts/crop-section.mjs <campaign-dir> <viewport> <section> [end-section] [--out-name X]");
  console.error("   or: node scripts/crop-section.mjs <campaign-dir> <viewport> --all-sections");
  process.exit(1);
}
const campaignDir = resolve(campaignDirArg);

// ---- load section_bounds from design-tokens.json, OR fall back to a generic
//      equal-height panel grid when no design tokens exist for this campaign.
//      The fallback lets brand-new Drive imports still produce usable crops.
const tokensPath = join(campaignDir, "extracted", "design-tokens.json");
let tokens = {};
if (existsSync(tokensPath)) {
  try { tokens = JSON.parse(readFileSync(tokensPath, "utf8")); } catch {}
}

const FALLBACK_PANEL_COUNT = 6;
function buildFallbackBounds(viewport) {
  // Equal-height slicing. Frame heights here are typical for our email format.
  const frame_height = viewport === "desktop" ? 2726 : 3397;
  const panelH = Math.floor(frame_height / FALLBACK_PANEL_COUNT);
  const b = { frame_height };
  for (let i = 0; i < FALLBACK_PANEL_COUNT; i++) {
    b[`panel_${i + 1}`] = { y: i * panelH, h: panelH };
  }
  return b;
}

let bounds = tokens.section_bounds?.[viewport];
let usingFallback = false;
if (!bounds) {
  bounds = buildFallbackBounds(viewport);
  usingFallback = true;
  console.warn(`No section_bounds.${viewport} in design-tokens — using equal-height panel fallback (${FALLBACK_PANEL_COUNT} panels).`);
}
const frameHeight = bounds.frame_height;

const renderedPath = join(campaignDir, "qa", `rendered-${viewport}.png`);
const pdfPath = join(campaignDir, "extracted", `pdf-${viewport}.png`);
if (!existsSync(renderedPath) || !existsSync(pdfPath)) {
  console.error(`Missing input: ${!existsSync(renderedPath) ? renderedPath : pdfPath}`);
  process.exit(1);
}

const cropsDir = join(campaignDir, "qa", "crops");
mkdirSync(cropsDir, { recursive: true });

async function cropOne(srcPath, label, figmaY, figmaH, basename) {
  const meta = await sharp(srcPath).metadata();
  const top = Math.max(0, Math.floor((figmaY / frameHeight) * meta.height));
  const height = Math.min(meta.height - top, Math.ceil((figmaH / frameHeight) * meta.height));
  const outPath = join(cropsDir, `${basename}-${label}.png`);
  await sharp(srcPath)
    .extract({ left: 0, top, width: meta.width, height })
    .resize({ width: 600, withoutEnlargement: true })
    .toFile(outPath);
  return { outPath, top, height };
}

async function cropSection(name, customBasename = null) {
  const sec = bounds[name];
  if (!sec || typeof sec.y !== "number") {
    console.warn(`Skipping unknown section: ${name}`);
    return;
  }
  const basename = customBasename ?? `${viewport}-${name}`;
  const r = await cropOne(renderedPath, "rendered", sec.y, sec.h, basename);
  const p = await cropOne(pdfPath, "pdf", sec.y, sec.h, basename);
  return { basename, rendered: r, pdf: p };
}

async function cropRange(startName, endName, customBasename) {
  const start = bounds[startName];
  const end = bounds[endName];
  if (!start || !end) {
    console.error(`Unknown section in range: ${startName} → ${endName}`);
    process.exit(1);
  }
  const y = start.y;
  const h = (end.y + end.h) - start.y;
  const basename = customBasename ?? `${viewport}-${startName}-${endName}`;
  const r = await cropOne(renderedPath, "rendered", y, h, basename);
  const p = await cropOne(pdfPath, "pdf", y, h, basename);
  return { basename, rendered: r, pdf: p };
}

// ---- dispatch -------------------------------------------------------------
if (allSections) {
  // If we're using the fallback, iterate the fallback panel keys; otherwise use section_order.
  const order = usingFallback
    ? Object.keys(bounds).filter((k) => k !== "frame_height")
    : (tokens.section_order ?? Object.keys(bounds).filter((k) => k !== "frame_height"));
  const results = [];
  for (const name of order) {
    if (name === "frame_height") continue;
    if (!bounds[name]) continue;
    const r = await cropSection(name);
    if (r) results.push(r);
  }
  console.log(`Produced ${results.length} ${usingFallback ? "fallback panel" : "section"} crop pairs (${viewport}) in ${cropsDir}`);
} else if (sectionEnd) {
  const r = await cropRange(sectionStart, sectionEnd, outName);
  console.log(`Range crop pair written: ${r.basename}-{rendered,pdf}.png`);
} else if (sectionStart) {
  const r = await cropSection(sectionStart, outName);
  if (r) console.log(`Section crop pair written: ${r.basename}-{rendered,pdf}.png`);
} else {
  console.error("Provide a section name, a section range, or --all-sections.");
  process.exit(1);
}
