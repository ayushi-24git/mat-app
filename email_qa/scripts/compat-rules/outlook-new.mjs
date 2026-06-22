// Outlook New (Microsoft 365 web + new desktop client) rule pack.
// Outlook New uses Edge/WebView2 under the hood — much more modern than Outlook Classic.
// Most rules here are about dark-mode handling and a small set of remaining quirks.
//
// Severity guide:
//   critical = visible breakage
//   major    = visible inconsistency vs design
//   minor    = cosmetic / partial support

export default [
  {
    id: "ON-001",
    client: "Outlook New",
    severity: "minor",
    name: "Missing color-scheme meta tag",
    description: "Without `<meta name=\"color-scheme\" content=\"light\">` (or `light dark`), Outlook New may apply automatic dark-mode color inversion in unexpected ways.",
    detect: { type: "regex_absent", pattern: /<meta\s+name\s*=\s*["']color-scheme["']/i },
    suggested_fix: "Add `<meta name=\"color-scheme\" content=\"light only\">` (or `light dark` if you support dark-mode designs) inside <head>.",
    doc_url: "https://www.caniemail.com/features/css-color-scheme/",
  },
  {
    id: "ON-002",
    client: "Outlook New",
    severity: "minor",
    name: "Missing supported-color-schemes meta tag",
    description: "Companion tag to color-scheme. Outlook New uses both to decide dark-mode behavior.",
    detect: { type: "regex_absent", pattern: /<meta\s+name\s*=\s*["']supported-color-schemes["']/i },
    suggested_fix: "Add `<meta name=\"supported-color-schemes\" content=\"light\">` inside <head>.",
  },
  {
    id: "ON-003",
    client: "Outlook New",
    severity: "minor",
    name: ":before / :after pseudo-elements used",
    description: "Outlook New does not render `::before` / `::after` pseudo-elements. Content generated this way is invisible.",
    detect: { type: "regex", pattern: /::?(?:before|after)\s*[{,]/gi },
    suggested_fix: "Inline the content directly into the HTML rather than generating it via pseudo-elements.",
  },
  {
    id: "ON-004",
    client: "Outlook New",
    severity: "minor",
    name: "@supports CSS feature query used",
    description: "Outlook New ignores `@supports` blocks. The fallback (outside the block) is the only thing rendered.",
    detect: { type: "regex", pattern: /@supports\s*\(/gi },
    suggested_fix: "Treat the styles outside @supports as your baseline; do not gate critical layout behind @supports.",
  },
];
