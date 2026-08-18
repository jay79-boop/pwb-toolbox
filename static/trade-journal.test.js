/*
 * Tests for the derived-fields block inside trade-journal.html.
 * Run: node static/trade-journal.test.js
 *
 * The journal is deliberately one self-contained file — it opens from file://
 * with no server and no build step, which is what makes it usable from a synced
 * folder. So its arithmetic cannot be imported; it is sliced out of the HTML
 * between the markers and evaluated here. That keeps the single-file property
 * and the tests, instead of trading one for the other.
 *
 * If this file starts failing with "markers not found", someone renamed or
 * removed the comment banners around the block. They are load-bearing.
 */
"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");

const HTML = fs.readFileSync(
  path.join(__dirname, "trade-journal.html"),
  "utf8"
);

const START = "/* ==== derived-fields block: pure, no DOM";
const END = "/* ==== end derived-fields block";
const i = HTML.indexOf(START);
const j = HTML.indexOf(END);
assert.ok(i !== -1 && j > i, "markers not found in trade-journal.html");
const BLOCK = HTML.slice(i, j);

// A block that reaches for the DOM is not the pure block any more, and would
// pass here only by accident of node not having those globals. Comments are
// stripped first — the banner above the block names document and localStorage
// in order to forbid them, and would otherwise trip its own guard.
const CODE = BLOCK.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
["document", "window", "localStorage", "alert("].forEach((forbidden) => {
  assert.ok(
    !CODE.includes(forbidden),
    "derived-fields block must not reference " + forbidden
  );
});

const J = new Function(
  BLOCK +
    "\nreturn { multOf, pnlSign, costBasisOf, maxLossOf, clampRisk," +
    " breakevenOf, daysBetween, rMultipleOf, MULT, BOUGHT_TO_OPEN, SOLD_TO_OPEN };"
)();

let passed = 0;
const failures = [];

function test(name, fn) {
  try {
    fn();
    passed += 1;
  } catch (err) {
    failures.push(name + "\n    " + err.message.split("\n").join("\n    "));
  }
}

const near = (a, b, eps) =>
  assert.ok(
    a != null && Math.abs(a - b) < (eps == null ? 1e-6 : eps),
    "expected " + a + " to be within " + (eps == null ? 1e-6 : eps) + " of " + b
  );

// A one-lot long call bought at 9.40 — the example the cap exists for.
const CALL = { instrument: "call", direction: "long", entryPrice: 9.4, qty: 1 };
const SHARES = { instrument: "shares", direction: "long", entryPrice: 232, qty: 10 };

// --------------------------------------------------------------- cost basis

test("an option's cost basis carries the 100x multiplier", () => {
  near(J.costBasisOf(CALL), 940);
});

test("shares have no multiplier", () => {
  near(J.costBasisOf(SHARES), 2320);
});

test("a negative quantity still costs what it costs", () => {
  near(J.costBasisOf({ ...SHARES, qty: -10 }), 2320);
});

test("a half-filled draft has no cost basis rather than a wrong one", () => {
  assert.strictEqual(J.costBasisOf({ instrument: "call" }), null);
  assert.strictEqual(J.costBasisOf({ ...CALL, qty: NaN }), null);
});

// ------------------------------------------------------------------ ceiling

test("a long call cannot lose more than the debit paid", () => {
  near(J.maxLossOf(CALL), 940);
});

test("a long put is bought to open even though the view is bearish", () => {
  near(J.maxLossOf({ instrument: "put", direction: "short", entryPrice: 12.5, qty: 2 }), 2500);
});

test("debit spreads are capped at the debit", () => {
  near(J.maxLossOf({ instrument: "call_spread", direction: "long", entryPrice: 2.1, qty: 3 }), 630);
});

test("long shares are capped at the position, going to zero", () => {
  near(J.maxLossOf(SHARES), 2320);
});

test("short shares have no ceiling at all", () => {
  assert.strictEqual(J.maxLossOf({ ...SHARES, direction: "short" }), null);
});

test("a cash-secured put is capped at strike minus the credit, not at the credit", () => {
  const csp = { instrument: "csp", direction: "long", entryPrice: 3, strike: 100, qty: 1 };
  near(J.maxLossOf(csp), 9700);
  assert.notStrictEqual(J.maxLossOf(csp), J.costBasisOf(csp));
});

test("a cash-secured put without a strike admits it cannot say", () => {
  assert.strictEqual(
    J.maxLossOf({ instrument: "csp", direction: "long", entryPrice: 3, qty: 1 }),
    null
  );
});

test("a covered call's ceiling is not the credit — the shares carry the risk", () => {
  assert.strictEqual(
    J.maxLossOf({ instrument: "cc", direction: "long", entryPrice: 2, qty: 1 }),
    null
  );
});

test("'other' is unknown, not unlimited and not zero", () => {
  assert.strictEqual(
    J.maxLossOf({ instrument: "other", direction: "long", entryPrice: 5, qty: 1 }),
    null
  );
});

// -------------------------------------------------------------- the clamp

test("1500 at risk on a 940 call is capped, and says it was", () => {
  const c = J.clampRisk(CALL, 1500);
  near(c.value, 940);
  assert.strictEqual(c.capped, true);
  near(c.ceiling, 940);
});

test("a risk under the ceiling passes through untouched", () => {
  const c = J.clampRisk(CALL, 376);
  near(c.value, 376);
  assert.strictEqual(c.capped, false);
});

test("a risk exactly at the ceiling is not a breach", () => {
  const c = J.clampRisk(CALL, 940);
  near(c.value, 940);
  assert.strictEqual(c.capped, false);
});

test("nothing is capped when nothing bounds the position", () => {
  const c = J.clampRisk({ ...SHARES, direction: "short" }, 999999);
  near(c.value, 999999);
  assert.strictEqual(c.capped, false);
  assert.strictEqual(c.ceiling, null);
});

test("an empty or negative risk yields null rather than a number", () => {
  assert.strictEqual(J.clampRisk(CALL, "").value, null);
  assert.strictEqual(J.clampRisk(CALL, "abc").value, null);
  assert.strictEqual(J.clampRisk(CALL, -50).value, null);
});

test("the cap tracks quantity", () => {
  near(J.clampRisk({ ...CALL, qty: 3 }, 5000).value, 2820);
});

// ------------------------------------------------------------- P&L sign

test("a long put gains when the premium rises, whatever the market view", () => {
  assert.strictEqual(J.pnlSign({ instrument: "put", direction: "short" }), 1);
});

test("sold-to-open positions invert", () => {
  assert.strictEqual(J.pnlSign({ instrument: "csp", direction: "long" }), -1);
  assert.strictEqual(J.pnlSign({ instrument: "cc", direction: "long" }), -1);
});

test("short shares invert", () => {
  assert.strictEqual(J.pnlSign({ instrument: "shares", direction: "short" }), -1);
  assert.strictEqual(J.pnlSign({ instrument: "shares", direction: "long" }), 1);
});

// ------------------------------------------------------------- breakeven

test("a call breaks even above the strike, a put below it", () => {
  near(J.breakevenOf({ instrument: "call", strike: 230, entryPrice: 9.4 }), 239.4);
  near(J.breakevenOf({ instrument: "put", strike: 500, entryPrice: 12.5 }), 487.5);
  near(J.breakevenOf({ instrument: "csp", strike: 100, entryPrice: 3 }), 97);
});

test("spreads and shares get no breakeven, because the form lacks the inputs", () => {
  assert.strictEqual(J.breakevenOf({ instrument: "call_spread", strike: 230, entryPrice: 2 }), null);
  assert.strictEqual(J.breakevenOf({ instrument: "shares", entryPrice: 232 }), null);
});

// ------------------------------------------------------------------ dates

test("DTE counts whole days and survives a timezone west of Greenwich", () => {
  assert.strictEqual(J.daysBetween("2026-08-11", "2026-09-18"), 38);
  assert.strictEqual(J.daysBetween("2026-12-31", "2027-01-01"), 1);
});

test("an expiry before entry reads negative rather than silently absolute", () => {
  assert.strictEqual(J.daysBetween("2026-09-18", "2026-08-11"), -38);
});

test("a missing or malformed date yields null", () => {
  assert.strictEqual(J.daysBetween("", "2026-09-18"), null);
  assert.strictEqual(J.daysBetween("09/18/2026", "2026-09-18"), null);
  assert.strictEqual(J.daysBetween(null, undefined), null);
});

// -------------------------------------------------------------- R multiple

test("R is P&L over what was risked", () => {
  near(J.rMultipleOf({ pnl: 470, riskAmount: 376 }), 1.25);
  near(J.rMultipleOf({ pnl: -940, riskAmount: 940 }), -1);
});

test("R is undefined when nothing was at risk, rather than infinite", () => {
  assert.strictEqual(J.rMultipleOf({ pnl: 100, riskAmount: 0 }), null);
  assert.strictEqual(J.rMultipleOf({ pnl: 100, riskAmount: null }), null);
  assert.strictEqual(J.rMultipleOf({ pnl: null, riskAmount: 940 }), null);
});

// ------------------------------------------------ nothing throws on a draft

test("every function tolerates the empty form the page starts on", () => {
  const empty = { instrument: "shares", direction: "long" };
  [J.costBasisOf, J.maxLossOf, J.breakevenOf].forEach((f) => {
    assert.doesNotThrow(() => f(empty));
    assert.strictEqual(f(empty), null);
  });
  assert.doesNotThrow(() => J.clampRisk(empty, ""));
});

failures.forEach((f) => console.error("FAIL  " + f));
console.log(passed + " passed, " + failures.length + " failed");
process.exit(failures.length ? 1 : 0);
