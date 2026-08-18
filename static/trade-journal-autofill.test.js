/*
 * Tests for trade-journal-autofill.js.  Run: node static/trade-journal-autofill.test.js
 *
 * Kept in plain node with no test framework so it runs anywhere node does, and
 * so tests/test_trade_journal_autofill.py can shell out to it under pytest.
 */
"use strict";

const assert = require("assert");
const T = require("./trade-journal-autofill.js");

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
    Math.abs(a - b) < (eps == null ? 1e-6 : eps),
    "expected " + a + " to be within " + (eps == null ? 1e-6 : eps) + " of " + b
  );

// ------------------------------------------------------------------ symbols

test("Schwab option symbol carries a 100x multiplier", () => {
  const s = T.parseSymbol("AAPL 09/18/2026 230.00 C");
  assert.strictEqual(s.instrument, "option");
  assert.strictEqual(s.multiplier, 100);
  assert.strictEqual(s.expiry, "2026-09-18");
  assert.strictEqual(s.kind, "call");
  assert.strictEqual(s.strike, 230);
});

test("OCC option symbol parses, strike divided by 1000", () => {
  const s = T.parseSymbol("AAPL  260918C00230000");
  assert.strictEqual(s.underlying, "AAPL");
  assert.strictEqual(s.expiry, "2026-09-18");
  assert.strictEqual(s.strike, 230);
  assert.strictEqual(s.multiplier, 100);
});

test("ISO-style option symbol parses", () => {
  const s = T.parseSymbol("SPY 2026-12-19 500 P");
  assert.strictEqual(s.kind, "put");
  assert.strictEqual(s.strike, 500);
  assert.strictEqual(s.expiry, "2026-12-19");
});

test("bare ticker is stock with multiplier 1", () => {
  const s = T.parseSymbol("aapl");
  assert.strictEqual(s.instrument, "stock");
  assert.strictEqual(s.multiplier, 1);
  assert.strictEqual(s.underlying, "AAPL");
});

test("unparseable symbol returns null rather than guessing", () => {
  assert.strictEqual(T.parseSymbol("what is this"), null);
  assert.strictEqual(T.parseSymbol(""), null);
});

// -------------------------------------------------------------- the clamp

test("typed risk above the debit paid is clamped to the debit", () => {
  const d = T.derive({
    symbol: "AAPL 09/18/2026 230.00 C",
    entry: 9.4,
    qty: 1,
    riskDollars: 1500,
  });
  near(d.costBasis, 940);
  near(d.maxLoss, 940);
  near(d.riskDollars, 940);
  assert.strictEqual(d.clamps.length, 1);
  assert.strictEqual(d.clamps[0].field, "riskDollars");
  near(d.clamps[0].given, 1500);
  near(d.clamps[0].used, 940);
});

test("typed risk below the debit is left alone", () => {
  const d = T.derive({
    symbol: "AAPL 09/18/2026 230.00 C",
    entry: 9.4,
    qty: 1,
    riskDollars: 376,
  });
  near(d.riskDollars, 376);
  assert.deepStrictEqual(d.clamps, []);
});

test("risk exactly equal to the debit is not treated as a breach", () => {
  const d = T.derive({ symbol: "AAPL", entry: 10, qty: 100, riskDollars: 1000 });
  near(d.riskDollars, 1000);
  assert.deepStrictEqual(d.clamps, []);
});

test("shares get the same cap, without the 100x", () => {
  const d = T.derive({ symbol: "AAPL", entry: 232, qty: 10, riskDollars: 5000 });
  near(d.costBasis, 2320);
  near(d.riskDollars, 2320);
  assert.strictEqual(d.clamps.length, 1);
});

test("a short position reports unbounded loss instead of a comfortable number", () => {
  const d = T.derive({
    symbol: "AAPL 09/18/2026 230.00 C",
    side: "short",
    entry: 9.4,
    qty: 1,
    riskDollars: 100000,
  });
  assert.strictEqual(d.maxLossUnbounded, true);
  assert.strictEqual(d.maxLoss, null);
  near(d.riskDollars, 100000); // not clamped — there is no floor to clamp to
  assert.strictEqual(T.format("maxLoss", d.maxLoss, d), "unbounded");
  assert.ok(/not bounded/.test(d.notes.join(" ")));
});

test("a stop at or above entry is rejected and falls back to the house stop", () => {
  const d = T.derive({ symbol: "AAPL", entry: 100, qty: 10, stopPrice: 120 });
  near(d.stopPrice, 60);
  assert.strictEqual(d.clamps[0].field, "stopPrice");
  near(d.riskDollars, 400);
});

test("a negative stop is floored at zero, and the risk is then the whole debit", () => {
  const d = T.derive({
    symbol: "AAPL 09/18/2026 230.00 C",
    entry: 9.4,
    qty: 2,
    stopPrice: -5,
  });
  near(d.stopPrice, 0);
  near(d.riskDollars, 1880);
  near(d.maxLoss, 1880);
});

test("a typed risk backfills the stop it implies", () => {
  const d = T.derive({ symbol: "AAPL", entry: 100, qty: 10, riskDollars: 250 });
  near(d.stopPrice, 75); // 250 / (10 shares) = 25 per share off a 100 entry
  near(d.riskDollars, 250);
});

test("a clamped risk backfills a stop of zero, not a negative price", () => {
  const d = T.derive({
    symbol: "AAPL 09/18/2026 230.00 C",
    entry: 9.4,
    qty: 1,
    riskDollars: 1500,
  });
  near(d.stopPrice, 0);
  near(d.riskDollars, 940);
});

// -------------------------------------------------------------- autofill

test("house exits match tools/trade_card.py", () => {
  assert.strictEqual(T.STOP_PCT, 40);
  assert.strictEqual(T.SCALE_OUT_PCT, 50);
  assert.strictEqual(T.EXIT_DTE, 21);
  const d = T.derive({ symbol: "AAPL 09/18/2026 230.00 C", entry: 9.4, qty: 1 });
  near(d.stopPrice, 5.64);
  near(d.scaleOutPrice, 14.1);
  near(d.riskDollars, 376); // (9.40 - 5.64) * 100
});

test("breakeven follows the option's own geometry", () => {
  const call = T.derive({ symbol: "AAPL 09/18/2026 230.00 C", entry: 9.4, qty: 1 });
  near(call.breakeven, 239.4);
  const put = T.derive({ symbol: "SPY 2026-12-19 500 P", entry: 12.5, qty: 1 });
  near(put.breakeven, 487.5);
  const stock = T.derive({ symbol: "AAPL", entry: 232, qty: 10 });
  near(stock.breakeven, 232);
});

test("DTE and the 21-DTE hard exit come off the symbol and entry date", () => {
  const d = T.derive({
    symbol: "AAPL 09/18/2026 230.00 C",
    entry: 9.4,
    qty: 1,
    entryDate: "2026-08-11",
  });
  assert.strictEqual(d.dte, 38);
  assert.strictEqual(d.hardExitDate, "2026-08-28");
  assert.deepStrictEqual(d.notes, []); // 38 DTE sits inside the 30-45 window
});

test("DTE outside the 30-45 window is called out", () => {
  const d = T.derive({
    symbol: "AAPL 09/18/2026 230.00 C",
    entry: 9.4,
    qty: 1,
    entryDate: "2026-09-08",
  });
  assert.strictEqual(d.dte, 10);
  assert.ok(/outside the 30-45 window/.test(d.notes.join(" ")));
});

test("position size against the account is computed and policed", () => {
  const d = T.derive({
    symbol: "AAPL 09/18/2026 230.00 C",
    entry: 9.4,
    qty: 1,
    account: 20000,
  });
  near(d.pctAccount, 4.7, 1e-9);
  near(d.riskPctAccount, 1.88, 1e-9);
  assert.ok(/over the 4% cap/.test(d.notes.join(" ")));
});

test("suggested quantity falls out of a risk budget and the stop", () => {
  const d = T.derive({
    symbol: "AAPL 09/18/2026 230.00 C",
    entry: 9.4,
    qty: 1,
    account: 20000,
    riskBudgetPct: 2,
  });
  // 2% of 20,000 = 400 budget; each contract risks (9.40-5.64)*100 = 376.
  assert.strictEqual(d.suggestedQty, 1);
});

test("reward and reward:risk default to the +50% scale-out", () => {
  const d = T.derive({ symbol: "AAPL 09/18/2026 230.00 C", entry: 9.4, qty: 1 });
  near(d.targetPrice, 14.1);
  near(d.rewardDollars, 470);
  near(d.rewardToRisk, 470 / 376);
});

test("an explicit target overrides the default", () => {
  const d = T.derive({
    symbol: "AAPL", entry: 100, qty: 10, targetPrice: 130, stopPrice: 90,
  });
  near(d.rewardDollars, 300);
  near(d.riskDollars, 100);
  near(d.rewardToRisk, 3);
});

// ------------------------------------------------------------- the close

test("closing a winner yields P/L, percent, R and hold time", () => {
  const d = T.derive({
    symbol: "AAPL 09/18/2026 230.00 C",
    entry: 9.4,
    qty: 1,
    exitPrice: 14.1,
    entryDate: "2026-08-11",
    exitDate: "2026-08-25",
    fees: 1.3,
  });
  near(d.pnl, 468.7);
  near(d.pnlPct, (468.7 / 940) * 100);
  near(d.rMultiple, 468.7 / 376);
  assert.strictEqual(d.holdDays, 14);
});

test("a total loss is exactly -1R when the stop is the whole debit", () => {
  const d = T.derive({
    symbol: "AAPL 09/18/2026 230.00 C",
    entry: 9.4, qty: 1, stopPrice: 0, exitPrice: 0,
  });
  near(d.rMultiple, -1);
  near(d.pnl, -940);
});

test("a short's P/L runs the other way", () => {
  const d = T.derive({
    symbol: "AAPL", side: "short", entry: 100, qty: 10, exitPrice: 90,
  });
  near(d.pnl, 100);
});

// ------------------------------------------------------------ input hygiene

test("pasted money formats are read, not rejected", () => {
  const d = T.derive({ symbol: "AAPL", entry: "$9.40", qty: "1,000" });
  near(d.costBasis, 9400);
});

test("a half-filled form derives what it can and nulls the rest", () => {
  const d = T.derive({ symbol: "AAPL 09/18/2026 230.00 C" });
  assert.strictEqual(d.instrument, "option");
  assert.strictEqual(d.multiplier, 100);
  assert.strictEqual(d.costBasis, null);
  assert.strictEqual(d.riskDollars, null);
  assert.deepStrictEqual(d.clamps, []);
});

test("derive never throws on garbage", () => {
  [undefined, null, {}, { symbol: 42, entry: "abc", qty: {} }].forEach((bad) => {
    assert.doesNotThrow(() => T.derive(bad));
  });
});

test("an explicit multiplier overrides the one read from the symbol", () => {
  const d = T.derive({ symbol: "/ES", entry: 5, qty: 1, multiplier: 50 });
  near(d.costBasis, 250);
});

test("money formatting groups thousands and keeps the sign", () => {
  assert.strictEqual(T.fmtMoney(1234.5), "$1,234.50");
  assert.strictEqual(T.fmtMoney(-940), "-$940.00");
  assert.strictEqual(T.fmtMoney(null), "");
});

// ----------------------------------------------------------------- report

failures.forEach((f) => console.error("FAIL  " + f));
console.log(passed + " passed, " + failures.length + " failed");
process.exit(failures.length ? 1 : 0);
