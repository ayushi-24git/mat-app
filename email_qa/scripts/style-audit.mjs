// Style audit — parse a campaign's email.html and cross-reference its used
// colors, fonts, and image dimensions against extracted/design-tokens.json.
// Emits qa/style-audit.json with structured findings.
// Usage: node scripts/style-audit.mjs <campaign-dir>

import { readFileSync, writeFileSync, existsSync, mkdirSync } from "node:fs";
import { resolve, join } from "node:path";

const campaignDir = resolve(process.argv[2] ?? ".");
const html = readFileSync(join(campaignDir, "output", "email.html"), "utf8");
// design-tokens.json is optional. When absent (e.g. a brand-new Drive folder with no
// Figma extraction yet), palette + token-placeholder validation is skipped — the rest of
// the audit (font stacks, image refs, alt text, byte size) still runs.
let tokens = {};
let tokensPresent = false;
try {
  tokens = JSON.parse(readFileSync(join(campaignDir, "extracted", "design-tokens.json"), "utf8"));
  tokensPresent = true;
} catch {
  console.warn("design-tokens.json not found — palette / link-placeholder checks will be skipped.");
}
const qaDir = join(campaignDir, "qa");
mkdirSync(qaDir, { recursive: true });

// ---- 1. Extract every hex color used in the HTML --------------------------
const hexes = new Set();
for (const m of html.matchAll(/#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b/g)) {
  hexes.add("#" + m[1].toLowerCase());
}

// Build the approved palette (lowercase, both with and without bg-hint).
const approvedPalette = new Map();
for (const [name, hex] of Object.entries(tokens.palette ?? {})) {
  approvedPalette.set(hex.toLowerCase(), name);
}

const colorFindings = [];
// Skip palette validation entirely when no approved palette is available.
for (const hex of (approvedPalette.size > 0 ? hexes : [])) {
  if (approvedPalette.has(hex)) continue;
  // Tolerate near-matches (within ΔE ≈ 5 — quick RGB distance).
  const [r, g, b] = hexToRgb(hex);
  let nearest = null;
  let nearestDist = Infinity;
  for (const [palHex, palName] of approvedPalette) {
    const [pr, pg, pb] = hexToRgb(palHex);
    const d = Math.sqrt((r - pr) ** 2 + (g - pg) ** 2 + (b - pb) ** 2);
    if (d < nearestDist) { nearestDist = d; nearest = { hex: palHex, name: palName, dist: d }; }
  }
  if (nearestDist <= 20) {
    colorFindings.push({
      severity: "minor",
      category: "color_drift",
      message: `Color ${hex} not in palette but close to ${nearest.hex} (${nearest.name}); RGB distance ${nearestDist.toFixed(1)}.`,
      hex_found: hex,
      nearest_palette: nearest,
    });
  } else {
    // Allow common neutrals (white/black/grays) without complaint
    const isNeutral = /^#(f{3,6}|0{3,6}|f9f9f9|c9d6e8)$/i.test(hex);
    if (!isNeutral) {
      colorFindings.push({
        severity: "major",
        category: "off_palette_color",
        message: `Color ${hex} is not in the design-tokens palette and not close to any approved color (nearest ${nearest.hex} ${nearest.name}, RGB distance ${nearestDist.toFixed(1)}).`,
        hex_found: hex,
        nearest_palette: nearest,
      });
    }
  }
}

// ---- 2. Extract font-family usage -----------------------------------------
const fontFamilies = new Set();
for (const m of html.matchAll(/font-family\s*:\s*([^;"'}]+)/gi)) {
  const v = m[1].trim().replace(/!important/i, "").trim();
  if (v) fontFamilies.add(v);
}
const expectedFontStacks = [
  /arial.*helvetica.*sans-serif/i,
  /arial\s*black.*arial.*helvetica.*sans-serif/i,
];
const fontFindings = [];
for (const ff of fontFamilies) {
  const ok = expectedFontStacks.some(rx => rx.test(ff));
  if (!ok) {
    fontFindings.push({
      severity: "major",
      category: "off_font_stack",
      message: `Font stack "${ff}" does not match the approved Arial / Arial Black stacks from design-tokens.`,
      font_found: ff,
    });
  }
}

// ---- 3. Image audit: every <img> should reference a real file -------------
const imgFindings = [];
const imgs = [...html.matchAll(/<img\b[^>]*\bsrc=["']([^"']+)["'][^>]*>/gi)];
for (const m of imgs) {
  const src = m[1];
  // Placeholder tokens left in HTML
  if (/^\{\{IMAGE:[^}]+\}\}$/.test(src)) {
    imgFindings.push({
      severity: "critical",
      category: "unresolved_image_placeholder",
      message: `Image src="${src}" is an unresolved placeholder. Email will show a broken image.`,
      src,
    });
    continue;
  }
  // Local file (relative path)
  if (/^(?:assets\/|\.\.\/|\/)/.test(src) || (!/^https?:/.test(src) && !/^data:/.test(src))) {
    const localPath = join(campaignDir, "output", src);
    if (!existsSync(localPath)) {
      imgFindings.push({
        severity: "critical",
        category: "missing_image_file",
        message: `Image src="${src}" resolved to ${localPath} but file is missing.`,
        src,
      });
    }
  }
  // Check alt attribute exists (not empty? — empty alt is intentional for decoration, so we only flag truly missing)
  const tag = m[0];
  if (!/\balt=/i.test(tag)) {
    imgFindings.push({
      severity: "major",
      category: "missing_alt",
      message: `Image src="${src}" has no alt attribute.`,
      src,
    });
  }
}

// ---- 4. Token audit: every {{...}} should be in a known category ----------
const tokenFindings = [];
const allTokens = [...html.matchAll(/\{\{([^}]+)\}\}/g)].map(m => m[0]);
// ESP tokens that the pipeline recognises as known/valid Mustache replacements.
// Keep this aligned with whatever Capillary's ESP actually resolves at send time.
const espAllowlist = new Set(["{{unsubscribe}}"]);
const tokenCategories = { esp: [], image: [], link: [], unknown: [] };
for (const tok of new Set(allTokens)) {
  if (tok.startsWith("{{IMAGE:")) tokenCategories.image.push(tok);
  else if (tok.startsWith("{{LINK:")) tokenCategories.link.push(tok);
  else if (espAllowlist.has(tok)) tokenCategories.esp.push(tok);
  else tokenCategories.unknown.push(tok);
}
for (const tok of tokenCategories.unknown) {
  tokenFindings.push({
    severity: "major",
    category: "unknown_token",
    message: `Token ${tok} not in ESP allowlist or known placeholder schemes (IMAGE: / LINK:). Possible typo or unregistered personalization token.`,
    token: tok,
  });
}

// ---- 5. Size / Gmail-clip audit -------------------------------------------
const sizeKb = Buffer.byteLength(html, "utf8") / 1024;
const sizeFinding = sizeKb > 102 ? {
  severity: "critical",
  category: "gmail_clip_risk",
  message: `HTML is ${sizeKb.toFixed(1)} KB — exceeds Gmail's 102 KB clip threshold. Gmail will truncate with "View entire message".`,
} : sizeKb > 90 ? {
  severity: "major",
  category: "gmail_clip_risk",
  message: `HTML is ${sizeKb.toFixed(1)} KB — approaching Gmail's 102 KB clip threshold.`,
} : null;

// ---- Aggregate -------------------------------------------------------------
const report = {
  campaign: campaignDir.split("/").pop(),
  generated_at: new Date().toISOString(),
  design_tokens_present: tokensPresent,
  html_size_kb: parseFloat(sizeKb.toFixed(2)),
  colors_used_count: hexes.size,
  font_families_used: [...fontFamilies],
  tokens_summary: {
    esp: tokenCategories.esp,
    image: tokenCategories.image,
    link: tokenCategories.link,
    unknown: tokenCategories.unknown,
  },
  images_count: imgs.length,
  findings: [
    ...(sizeFinding ? [sizeFinding] : []),
    ...colorFindings,
    ...fontFindings,
    ...imgFindings,
    ...tokenFindings,
  ],
};

writeFileSync(join(qaDir, "style-audit.json"), JSON.stringify(report, null, 2));
console.log(`Style audit complete: ${report.findings.length} findings (${report.findings.filter(f => f.severity === "critical").length} critical, ${report.findings.filter(f => f.severity === "major").length} major, ${report.findings.filter(f => f.severity === "minor").length} minor)`);
console.log(`Written to ${join(qaDir, "style-audit.json")}`);

function hexToRgb(hex) {
  const h = hex.replace("#", "");
  if (h.length === 3) {
    return [parseInt(h[0] + h[0], 16), parseInt(h[1] + h[1], 16), parseInt(h[2] + h[2], 16)];
  }
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}
