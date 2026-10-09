// Render the raster brand assets that need real fonts: the wordmark lockups and the
// social card. Colours come from tokens.json and the mark from logo/mark.svg, both
// written by build.py, so run that first.
//
//   node branding/render.mjs
//
// Needs Playwright with a Chromium (NODE_PATH pointing at a global install works) and
// network access to Google Fonts. Writes PNGs to branding/logo/ and docs/assets/brand/.

import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import { execFileSync } from "node:child_process";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const here = dirname(fileURLToPath(import.meta.url));
const tokens = JSON.parse(readFileSync(join(here, "tokens.json"), "utf8"));
const c = (family, step) => tokens.families[family].steps[String(step)].hex;
const s = (name) => tokens.surfaces[name].hex;
const mark = readFileSync(join(here, "logo", "mark.svg"), "utf8");
const markLight = readFileSync(join(here, "logo", "mark-light.svg"), "utf8");

const FONTS =
  "https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@100..125,600..900" +
  "&family=Instrument+Sans:wght@400;500;600&family=JetBrains+Mono:wght@500;700&display=block";

const page = (body, css) => `<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="${FONTS}">
<style>
  * { box-sizing: border-box; margin: 0; }
  body { font-family: "Instrument Sans", sans-serif; }
  .word { font-family: "Archivo", sans-serif; font-weight: 900; font-stretch: 112%;
          letter-spacing: -0.035em; line-height: 1; }
  ${css}
</style></head><body>${body}
<div aria-hidden="true" style="position:absolute;opacity:0;pointer-events:none">
  <span style="font-family:'Archivo';font-weight:900;font-stretch:112%">a</span>
  <span style="font-family:'Instrument Sans'">a</span>
  <span style="font-family:'JetBrains Mono'">a</span>
</div></body></html>`;

function lockup(markSvg, ink, bg) {
  return page(
    `<div class="lockup">${markSvg.replace(/width="64" height="64"/, 'width="168" height="168"')}
     <span class="word">grip</span></div>`,
    `body { background: ${bg}; }
     .lockup { display: inline-flex; align-items: center; gap: 40px; padding: 48px 72px 48px 48px; }
     .word { font-size: 176px; color: ${ink}; transform: translateY(-10px); }`,
  );
}

function card() {
  const ring = (pct) => {
    const r = 118, len = 2 * Math.PI * r;
    return `<svg width="300" height="300" viewBox="0 0 300 300">
      <circle cx="150" cy="150" r="${r}" fill="none" stroke="${c("graphite", 900)}" stroke-width="26"/>
      <circle cx="150" cy="150" r="${r}" fill="none" stroke="${c("ember", 400)}" stroke-width="26"
        stroke-linecap="round" stroke-dasharray="${(len * pct).toFixed(1)} ${len.toFixed(1)}"
        transform="rotate(-90 150 150)"/>
    </svg>`;
  };
  return page(
    `<main>
      <div class="grid"></div>
      <section class="left">
        <div class="brand">${mark.replace(/width="64" height="64"/, 'width="72" height="72"')}
          <span class="word">grip</span></div>
        <h1>Keep a grip<br>on your code.</h1>
        <p>A quiz on your own diff, before it goes upstream.<br>
           In your terminal and inside your coding agent.</p>
        <code>npx skills add guilyx/grip -g</code>
      </section>
      <section class="score">
        ${ring(0.92)}
        <div class="num"><b>92</b><span>/100</span><em>PASS</em></div>
      </section>
    </main>`,
    `body { width: 1200px; height: 630px; background: ${s("graphite-975")}; color: ${s("chalk")}; }
     main { position: relative; height: 100%; display: flex; align-items: center;
            justify-content: space-between; padding: 0 88px; overflow: hidden; }
     .grid { position: absolute; inset: 0; opacity: .55;
             background-image: radial-gradient(${c("graphite", 900)} 1.6px, transparent 1.6px);
             background-size: 28px 28px; }
     .left, .score { position: relative; }
     .brand { display: flex; align-items: center; gap: 18px; margin-bottom: 40px; }
     .brand .word { font-size: 64px; color: ${s("chalk")}; transform: translateY(-4px); }
     h1 { font-family: "Archivo"; font-weight: 900; font-stretch: 112%; font-size: 70px;
          letter-spacing: -0.035em; line-height: 1.02; margin-bottom: 26px; }
     p { font-size: 25px; line-height: 1.45; color: ${c("graphite", 300)}; margin-bottom: 34px; }
     code { font-family: "JetBrains Mono"; font-size: 22px; font-weight: 500;
            color: ${c("ember", 300)}; background: ${c("graphite", 950)};
            border: 1px solid ${c("graphite", 800)}; border-radius: 12px; padding: 14px 20px; }
     .score { width: 300px; height: 300px; }
     .num { position: absolute; inset: 0; display: flex; flex-direction: column;
            align-items: center; justify-content: center; font-family: "Archivo"; font-stretch: 112%; }
     .num b { font-size: 108px; font-weight: 800; letter-spacing: -0.05em; line-height: .9; }
     .num span { font-size: 26px; color: ${c("graphite", 400)}; margin-top: 2px; }
     .num em { font-style: normal; font-family: "JetBrains Mono"; font-weight: 700;
               font-size: 18px; letter-spacing: .2em; color: ${c("jade", 300)}; margin-top: 10px; }`,
  );
}

const jobs = [
  ["lockup-dark.png", lockup(mark, s("chalk"), s("graphite-975")), null],
  ["lockup-light.png", lockup(mark, c("graphite", 950), s("chalk")), null],
  ["lockup-ember.png", lockup(markLight, s("graphite-975"), c("ember", 400)), null],
  ["og-card.png", card(), { width: 1200, height: 630 }],
];

// Fetch remote assets (the fonts) with curl and hand them to the page. curl honours the
// system proxy and CA bundle, which a bundled Chromium may not, and TLS stays verified.
function viaCurl(route) {
  const url = route.request().url();
  try {
    const headers = execFileSync("curl", ["-sSL", "-D", "-", "-o", "/dev/null", "-A", UA, url]).toString();
    const type = /content-type:\s*([^\r\n]+)/i.exec(headers.split(/\r?\n\r?\n/).filter(Boolean).pop())?.[1];
    const body = execFileSync("curl", ["-sSL", "-A", UA, url], { maxBuffer: 1 << 26 });
    return route.fulfill({ status: 200, body, contentType: type, headers: { "access-control-allow-origin": "*" } });
  } catch (err) {
    console.error(`could not fetch ${url}: ${err.message}`);
    return route.abort();
  }
}
const UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140 Safari/537.36";

const browser = await chromium.launch();
for (const [name, html, size] of jobs) {
  const tab = await browser.newPage({ viewport: size ?? { width: 900, height: 400 }, deviceScaleFactor: 2 });
  await tab.route(/^https:\/\//, viaCurl);
  await tab.setContent(html, { waitUntil: "networkidle" });
  await tab.evaluate(() => document.fonts.ready);
  const missing = await tab.evaluate(() => {
    const loaded = new Set([...document.fonts].filter((f) => f.status === "loaded").map((f) => f.family.replaceAll('"', "")));
    return ["Archivo", "Instrument Sans", "JetBrains Mono"].filter((f) => !loaded.has(f));
  });
  if (missing.length) throw new Error(`fonts did not load: ${missing.join(", ")}`);
  const target = size ? tab : tab.locator(".lockup");
  const png = await target.screenshot({ omitBackground: false });
  for (const dir of [join(here, "logo"), join(here, "..", "docs", "assets", "brand")]) {
    mkdirSync(dir, { recursive: true });
    writeFileSync(join(dir, name), png);
  }
  console.log(`wrote ${name}`);
  await tab.close();
}
await browser.close();
