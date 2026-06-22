// Outlook Classic (Outlook 2007–2021 desktop on Windows) rule pack.
// Outlook Classic renders email via Microsoft Word's HTML engine — closed-source,
// not a browser. This is the highest-risk client; rules here are conservative.
//
// Severity guide:
//   critical = will visibly break / drop content / collapse layout
//   major    = will visibly degrade (wrong color, lost rounded corners, wrong padding)
//   minor    = subtle / cosmetic in Outlook Classic
//
// Reference: Campaign Monitor CSS support matrix + caniemail.com + Email Geeks.

export default [
  // ────────────────────── Layout primitives ──────────────────────
  {
    id: "OC-001",
    client: "Outlook Classic",
    severity: "critical",
    name: "display: flex not supported",
    description: "Outlook Classic ignores `display:flex`. Children collapse to block stacking.",
    detect: { type: "regex", pattern: /display\s*:\s*(?:inline-)?flex\b/gi },
    suggested_fix: "Replace flex layout with table-based layout (<tr> for rows, <td> for columns).",
    doc_url: "https://www.caniemail.com/features/css-display-flex/",
  },
  {
    id: "OC-002",
    client: "Outlook Classic",
    severity: "critical",
    name: "display: grid not supported",
    description: "Outlook Classic ignores `display:grid` / `display:inline-grid`. Layout collapses.",
    detect: { type: "regex", pattern: /display\s*:\s*(?:inline-)?grid\b/gi },
    suggested_fix: "Use tables. Grid has no Outlook Classic equivalent.",
    doc_url: "https://www.caniemail.com/features/css-display-grid/",
  },
  {
    id: "OC-003",
    client: "Outlook Classic",
    severity: "critical",
    name: "position: absolute / fixed / sticky not supported",
    description: "Outlook Classic ignores out-of-flow positioning. Elements render in document order without offsets.",
    detect: { type: "regex", pattern: /position\s*:\s*(?:absolute|fixed|sticky)\b/gi },
    suggested_fix: "Use table-cell padding or empty spacer cells to position visually.",
    doc_url: "https://www.caniemail.com/features/css-position/",
  },

  // ────────────────────── CSS engine quirks ──────────────────────
  {
    id: "OC-010",
    client: "Outlook Classic",
    severity: "critical",
    name: "calc() not supported",
    description: "Word engine cannot resolve `calc()` expressions. The declaration is dropped silently — element may default to 0 or its initial value.",
    detect: { type: "regex", pattern: /\bcalc\s*\(/gi },
    suggested_fix: "Pre-compute the value at authoring time and write the literal pixel/percent.",
    doc_url: "https://www.caniemail.com/features/css-function-calc/",
  },
  {
    id: "OC-011",
    client: "Outlook Classic",
    severity: "critical",
    name: "CSS variables (var()) not supported",
    description: "Word does not resolve `var(--name)`. The fallback (if any after the comma) is also ignored — the whole property is dropped.",
    detect: { type: "regex", pattern: /\bvar\s*\(\s*--/gi },
    suggested_fix: "Inline the literal value. juice does not resolve CSS variables either.",
    doc_url: "https://www.caniemail.com/features/css-variables/",
  },
  {
    id: "OC-012",
    client: "Outlook Classic",
    severity: "major",
    name: "border-radius ignored on tables/divs",
    description: "Word renders sharp corners regardless of `border-radius`. Visible on buttons, panels, badges.",
    detect: { type: "regex", pattern: /border-radius\s*:/gi },
    suggested_fix: "Accept sharp corners in Outlook Classic, or wrap the element in a VML <v:roundrect> inside an [if mso] conditional for true bulletproof buttons.",
    doc_url: "https://www.caniemail.com/features/css-border-radius/",
  },
  {
    id: "OC-013",
    client: "Outlook Classic",
    severity: "minor",
    name: "box-shadow ignored",
    description: "Word does not render `box-shadow`. The element appears without its shadow.",
    detect: { type: "regex", pattern: /box-shadow\s*:/gi },
    suggested_fix: "Treat shadows as decorative; if required, fake them with a thin grey border or a background image.",
    doc_url: "https://www.caniemail.com/features/css-box-shadow/",
  },
  {
    id: "OC-014",
    client: "Outlook Classic",
    severity: "minor",
    name: "transform ignored",
    description: "Word does not apply `transform` (rotate, scale, translate, etc.). Element renders in its default position/orientation.",
    detect: { type: "regex", pattern: /transform\s*:/gi },
    suggested_fix: "Avoid transforms; if rotation is essential, pre-render the rotated element as an image.",
    doc_url: "https://www.caniemail.com/features/css-transform/",
  },

  // ────────────────────── Text & typography ──────────────────────
  {
    id: "OC-020",
    client: "Outlook Classic",
    severity: "major",
    name: "Padding on <p> renders inconsistently",
    description: "Word interprets `<p>` padding as part of paragraph margin in unpredictable ways. Use padding on a wrapping <td> instead, or convert to margin on <p>.",
    detect: {
      type: "custom",
      fn: (html) => {
        const out = [];
        for (const m of html.matchAll(/<p\b[^>]*\bstyle=(["'])([^"']*)\1[^>]*>/gi)) {
          const pm = m[2].match(/padding\s*:\s*([^;]+)/i);
          if (!pm) continue;
          // Skip if every component is 0 / 0px (the rule's about *non-zero* padding).
          const parts = pm[1].trim().split(/\s+/);
          const allZero = parts.every((p) => p === "0" || p === "0px");
          if (!allZero) out.push(m[0].slice(0, 120));
        }
        return out;
      },
    },
    suggested_fix: "Combine `margin: 0; padding: X Y Z W` into `margin: X Y Z W` on the <p>, or move the padding to a wrapping <td>.",
    doc_url: "https://www.caniemail.com/features/html-p/",
  },
  {
    id: "OC-021",
    client: "Outlook Classic",
    severity: "minor",
    name: "Missing mso-line-height-rule",
    description: "Without `mso-line-height-rule: exactly`, Word may add extra space between lines, especially when mixing different line-heights.",
    detect: { type: "regex_absent", pattern: /mso-line-height-rule\s*:\s*exactly/gi },
    suggested_fix: "On text-bearing <td> or <p>, add `mso-line-height-rule: exactly` alongside `line-height`.",
    doc_url: "https://www.litmus.com/community/learning/19-line-height-and-msoline-height-rule",
  },
  {
    id: "OC-022",
    client: "Outlook Classic",
    severity: "minor",
    name: "letter-spacing partially supported",
    description: "Word's support for `letter-spacing` is inconsistent across versions. Visual result may differ by 1–2px from spec.",
    detect: { type: "regex", pattern: /letter-spacing\s*:/gi },
    suggested_fix: "Test in Outlook 2019 / 365 specifically. Avoid relying on tight letter-spacing for layout.",
    doc_url: "https://www.caniemail.com/features/css-letter-spacing/",
  },

  // ────────────────────── Tables ──────────────────────
  {
    id: "OC-030",
    client: "Outlook Classic",
    severity: "major",
    name: "Layout table missing cellpadding/cellspacing/border attributes",
    description: "Word adds default cellpadding/spacing if not zeroed. Spacing creeps in between cells.",
    detect: {
      type: "custom",
      fn: (html) => {
        const tables = html.match(/<table\b[^>]*>/gi) ?? [];
        return tables
          .filter((t) => !/cellpadding\s*=\s*["']?0/i.test(t) || !/cellspacing\s*=\s*["']?0/i.test(t) || !/\bborder\s*=\s*["']?0/i.test(t))
          .slice(0, 5);
      },
    },
    suggested_fix: "Add `cellpadding=\"0\" cellspacing=\"0\" border=\"0\"` to every <table>.",
  },
  {
    id: "OC-032",
    client: "Outlook Classic",
    severity: "major",
    name: 'role="pre sentation" typo (literal whitespace in attribute value)',
    description: "Attribute `role=\"pre sentation\"` with a literal space is invalid — browsers and screen readers ignore the role. Real bug observed in the Optum reference template; flagged so it doesn't propagate.",
    detect: { type: "regex", pattern: /role\s*=\s*["']pre\s+sentation["']/gi },
    suggested_fix: 'Change to `role="presentation"` (no space).',
  },
  {
    id: "OC-033",
    client: "Outlook Classic",
    severity: "minor",
    name: "Outer container width > 640px",
    description: "Older Outlook clients at default DPI handle widths up to ~640px reliably. Above that, horizontal scrolling or rendering glitches appear.",
    detect: { type: "custom", fn: (html) => {
      const widths = [...html.matchAll(/<table\b[^>]*\bwidth\s*=\s*["']?(\d+)/gi)].map(m => parseInt(m[1], 10));
      return widths.filter(w => w > 640).map(w => `width="${w}"`).slice(0, 5);
    }},
    suggested_fix: "Constrain the outermost container table to width=\"640\".",
  },

  // ────────────────────── MSO conditionals ──────────────────────
  {
    id: "OC-040",
    client: "Outlook Classic",
    severity: "major",
    name: "No MSO conditional wrapper for outer container width",
    description: "Without `<!--[if mso]><table width=640>...<![endif]-->` wrapping the outer content, Outlook Classic may not enforce the 640px container — content stretches to inbox width.",
    detect: { type: "regex_absent", pattern: /<!--\[if mso\]>/i },
    suggested_fix: "Wrap the outer container in an MSO conditional table that fixes width=\"640\" for Word's engine.",
  },

  // ────────────────────── HTML hygiene ──────────────────────
  {
    id: "OC-050",
    client: "Outlook Classic",
    severity: "major",
    name: "HTML5 semantic elements used",
    description: "Word treats <section>, <article>, <nav>, <header>, <footer>, <main> as unknown elements — they render but may not style as expected.",
    detect: { type: "regex", pattern: /<\/?(section|article|nav|header|footer|main|aside)\b/gi },
    suggested_fix: "Replace semantic HTML5 elements with <div> or <table>. Email is a 1999-vintage medium.",
  },
  {
    id: "OC-051",
    client: "Outlook Classic",
    severity: "critical",
    name: "<script> tag present",
    description: "All email clients (not just Outlook) strip <script> tags. If your layout depends on JS, it will break.",
    detect: { type: "regex", pattern: /<script\b/gi },
    suggested_fix: "Remove all <script> tags. Email does not execute JavaScript.",
  },
  {
    id: "OC-052",
    client: "Outlook Classic",
    severity: "critical",
    name: "<form> tag present",
    description: "Form elements are stripped or behave unpredictably in Outlook Classic (and Gmail).",
    detect: { type: "regex", pattern: /<form\b/gi },
    suggested_fix: "Use a link to a hosted form page instead.",
  },

  // ────────────────────── Images / fonts ──────────────────────
  {
    id: "OC-060",
    client: "Outlook Classic",
    severity: "major",
    name: "SVG <img> not rendered",
    description: "Outlook Classic does not render SVG. Image area shows broken-image icon.",
    detect: { type: "regex", pattern: /<img\b[^>]*\bsrc=["'][^"']*\.svg\b[^"']*["']/gi },
    suggested_fix: "Convert SVG assets to PNG or JPG at the required resolution before linking.",
    doc_url: "https://www.caniemail.com/features/image-svg/",
  },
  {
    id: "OC-061",
    client: "Outlook Classic",
    severity: "major",
    name: "@font-face / web font used without web-safe fallback",
    description: "Outlook Classic does not load @font-face fonts. If the font stack ends with an exotic name and no Arial / Helvetica fallback, recipients see Times New Roman by default.",
    detect: { type: "custom", fn: (html) => {
      const stacks = [...html.matchAll(/font-family\s*:\s*([^;"'}]+)/gi)].map(m => m[1].trim());
      return stacks.filter(s => s && !/arial|helvetica|sans-serif|serif|verdana|tahoma|georgia|times|courier|trebuchet|monospace/i.test(s)).slice(0, 5);
    }},
    suggested_fix: "End every font stack with a web-safe family (Arial, Helvetica, Georgia, Times, Verdana, Tahoma, etc.) and a generic family (sans-serif / serif).",
    doc_url: "https://www.caniemail.com/features/css-at-font-face/",
  },
];
