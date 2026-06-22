// Gmail (web + iOS + Android) rule pack.
// Gmail web/Outlook365-web uses a CSS sandbox; Gmail apps strip more aggressively.
//
// Severity guide:
//   critical = clipped, stripped, or visibly broken on send
//   major    = visible degradation on mobile clients
//   minor    = cosmetic / partial support

export default [
  {
    id: "GM-001",
    client: "Gmail (all)",
    severity: "critical",
    name: "HTML size > 102 KB (Gmail clip threshold)",
    description: "Gmail truncates the email at 102 KB and shows a 'View entire message' link. Anything after the truncation point — often including the unsubscribe link — is hidden by default.",
    detect: { type: "size", limit_kb: 102, comparator: ">" },
    suggested_fix: "Reduce HTML size: remove commented-out blocks, deduplicate inline styles, compress repeated table boilerplate.",
    doc_url: "https://www.litmus.com/blog/gmail-clipping-101",
  },
  {
    id: "GM-002",
    client: "Gmail (all)",
    severity: "minor",
    name: "HTML size approaching 102 KB",
    description: "Above 90 KB you're close to the clip threshold. Add 10 KB more and Gmail will start truncating.",
    detect: { type: "size", limit_kb: 90, comparator: ">" },
    suggested_fix: "Keep an eye on size as you iterate. Remove dead code and redundant inline styles.",
  },
  {
    id: "GM-003",
    client: "Gmail (mobile)",
    severity: "major",
    name: ":hover pseudo-class used (stripped on Gmail iOS/Android)",
    description: "Gmail mobile apps strip pseudo-class selectors from <style> blocks. Hover effects degrade silently.",
    detect: { type: "regex", pattern: /:hover\s*[{,]/gi },
    suggested_fix: "Provide a non-:hover default style. Treat hover as progressive enhancement, not load-bearing.",
  },
  {
    id: "GM-004",
    client: "Gmail (all)",
    severity: "critical",
    name: "<form> tag present",
    description: "Gmail strips <form> elements. Any interactive form inside the email will be removed.",
    detect: { type: "regex", pattern: /<form\b/gi },
    suggested_fix: "Use a link to a hosted form page.",
  },
  {
    id: "GM-005",
    client: "Gmail (all)",
    severity: "critical",
    name: "External CSS via <link rel=stylesheet>",
    description: "Gmail does not load external stylesheets. Styles defined in linked CSS files are not applied.",
    detect: { type: "regex", pattern: /<link\b[^>]*rel\s*=\s*["']stylesheet["']/gi },
    suggested_fix: "Move CSS into a <style> block in <head> and let juice inline it.",
  },
  {
    id: "GM-006",
    client: "Gmail (mobile)",
    severity: "major",
    name: "background-image on <body> (stripped on mobile clients)",
    description: "Gmail mobile apps strip body background-image. Desktop Gmail web preserves it. Resulting inconsistency between viewports.",
    detect: { type: "regex", pattern: /<body\b[^>]*\bbackground-image\s*:/gi },
    suggested_fix: "Apply background color to <body>; use background-image on inner <table> rows only.",
  },
  {
    id: "GM-007",
    client: "Gmail (all)",
    severity: "minor",
    name: "Embedded data: URI image (base64)",
    description: "Gmail does not render `<img src=\"data:...\">` images in most cases — they're shown as broken.",
    detect: { type: "regex", pattern: /<img\b[^>]*\bsrc\s*=\s*["']data:/gi },
    suggested_fix: "Host images on a CDN and use an https:// URL.",
  },
];
