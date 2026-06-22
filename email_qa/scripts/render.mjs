// Render output/email.html via headless Chromium at desktop (640) and mobile (375) widths.
// Outputs qa/rendered-desktop.png and qa/rendered-mobile.png.
// Usage: node scripts/render.mjs <campaign-dir>

import { chromium } from "playwright";
import { existsSync, mkdirSync } from "node:fs";
import { resolve, join } from "node:path";
import { pathToFileURL } from "node:url";

const campaignDir = resolve(process.argv[2] ?? ".");
const html = join(campaignDir, "output", "email.html");
const qaDir = join(campaignDir, "qa");

if (!existsSync(html)) {
  console.error(`output/email.html not found at ${html}. Run /email-generate first.`);
  process.exit(1);
}
mkdirSync(qaDir, { recursive: true });

const browser = await chromium.launch();
try {
  for (const [label, width] of [["desktop", 640], ["mobile", 375]]) {
    const ctx = await browser.newContext({ viewport: { width, height: 800 }, deviceScaleFactor: 2 });
    const page = await ctx.newPage();
    await page.goto(pathToFileURL(html).toString(), { waitUntil: "networkidle" });
    await page.waitForTimeout(250);
    await page.screenshot({ path: join(qaDir, `rendered-${label}.png`), fullPage: true });
    await ctx.close();
    console.log(`rendered-${label}.png written`);
  }
} finally {
  await browser.close();
}
