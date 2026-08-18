/*
 * Tests for journal-shots.js.  Run: node static/journal-shots.test.js
 *
 * shrink() needs a canvas and is exercised in a real browser instead. What is
 * covered here is the arithmetic around it, which is where a quiet mistake
 * costs data: a wrong byte count or an off-by-one budget lets a screenshot be
 * accepted that cannot be stored, and localStorage answers that by throwing at
 * save time — after the trade is in memory and looks logged.
 */
"use strict";

const assert = require("assert");
const S = require("./journal-shots.js");

let passed = 0;
const failures = [];

function test(name, fn) {
  try { fn(); passed += 1; }
  catch (err) { failures.push(name + "\n    " + err.message.split("\n").join("\n    ")); }
}

/* ------------------------------------------------------------- fitWithin */

test("a landscape screenshot is capped on its long edge", () => {
  const f = S.fitWithin(3840, 2160, 1280);
  assert.strictEqual(f.width, 1280);
  assert.strictEqual(f.height, 720);
  assert.strictEqual(f.scaled, true);
});

test("a portrait image is capped on its long edge too", () => {
  const f = S.fitWithin(1000, 4000, 1280);
  assert.strictEqual(f.width, 320);
  assert.strictEqual(f.height, 1280);
});

test("aspect ratio survives the round trip", () => {
  [[3840, 2160], [1920, 1080], [2560, 1440], [1366, 768], [3000, 1997]].forEach(([w, h]) => {
    const f = S.fitWithin(w, h, 1280);
    const before = w / h, after = f.width / f.height;
    assert.ok(Math.abs(before - after) / before < 0.002,
      w + "x" + h + " became " + f.width + "x" + f.height);
  });
});

test("a small image is never upscaled", () => {
  const f = S.fitWithin(640, 480, 1280);
  assert.strictEqual(f.width, 640);
  assert.strictEqual(f.height, 480);
  assert.strictEqual(f.scale, 1);
  assert.strictEqual(f.scaled, false);
});

test("dimensions come back as integers, not fractions", () => {
  const f = S.fitWithin(1333, 777, 1000);
  assert.strictEqual(f.width, Math.round(f.width));
  assert.strictEqual(f.height, Math.round(f.height));
});

test("a degenerate image never yields a zero-pixel canvas", () => {
  const f = S.fitWithin(10000, 3, 1280);
  assert.ok(f.height >= 1, "height was " + f.height);
  assert.strictEqual(S.fitWithin(0, 100, 1280), null);
  assert.strictEqual(S.fitWithin(100, -1, 1280), null);
  assert.strictEqual(S.fitWithin("x", "y", 1280), null);
});

/* --------------------------------------------------------- dataUriBytes */

test("base64 payload length converts to decoded bytes", () => {
  // "AAAA" decodes to three bytes; padding removes one each.
  assert.strictEqual(S.dataUriBytes("data:image/jpeg;base64,AAAA"), 3);
  assert.strictEqual(S.dataUriBytes("data:image/jpeg;base64,AAA="), 2);
  assert.strictEqual(S.dataUriBytes("data:image/jpeg;base64,AA=="), 1);
});

test("byte count matches what Buffer decodes, across sizes", () => {
  [1, 2, 3, 17, 100, 4095, 65537].forEach(n => {
    const b64 = Buffer.alloc(n, 7).toString("base64");
    const uri = "data:image/jpeg;base64," + b64;
    assert.strictEqual(S.dataUriBytes(uri), n, "at " + n + " bytes");
  });
});

test("a malformed or empty URI counts as nothing, not NaN", () => {
  [undefined, null, "", "not a uri", "data:image/png"].forEach(v => {
    assert.strictEqual(S.dataUriBytes(v), 0, JSON.stringify(v));
  });
});

/* ---------------------------------------------------------- the budget */

test("storage is measured off the serialized store, not estimated", () => {
  const trades = [{ ticker: "AAPL", shots: ["data:image/jpeg;base64,AAAA"] }];
  assert.strictEqual(S.storageChars(trades), JSON.stringify(trades).length);
  assert.strictEqual(S.storageChars([]), 2);
});

test("a store that cannot be serialized reports zero rather than throwing", () => {
  const loop = { a: 1 };
  loop.self = loop;
  assert.doesNotThrow(() => S.storageChars([loop]));
  assert.strictEqual(S.storageChars([loop]), 0);
});

test("the budget reports used, free and a percentage that agree", () => {
  const b = S.budget([{ x: "y" }], 1000);
  assert.strictEqual(b.used + b.free, 1000);
  assert.strictEqual(b.total, 1000);
  assert.ok(Math.abs(b.pct - b.used / 10) < 1e-9);
});

test("an overfull store reports no free space and caps at 100%", () => {
  const big = [{ blob: "x".repeat(5000) }];
  const b = S.budget(big, 1000);
  assert.strictEqual(b.free, 0);
  assert.strictEqual(b.pct, 100);
});

test("canAccept says yes up to exactly the free space and no past it", () => {
  const trades = [];               // serializes to "[]", two characters
  const free = S.budget(trades, 100).free;
  assert.strictEqual(free, 98);
  assert.strictEqual(S.canAccept(trades, 98, 100).ok, true);
  assert.strictEqual(S.canAccept(trades, 99, 100).ok, false);
  assert.strictEqual(S.canAccept(trades, 99, 100).over, 1);
});

test("canAccept treats junk as needing nothing rather than failing open", () => {
  ["", null, undefined, "abc"].forEach(v => {
    const r = S.canAccept([], v, 100);
    assert.strictEqual(r.need, 0);
    assert.strictEqual(r.ok, true);
  });
});

test("a real screenshot's worth of base64 fits the default budget many times", () => {
  // 90 KB decoded is roughly 120 K characters of base64. The 4 MB budget should
  // hold dozens, which is the whole reason for re-encoding on the way in.
  const chars = Math.ceil(90 * 1024 * 4 / 3);
  const fits = Math.floor(S.BUDGET / chars);
  assert.ok(fits >= 30, "only " + fits + " screenshots would fit");
});

test("an unshrunk 3 MB screenshot would not fit, which is the point", () => {
  const chars = Math.ceil(3 * 1024 * 1024 * 4 / 3);
  assert.strictEqual(S.canAccept([], chars).ok, false);
});

/* ---------------------------------------------------------- formatting */

test("byte sizes read the way a person would say them", () => {
  assert.strictEqual(S.fmtBytes(0), "0 B");
  assert.strictEqual(S.fmtBytes(512), "512 B");
  assert.strictEqual(S.fmtBytes(1024), "1 KB");
  assert.strictEqual(S.fmtBytes(90 * 1024), "90 KB");
  assert.strictEqual(S.fmtBytes(1024 * 1024), "1.0 MB");
  assert.strictEqual(S.fmtBytes(undefined), "0 B");
});

/* ------------------------------------------------------------- contract */

test("the per-image cap leaves the budget room for a useful number of trades", () => {
  assert.ok(S.PER_IMAGE_CAP * 4 / 3 * 20 < S.BUDGET,
    "twenty capped images should not fill the budget");
});

test("quality steps only ever go down", () => {
  for (let i = 1; i < S.QUALITY.length; i++) {
    assert.ok(S.QUALITY[i] < S.QUALITY[i - 1], "step " + i);
  }
  assert.ok(S.MIN_EDGE < S.MAX_EDGE);
});

test("blobsFrom survives an event carrying nothing it wants", () => {
  assert.deepStrictEqual(S.blobsFrom({}), []);
  assert.deepStrictEqual(S.blobsFrom({ clipboardData: {} }), []);
  assert.deepStrictEqual(S.blobsFrom({ dataTransfer: { files: [], items: [] } }), []);
  assert.deepStrictEqual(
    S.blobsFrom({ clipboardData: { files: [{ type: "text/plain" }] } }), []);
});

test("blobsFrom picks images out of a mixed drop", () => {
  const png = { type: "image/png" };
  const got = S.blobsFrom({ dataTransfer: { files: [{ type: "text/csv" }, png] } });
  assert.deepStrictEqual(got, [png]);
});

failures.forEach(f => console.error("FAIL  " + f));
console.log(passed + " passed, " + failures.length + " failed");
process.exit(failures.length ? 1 : 0);
