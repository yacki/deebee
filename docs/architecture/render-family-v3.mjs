import { chromium } from '@playwright/test';
import { readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

// Deterministic export from the editable SVG. No image-model generation or upscaling.
const directory = path.dirname(fileURLToPath(import.meta.url));
const basename = process.argv[2] ?? 'agent-security-family-flat-v3';
if (!/^[a-z0-9-]+$/.test(basename)) throw new Error('Expected a local lowercase artifact basename.');
const source = await readFile(path.join(directory, `${basename}.svg`), 'utf8');
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 2400, height: 3584 }, deviceScaleFactor: 1 });
  await page.setContent(`<html><head><meta charset="utf-8"><style>html,body{margin:0;padding:0}svg{display:block}</style></head><body>${source}</body></html>`);
  await page.evaluate(() => document.fonts.ready);
  const report = await page.evaluate(() => {
    const issues = [];
    const texts = [...document.querySelectorAll('svg text')];
    const box = el => {
      const b = el.getBoundingClientRect();
      return { x: b.x, y: b.y, right: b.right, bottom: b.bottom, width: b.width, height: b.height };
    };
    const boxes = texts.map(el => ({ text: el.textContent, ...box(el) }));
    for (const entry of boxes) {
      if (entry.x < 0 || entry.y < 0 || entry.right > 2400 || entry.bottom > 3584) issues.push({ type: 'outside-canvas', ...entry });
    }
    for (const group of document.querySelectorAll('[data-card]')) {
      const rect = group.querySelector(':scope > rect');
      const bounds = box(rect);
      for (const text of group.querySelectorAll('text')) {
        const b = box(text);
        if (b.x < bounds.x + 8 || b.right > bounds.right - 8 || b.y < bounds.y + 3 || b.bottom > bounds.bottom - 3) {
          issues.push({ type: 'card-text-overflow', card: group.dataset.card, text: text.textContent, bounds, box: b });
        }
      }
    }
    for (let a = 0; a < boxes.length; a++) {
      for (let b = a + 1; b < boxes.length; b++) {
        const x = boxes[a], y = boxes[b];
        const overlapWidth = Math.min(x.right, y.right) - Math.max(x.x, y.x);
        const overlapHeight = Math.min(x.bottom, y.bottom) - Math.max(x.y, y.y);
        if (overlapWidth > 1 && overlapHeight > 1) issues.push({ type: 'text-overlap', a: x.text, b: y.text, overlapWidth, overlapHeight });
      }
    }
    const svg = document.querySelector('svg');
    return {
      textElements: texts.length,
      cardGroups: document.querySelectorAll('[data-card]').length,
      rasterImageElements: svg.querySelectorAll('image').length,
      scriptElements: svg.querySelectorAll('script').length,
      baseSize: [2400, 3584],
      exportSize: [4800, 7168],
      issues,
    };
  });
  await writeFile(path.join(directory, `${basename}-qa.json`), JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify(report, null, 2));
  await page.screenshot({ path: path.join(directory, `${basename}-preview.png`), fullPage: true });
  const highResolutionPage = await browser.newPage({ viewport: { width: 2400, height: 3584 }, deviceScaleFactor: 2 });
  await highResolutionPage.setContent(`<html><head><meta charset="utf-8"><style>html,body{margin:0;padding:0}svg{display:block}</style></head><body>${source}</body></html>`);
  await highResolutionPage.evaluate(() => document.fonts.ready);
  await highResolutionPage.screenshot({ path: path.join(directory, `${basename}-4800.png`), fullPage: true });
  // Region exports are for visual QA at readable scale, not extra deliverables.
  for (const [name, clip] of Object.entries({
    upper: { x: 104, y: 236, width: 1768, height: 1008 },
    identity: { x: 104, y: 1280, width: 1768, height: 1000 },
    foundation: { x: 104, y: 2936, width: 2192, height: 626 },
  })) {
    await page.screenshot({ path: path.join(directory, `${basename}-qa-${name}.png`), clip });
  }
  if (report.issues.length) process.exitCode = 1;
} finally {
  await browser.close();
}
