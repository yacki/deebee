import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("production build contains the Vue DeeBee application shell", async () => {
  const html = await readFile(new URL("../dist/index.html", import.meta.url), "utf8");
  assert.match(html, /<title>DeeBee — MySQL 数据库工作台<\/title>/i);
  assert.match(html, /<div id="app"><\/div>/);
  assert.match(html, /<script type="module"[^>]+\/assets\/index-[^"']+\.js/);
  assert.doesNotMatch(html, /_next\/|react|vinext|Your site is taking shape/i);
});
