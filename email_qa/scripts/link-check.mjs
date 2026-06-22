// Link QA for a campaign's output/email.html.
//
// What it does:
//   1. Extracts every <a href> + its visible anchor text from the HTML.
//   2. Resolves {{LINK:*}} placeholder hrefs via output/manifest.json.
//   3. Runs automated checks (HTTPS-only, no javascript:/data:, no localhost, well-formed URL, etc.).
//   4. If input/link-mapping.json exists, validates each entry against the HTML:
//        - missing link (anchor text not found) → critical
//        - wrong URL (anchor found but href doesn't match expected) → per entry's severity_if_wrong (default major)
//        - unmapped link (in HTML but not in mapping) → minor
//   5. If input/link-mapping.json is absent, writes a scaffolded template at
//      input/link-mapping.template.json with the anchor_text pre-filled, expected_url = "FILL_ME_IN".
//
// Output: qa/link-check.json
// Usage:  node scripts/link-check.mjs <campaign-dir>

import { readFileSync, writeFileSync, existsSync, mkdirSync } from "node:fs";
import { resolve, join } from "node:path";

const campaignDir = resolve(process.argv[2] ?? ".");
const htmlPath = join(campaignDir, "output", "email.html");
const mappingPath = join(campaignDir, "input", "link-mapping.json");
const templatePath = join(campaignDir, "input", "link-mapping.template.json");
const manifestPath = join(campaignDir, "output", "manifest.json");
const qaDir = join(campaignDir, "qa");
mkdirSync(qaDir, { recursive: true });

if (!existsSync(htmlPath)) {
  console.error(`output/email.html not found at ${htmlPath}`);
  process.exit(1);
}
const html = readFileSync(htmlPath, "utf8");

// ---- helpers --------------------------------------------------------------
const manifest = existsSync(manifestPath) ? JSON.parse(readFileSync(manifestPath, "utf8")) : null;

function resolveHref(href) {
  if (!manifest?.link_placeholders) return href;
  return manifest.link_placeholders[href] ?? href;
}

function isMustacheToken(s) {
  return /^\{\{[^}]+\}\}$/.test(s);
}

function normalizeAnchorText(htmlFragment) {
  // For image links (<a><img alt="..."></a>), the alt attribute is the effective visible text.
  // Extract any nested <img alt> values first; use them when no plain text is present.
  const imgAlts = [...htmlFragment.matchAll(/<img\b[^>]*\balt\s*=\s*["']([^"']*)["']/gi)]
    .map((m) => m[1].trim())
    .filter(Boolean);

  const plainText = htmlFragment
    .replace(/<[^>]+>/g, "")
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&#39;|&apos;/gi, "'")
    .replace(/&quot;/gi, '"')
    .replace(/&[a-z]+;/gi, " ")
    .replace(/\s+/g, " ")
    .trim();

  if (plainText) return plainText;
  return imgAlts.join(" ").trim();
}

// ---- extract all <a href> from the HTML -----------------------------------
const allLinks = [];
const linkRe = /<a\b[^>]*\bhref\s*=\s*["']([^"']+)["'][^>]*>([\s\S]*?)<\/a>/gi;
let m;
while ((m = linkRe.exec(html)) !== null) {
  const href = m[1];
  const anchor_text = normalizeAnchorText(m[2]);
  allLinks.push({ href, anchor_text, resolved_href: resolveHref(href) });
}

// ---- automated checks (no mapping required) -------------------------------
const findings = [];

for (const link of allLinks) {
  const { href, anchor_text, resolved_href } = link;

  // 1. Empty anchor text (accessibility)
  if (!anchor_text) {
    findings.push({
      severity: "major",
      category: "empty_anchor_text",
      message: `Link with href="${href}" has no visible text. Screen readers will say "link, link" with no context.`,
      href,
      anchor_text,
    });
  }

  // ESP tokens and placeholder URLs are exempt from scheme/URL checks.
  if (isMustacheToken(href)) continue;

  // 2. Unsafe schemes
  if (/^(javascript|data|vbscript|file):/i.test(resolved_href)) {
    findings.push({
      severity: "critical",
      category: "unsafe_scheme",
      message: `Link uses unsafe scheme: ${resolved_href}`,
      href, anchor_text,
    });
    continue;
  }

  const isMailto = /^mailto:/i.test(resolved_href);
  const isTel = /^tel:/i.test(resolved_href);
  if (isMailto || isTel) continue; // Skip URL-shape checks for mailto:/tel:

  // 3. Plain HTTP
  if (/^http:\/\//i.test(resolved_href)) {
    findings.push({
      severity: "major",
      category: "insecure_http",
      message: `Link uses plain http (not https): ${resolved_href}. Some email clients block or warn on non-HTTPS links.`,
      href, anchor_text,
    });
  }

  // 4. Localhost / dev URLs
  if (/(localhost|127\.0\.0\.1|0\.0\.0\.0|:\d{4,5}(?![0-9]))/i.test(resolved_href)) {
    findings.push({
      severity: "critical",
      category: "dev_url",
      message: `Link points to a local/dev URL: ${resolved_href}. Will not work for recipients.`,
      href, anchor_text,
    });
  }

  // 5. Well-formed URL?
  try {
    if (resolved_href.startsWith("http://") || resolved_href.startsWith("https://")) {
      new URL(resolved_href);
    }
  } catch {
    findings.push({
      severity: "critical",
      category: "malformed_url",
      message: `Link is not a valid URL: ${resolved_href}`,
      href, anchor_text,
    });
  }

  // 6. Anchor text identical to URL (accessibility hint)
  if (anchor_text && anchor_text.toLowerCase() === resolved_href.toLowerCase()) {
    findings.push({
      severity: "minor",
      category: "anchor_text_equals_url",
      message: `Anchor text is the URL itself. Prefer descriptive text (e.g. "Visit the resource guide" vs "https://...").`,
      href, anchor_text,
    });
  }

  // 7. Unresolved {{LINK:*}} placeholder (no manifest entry)
  if (/^\{\{LINK:[^}]+\}\}$/.test(href) && resolved_href === href) {
    findings.push({
      severity: "major",
      category: "unresolved_link_placeholder",
      message: `Link placeholder ${href} has no entry in output/manifest.json/link_placeholders.`,
      href, anchor_text,
    });
  }
}

// ---- mapping-based checks (if input/link-mapping.json exists) -------------
let mappingState = "absent";
let mappingFindings = [];

if (existsSync(mappingPath)) {
  mappingState = "present";
  const mapping = JSON.parse(readFileSync(mappingPath, "utf8"));

  // Two schemas supported:
  //   (A) Structured: { "clickable_links": [{ anchor_text, expected_url, severity_if_wrong?, where? }, ...] }
  //   (B) Flat key-value: { "anchor text": "url", ..., "figma": "...", ... }
  // The flat schema treats each key (except reserved metadata keys like "figma") as an anchor_text.
  const RESERVED_KEYS = new Set(["figma", "_note", "_notes", "_comment", "_comments"]);
  const expected = Array.isArray(mapping.clickable_links)
    ? mapping.clickable_links
    : Object.entries(mapping)
        .filter(([k]) => !RESERVED_KEYS.has(k))
        .map(([anchor_text, expected_url]) => ({ anchor_text, expected_url }));

  const matched = new Set();

  for (const exp of expected) {
    const sev = exp.severity_if_wrong ?? "major";
    const expectedResolved = resolveHref(exp.expected_url);

    // Match ALL anchors with this anchor_text (responsive emails often duplicate the
    // same link in desktop + mobile blocks). Each instance is validated against the
    // single expected URL.
    const instances = allLinks
      .map((link, idx) => ({ link, idx }))
      .filter(({ link }) => link.anchor_text === exp.anchor_text);

    if (instances.length === 0) {
      mappingFindings.push({
        severity: "critical",
        category: "link_missing",
        message: `Expected link "${exp.anchor_text}" → ${exp.expected_url} not found in HTML.`,
        expected: exp,
      });
      continue;
    }

    for (const { link: actual, idx } of instances) {
      matched.add(idx);
      if (actual.resolved_href !== expectedResolved) {
        mappingFindings.push({
          severity: sev,
          category: "wrong_url",
          message: `Link "${exp.anchor_text}" (instance ${idx + 1}) points to "${actual.href}" (resolves to ${actual.resolved_href}); expected "${exp.expected_url}" (resolves to ${expectedResolved}).`,
          expected: exp,
          actual,
        });
      }
    }
  }

  // Unmapped links (in HTML but not in mapping)
  // Auto-skip a few categories that are inherently unvalidatable or out-of-scope:
  //   · Mustache tokens (e.g. {{unsubscribe}}) — ESP fills at send time
  //   · href="#" — CSS-fragment placeholder, no destination to validate
  //   · tel:/mailto:/sms:/fax: — non-URL schemes; not part of link-target validation
  for (let li = 0; li < allLinks.length; li++) {
    if (matched.has(li)) continue;
    const link = allLinks[li];
    if (isMustacheToken(link.href)) continue;
    if (link.href === "#" || link.href.trim() === "") continue;
    if (/^(tel|mailto|sms|fax):/i.test(link.href)) continue;
    mappingFindings.push({
      severity: "minor",
      category: "unmapped_link",
      message: `Link "${link.anchor_text || link.href}" is in the HTML but not in link-mapping.json. Add it if intentional, or remove the link.`,
      href: link.href, anchor_text: link.anchor_text,
    });
  }
} else {
  // Scaffold a template for the user to fill in.
  const template = {
    "_note": "Auto-generated template. Fill in expected_url for each link, then save as link-mapping.json (drop the .template extension). Re-run /email-qa to validate.",
    "clickable_links": allLinks.map(l => ({
      anchor_text: l.anchor_text,
      expected_url: isMustacheToken(l.href) ? l.href : "FILL_ME_IN",
      where: "",
    })),
  };
  writeFileSync(templatePath, JSON.stringify(template, null, 2));
}

// ---- aggregate ------------------------------------------------------------
const allFindings = [...findings, ...mappingFindings];
const report = {
  campaign: campaignDir.split("/").pop(),
  generated_at: new Date().toISOString(),
  links_found: allLinks.length,
  mapping_state: mappingState,
  totals: {
    critical: allFindings.filter(f => f.severity === "critical").length,
    major: allFindings.filter(f => f.severity === "major").length,
    minor: allFindings.filter(f => f.severity === "minor").length,
  },
  all_links: allLinks,
  findings: allFindings,
};

writeFileSync(join(qaDir, "link-check.json"), JSON.stringify(report, null, 2));

console.log(`Link check: ${allLinks.length} links analyzed, ${allFindings.length} findings (${report.totals.critical} critical, ${report.totals.major} major, ${report.totals.minor} minor).`);
if (mappingState === "absent") {
  console.log(`No link-mapping.json — scaffolded template at ${templatePath}.`);
  console.log(`Fill in expected URLs and save as input/link-mapping.json to enable validation.`);
}
