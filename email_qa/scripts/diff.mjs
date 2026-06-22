// Pixel-diff rendered HTML against the PDF reference.
// Reads qa/rendered-{label}.png and extracted/pdf-{label}.png, writes qa/diff-{label}.png.
// Prints mismatch % per viewport.
// Usage: node scripts/diff.mjs <campaign-dir>

import { readFileSync, writeFileSync, mkdirSync, existsSync } from "node:fs";
import { resolve, join } from "node:path";
import { PNG } from "pngjs";
import pixelmatch from "pixelmatch";

const campaignDir = resolve(process.argv[2] ?? ".");
const qaDir = join(campaignDir, "qa");
const extractedDir = join(campaignDir, "extracted");
mkdirSync(qaDir, { recursive: true });

function loadPng(p) { return PNG.sync.read(readFileSync(p)); }

function padTo(png, width, height) {
  if (png.width === width && png.height === height) return png;
  const out = new PNG({ width, height });
  for (let y = 0; y < png.height; y++) {
    for (let x = 0; x < png.width; x++) {
      const i = (y * png.width + x) * 4;
      const j = (y * width + x) * 4;
      out.data[j] = png.data[i];
      out.data[j + 1] = png.data[i + 1];
      out.data[j + 2] = png.data[i + 2];
      out.data[j + 3] = png.data[i + 3];
    }
  }
  return out;
}

const results = {};

for (const label of ["desktop", "mobile"]) {
  const renderedPath = join(qaDir, `rendered-${label}.png`);
  const pdfPath = join(extractedDir, `pdf-${label}.png`);
  if (!existsSync(renderedPath) || !existsSync(pdfPath)) {
    console.warn(`Skipping ${label} — missing ${renderedPath} or ${pdfPath}`);
    continue;
  }
  const a = loadPng(renderedPath);
  const b = loadPng(pdfPath);
  const width = Math.max(a.width, b.width);
  const height = Math.max(a.height, b.height);
  const ap = padTo(a, width, height);
  const bp = padTo(b, width, height);
  const diff = new PNG({ width, height });
  const mismatched = pixelmatch(ap.data, bp.data, diff.data, width, height, {
    threshold: 0.1,
    alpha: 0.4,
    diffColor: [255, 0, 0],
  });
  const pct = (mismatched / (width * height) * 100).toFixed(2);
  writeFileSync(join(qaDir, `diff-${label}.png`), PNG.sync.write(diff));
  results[label] = { width, height, mismatched_pixels: mismatched, mismatch_pct: parseFloat(pct) };
  console.log(`${label}: ${pct}% mismatch (${mismatched} px / ${width}x${height})`);
}

writeFileSync(join(qaDir, "diff-stats.json"), JSON.stringify(results, null, 2));
