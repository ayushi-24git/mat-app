// Convert a campaign's PDF reference(s) to per-viewport PNGs.
// Supports two input shapes:
//   (a) input/reference.pdf      — single PDF, page 1 = desktop, page 2 = mobile  [legacy]
//   (b) input/desktop.pdf  +
//       input/mobile.pdf         — separate PDFs (Eaton/OPM convention via Drive assets/) [preferred]
//
// Output: extracted/pdf-desktop.png + extracted/pdf-mobile.png
// Uses pdftoppm (poppler). Install with `brew install poppler`.
// Usage: node scripts/pdf-to-png.mjs <campaign-dir>

import { execSync } from "node:child_process";
import { existsSync, mkdirSync, readdirSync, renameSync, unlinkSync } from "node:fs";
import { resolve, join } from "node:path";

const campaignDir = resolve(process.argv[2] ?? ".");
const inputDir = join(campaignDir, "input");
const outDir = join(campaignDir, "extracted");

const desktopPdf = join(inputDir, "desktop.pdf");
const mobilePdf  = join(inputDir, "mobile.pdf");
const referencePdf = join(inputDir, "reference.pdf");

// Detect which shape we're working with
const hasSeparate = existsSync(desktopPdf) && existsSync(mobilePdf);
const hasCombined = existsSync(referencePdf);

if (!hasSeparate && !hasCombined) {
  console.error(`No PDF found. Expected one of:`);
  console.error(`  - ${desktopPdf} AND ${mobilePdf}`);
  console.error(`  - ${referencePdf}`);
  process.exit(1);
}

mkdirSync(outDir, { recursive: true });

// Helper: render a single page of a PDF, rename the pdftoppm output to a canonical name.
function renderPage({ pdf, page, label, width }) {
  const tmpPrefix = join(outDir, `__tmp-${label}`);
  try {
    execSync(
      `pdftoppm -f ${page} -l ${page} -scale-to-x ${width} -scale-to-y -1 -png "${pdf}" "${tmpPrefix}"`,
      { stdio: "inherit" }
    );
  } catch (err) {
    console.warn(`pdftoppm failed for page ${page} of ${pdf} (${label}); skipping. ${err.message}`);
    return false;
  }
  const generated = readdirSync(outDir).filter(f => f.startsWith(`__tmp-${label}-`) && f.endsWith(".png"));
  if (!generated.length) {
    console.warn(`No PNG produced for page ${page} of ${pdf} (${label}). PDF may be shorter than expected.`);
    return false;
  }
  const src = join(outDir, generated[0]);
  const dst = join(outDir, `pdf-${label}.png`);
  if (existsSync(dst)) unlinkSync(dst);
  renameSync(src, dst);
  return true;
}

// Render desktop and mobile, picking the right source per shape.
if (hasSeparate) {
  console.log(`[pdf-to-png] separate-PDFs mode: desktop.pdf + mobile.pdf`);
  renderPage({ pdf: desktopPdf, page: 1, label: "desktop", width: 1280 });
  renderPage({ pdf: mobilePdf,  page: 1, label: "mobile",  width: 750 });
} else {
  console.log(`[pdf-to-png] combined-PDF mode: reference.pdf (page 1 = desktop, page 2 = mobile)`);
  renderPage({ pdf: referencePdf, page: 1, label: "desktop", width: 1280 });
  renderPage({ pdf: referencePdf, page: 2, label: "mobile",  width: 750 });
}

console.log(`PDF -> PNG written to ${outDir}`);
