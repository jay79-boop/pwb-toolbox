/*
 * Tests for the QR encoder inlined in static/karaoke-queue.html.
 *
 *   node static/karaoke-qr.test.js
 *
 * The encoder lives inside the page rather than in a module of its own
 * because the page is embedded whole into the standalone karaoke_os.py, and
 * a second file would have to be inlined by hand -- the trap the journal's
 * inline-verbatim rule exists to avoid. So this suite extracts the encoder
 * from the page and exercises the code that actually ships.
 *
 * The oracle is static/karaoke-qr.fixtures.json: matrices produced by
 * python-qrcode in byte mode at error correction M, every one of which was
 * decoded back to its own URL by OpenCV -- an unrelated implementation --
 * before being written. Matching them means this encoder emits codes a real
 * scanner reads, not merely codes that look like QR.
 *
 * Regenerating the fixtures needs `pip install qrcode opencv-python-headless
 * numpy`; they are committed so the suite needs neither the network nor
 * those packages.
 */
"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");

const PAGE = path.join(__dirname, "karaoke-queue.html");
const FIXTURES = path.join(__dirname, "karaoke-qr.fixtures.json");

const page = fs.readFileSync(PAGE, "utf8");
const start = page.indexOf("  // ==== qr, drawn here");
const end = page.indexOf("  // ==== end qr");
assert.ok(start > -1 && end > start, "the qr encoder markers moved in the page");
// new Function rather than eval: the extracted block declares with var and
// function, which a direct eval in this strict-mode file would keep to
// itself. This hands the three entry points back without touching globals.
const { qrDraw, qrEncode, qrPenalty } = new Function(
  page.slice(start, end) +
    "\nreturn { qrDraw: qrDraw, qrEncode: qrEncode, qrPenalty: qrPenalty };"
)();

const fixtures = JSON.parse(fs.readFileSync(FIXTURES, "utf8")).cases;

let passed = 0;
const failures = [];
function test(name, fn) {
  try { fn(); passed++; }
  catch (e) { failures.push(name + ": " + e.message); }
}
function rows(matrix) {
  return matrix.map(row => row.join(""));
}
function bytesOf(text) {
  return Array.prototype.slice.call(new TextEncoder().encode(text));
}

/* --- against the decoded oracle --- */

fixtures.forEach(c => {
  test(`v${c.version} mask ${c.mask} matches a matrix OpenCV decoded`, () => {
    assert.deepStrictEqual(rows(qrDraw(bytesOf(c.text), c.version, c.mask)), c.rows);
  });
});

test("the comparison can fail -- one flipped module is caught", () => {
  const c = fixtures[0];
  const m = qrDraw(bytesOf(c.text), c.version, c.mask);
  m[8][4] ^= 1;
  assert.notDeepStrictEqual(rows(m), c.rows);
});

/* --- version choice --- */

const CAPACITY = { 1: 14, 2: 26, 3: 42, 4: 62, 5: 84, 6: 106 };

test("the smallest version that fits is the one used", () => {
  Object.keys(CAPACITY).forEach(v => {
    const version = Number(v);
    const full = qrEncode("u".repeat(CAPACITY[version]));
    assert.strictEqual(full.length, version * 4 + 17, `${CAPACITY[version]} bytes`);
  });
});

test("one byte past a version's capacity moves up, never truncates", () => {
  const m = qrEncode("u".repeat(CAPACITY[2] + 1));
  assert.strictEqual(m.length, 3 * 4 + 17);
});

test("longer than any address we serve refuses rather than mangling", () => {
  assert.strictEqual(qrEncode("u".repeat(CAPACITY[6] + 1)), null);
});

/* --- the structure a scanner looks for --- */

test("all three finder patterns are present and square", () => {
  const m = qrEncode("http://192.168.1.50:8772/");
  const n = m.length;
  [[0, 0], [0, n - 7], [n - 7, 0]].forEach(([r0, c0]) => {
    for (let r = 0; r < 7; r++) {
      for (let c = 0; c < 7; c++) {
        const edge = r === 0 || r === 6 || c === 0 || c === 6;
        const core = r >= 2 && r <= 4 && c >= 2 && c <= 4;
        assert.strictEqual(m[r0 + r][c0 + c], edge || core ? 1 : 0, `${r0},${c0} ${r},${c}`);
      }
    }
  });
});

test("the timing patterns alternate", () => {
  const m = qrEncode("http://192.168.1.50:8772/");
  for (let i = 8; i < m.length - 8; i++) {
    assert.strictEqual(m[6][i], i % 2 ? 0 : 1, `row 6 col ${i}`);
    assert.strictEqual(m[i][6], i % 2 ? 0 : 1, `col 6 row ${i}`);
  }
});

test("the dark module is dark -- a scanner rejects the symbol without it", () => {
  const m = qrEncode("http://10.0.0.7:8772/");
  assert.strictEqual(m[m.length - 8][8], 1);
});

/* --- mask choice --- */

test("the chosen mask is one of the eight, and is the lowest-penalty one", () => {
  ["http://192.168.1.50:8772/", "http://10.0.0.7:8772/", "http://a.b/"].forEach(url => {
    const chosen = qrEncode(url);
    const bytes = bytesOf(url);
    const version = (chosen.length - 17) / 4;
    const scores = [];
    for (let mask = 0; mask < 8; mask++) scores.push(qrPenalty(qrDraw(bytes, version, mask)));
    const best = scores.indexOf(Math.min.apply(null, scores));
    assert.deepStrictEqual(rows(chosen), rows(qrDraw(bytes, version, best)), url);
  });
});

/* --- the reason this file exists --- */

test("the page asks no CDN for a QR library", () => {
  assert.ok(page.indexOf("cdnjs") === -1, "a CDN reference came back");
  assert.ok(page.indexOf("qrcode.min.js") === -1, "the CDN QR library came back");
});

test("a multibyte name still encodes, by bytes rather than characters", () => {
  const url = "http://192.168.1.50:8772/?room=café";
  const m = qrEncode(url);
  assert.ok(m, "café refused");
  assert.strictEqual(m.length >= 25, true);
});

failures.forEach(f => console.error("FAIL  " + f));
console.log(passed + " passed, " + failures.length + " failed");
process.exit(failures.length ? 1 : 0);
