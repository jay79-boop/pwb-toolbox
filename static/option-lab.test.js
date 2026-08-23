/*
 * Tests for option-lab.js.
 *
 *   node static/option-lab.test.js                 the suite
 *   node static/option-lab.test.js --cross f.json  reconcile against Python
 *
 * The second mode is what tests/test_option_lab.py drives: it generates the
 * same cases through pwb_toolbox.options and hands them over, so the port
 * cannot drift away from the module it was ported from. The suite below covers
 * what Python has no counterpart for — rho, the probabilities, the ladders —
 * and pins those against closed forms and identities rather than against a
 * recorded number, since a recorded number only proves the code still does
 * whatever it did the day it was written.
 */
"use strict";

const assert = require("assert");
const fs = require("fs");
const T = require("./option-lab.js");

/* ------------------------------------------------ cross-check mode */

const crossAt = process.argv.indexOf("--cross");
if (crossAt !== -1) {
  const spec = JSON.parse(fs.readFileSync(process.argv[crossAt + 1], "utf8"));
  const tol = spec.tolerance || 1e-9;
  let worst = 0, worstAt = "";
  let checked = 0;
  const bad = [];

  spec.cases.forEach((c, i) => {
    const got = c.fn === "decaySchedule"
      ? T.decaySchedule.apply(null, c.args).map(r => [r.dte, r.extrinsic, r.pctOfToday])
      : c.fn === "blackScholes"
        ? (() => { const g = T.blackScholes.apply(null, c.args);
                   return c.fields.map(f => g[f]); })()
        : T[c.fn].apply(null, c.args);

    const flat = x => Array.isArray(x) ? x.flat(9) : [x];
    const a = flat(got), b = flat(c.expected);
    if (a.length !== b.length) {
      bad.push(c.fn + "[" + i + "] shape " + a.length + " vs " + b.length);
      return;
    }
    a.forEach((v, k) => {
      checked += 1;
      const e = Math.abs(v - b[k]);
      if (e > worst) { worst = e; worstAt = c.fn; }
      if (!(e <= tol)) bad.push(c.fn + "[" + i + "][" + k + "] " + v + " vs " + b[k]);
    });
  });

  bad.slice(0, 20).forEach(m => console.error("FAIL  " + m));
  console.log(
    checked + " values reconciled against Python, worst error " +
    worst.toExponential(2) + " (" + worstAt + "), " + bad.length + " failed"
  );
  process.exit(bad.length ? 1 : 0);
}

/* ------------------------------------------------------- the suite */

let passed = 0;
const failures = [];

function test(name, fn) {
  try { fn(); passed += 1; }
  catch (err) { failures.push(name + "\n    " + err.message.split("\n").join("\n    ")); }
}

const near = (a, b, eps) =>
  assert.ok(
    a != null && Math.abs(a - b) < (eps == null ? 1e-9 : eps),
    "expected " + a + " to be within " + (eps == null ? 1e-9 : eps) + " of " + b
  );

// The running example throughout: AAPL 230 call, 38 DTE, 28% vol, paid 9.40.
const POS = {
  spot: 232, strike: 230, days: 38, vol: 0.28, rate: 0.045, kind: "call",
  premium: 9.4, contracts: 1, multiplier: 100, risk: 376,
};

/* ------------------------------------------------------ normal dist */

test("normCdf is exact at the points where the answer is known", () => {
  near(T.normCdf(0), 0.5);
  near(T.normCdf(-1.959963984540054), 0.025, 1e-12);
  near(T.normCdf(1.959963984540054), 0.975, 1e-12);
  near(T.normCdf(-40), 0);
  near(T.normCdf(40), 1);
});

test("normCdf is symmetric to machine precision", () => {
  [0.1, 0.5, 1, 2, 3.5, 6, 8, 12].forEach(x => {
    near(T.normCdf(x) + T.normCdf(-x), 1, 1e-15);
  });
});

test("normPdf integrates to the density it should", () => {
  near(T.normPdf(0), 1 / Math.sqrt(2 * Math.PI), 1e-15);
  near(T.normPdf(1), Math.exp(-0.5) / Math.sqrt(2 * Math.PI), 1e-15);
});

/* --------------------------------------------------------- pricing */

test("put-call parity holds", () => {
  const c = T.blackScholes(232, 230, 38, 0.28, 0.045, "call");
  const p = T.blackScholes(232, 230, 38, 0.28, 0.045, "put");
  const discount = Math.exp(-0.045 * 38 / 365);
  near(c.price - p.price, 232 - 230 * discount, 1e-12);
});

test("call delta minus put delta is exactly one", () => {
  const c = T.blackScholes(232, 230, 38, 0.28, 0.045, "call");
  const p = T.blackScholes(232, 230, 38, 0.28, 0.045, "put");
  near(c.delta - p.delta, 1, 1e-15);
});

test("gamma and vega are the same for a call and its put", () => {
  const c = T.blackScholes(232, 230, 38, 0.28, 0.045, "call");
  const p = T.blackScholes(232, 230, 38, 0.28, 0.045, "put");
  near(c.gamma, p.gamma, 1e-15);
  near(c.vega, p.vega, 1e-15);
});

test("delta matches a numerical derivative of price", () => {
  const h = 1e-5;
  const up = T.blackScholes(232 + h, 230, 38, 0.28, 0.045, "call").price;
  const dn = T.blackScholes(232 - h, 230, 38, 0.28, 0.045, "call").price;
  const g = T.blackScholes(232, 230, 38, 0.28, 0.045, "call");
  near((up - dn) / (2 * h), g.delta, 1e-7);
});

test("gamma matches a numerical second derivative of price", () => {
  const h = 1e-3;
  const up = T.blackScholes(232 + h, 230, 38, 0.28, 0.045, "call").price;
  const mid = T.blackScholes(232, 230, 38, 0.28, 0.045, "call").price;
  const dn = T.blackScholes(232 - h, 230, 38, 0.28, 0.045, "call").price;
  const g = T.blackScholes(232, 230, 38, 0.28, 0.045, "call");
  near((up - 2 * mid + dn) / (h * h), g.gamma, 1e-6);
});

test("theta matches the price lost over one calendar day", () => {
  const now = T.blackScholes(232, 230, 38, 0.28, 0.045, "call");
  const tomorrow = T.blackScholes(232, 230, 37, 0.28, 0.045, "call");
  // theta is the instantaneous rate, so it slightly overstates one whole day
  near(tomorrow.price - now.price, now.theta, 5e-3);
});

test("vega is per one point of IV, not per unit", () => {
  const g = T.blackScholes(232, 230, 38, 0.28, 0.045, "call");
  const up = T.blackScholes(232, 230, 38, 0.29, 0.045, "call");
  near(up.price - g.price, g.vega, 1e-3);
});

test("rho is per one point of rate, and signed by kind", () => {
  const c = T.blackScholes(232, 230, 38, 0.28, 0.045, "call");
  const cUp = T.blackScholes(232, 230, 38, 0.28, 0.055, "call");
  near(cUp.price - c.price, c.rho, 1e-3);
  const p = T.blackScholes(232, 230, 38, 0.28, 0.045, "put");
  assert.ok(c.rho > 0, "a call gains when rates rise");
  assert.ok(p.rho < 0, "a put loses when rates rise");
});

test("intrinsic and extrinsic add back to the price", () => {
  [["call", 232], ["put", 232], ["call", 300], ["put", 150]].forEach(([kind, spot]) => {
    const g = T.blackScholes(spot, 230, 38, 0.28, 0.045, kind);
    near(g.intrinsic + g.extrinsic, g.price, 1e-12);
  });
});

test("a deep in-the-money put prices under intrinsic, and that is not a bug", () => {
  // No early exercise in a European model, so the strike is collected late and
  // discounted. Documented in greeks.py; pinned here so nobody "fixes" it.
  const g = T.blackScholes(100, 300, 700, 0.15, 0.045, "put");
  assert.ok(g.extrinsic < 0, "expected negative extrinsic, got " + g.extrinsic);
  assert.ok(g.theta > 0, "expected positive theta, got " + g.theta);
});

test("unpriceable inputs return null rather than throwing", () => {
  [
    [232, 230, 0, 0.28, 0.045, "call"],
    [232, 230, -5, 0.28, 0.045, "call"],
    [0, 230, 38, 0.28, 0.045, "call"],
    [232, 0, 38, 0.28, 0.045, "call"],
    [232, 230, 38, 0, 0.045, "call"],
    [232, 230, 38, 0.28, 0.045, "straddle"],
  ].forEach(args => {
    assert.doesNotThrow(() => T.blackScholes.apply(null, args));
    assert.strictEqual(T.blackScholes.apply(null, args), null, JSON.stringify(args));
  });
});

/* ----------------------------------------------------- implied vol */

test("implied vol round-trips the price it was solved from", () => {
  [0.12, 0.28, 0.65, 1.4].forEach(vol => {
    const g = T.blackScholes(232, 230, 38, vol, 0.045, "call");
    near(T.impliedVol(g.price, 232, 230, 38, 0.045, "call"), vol, 1e-8);
  });
});

test("a price no volatility can produce returns null", () => {
  assert.strictEqual(T.impliedVol(500, 232, 230, 38, 0.045, "call"), null);
  assert.strictEqual(T.impliedVol(0, 232, 230, 38, 0.045, "call"), null);
});

/* -------------------------------------------------- probabilities */

test("at-the-money finish probability is near a half", () => {
  const p = T.finishProbability(232, 232, 0.28, 38, 0.045);
  assert.ok(p > 0.45 && p < 0.55, "got " + p);
});

test("finish probability falls as the target gets further away", () => {
  let prev = 1;
  [235, 240, 250, 275, 300].forEach(target => {
    const p = T.finishProbability(232, target, 0.28, 38, 0.045);
    assert.ok(p < prev, target + ": " + p + " should be under " + prev);
    prev = p;
  });
});

test("touch probability is twice the finish probability, and never over one", () => {
  // The reflection identity, stated without drift. Checked against a directly
  // computed driftless finish probability rather than the risk-neutral one.
  const spot = 232, target = 250, vol = 0.28, days = 38;
  const d = Math.abs(Math.log(target / spot)) / (vol * Math.sqrt(days / 365));
  near(T.touchProbability(spot, target, vol, days), 2 * T.normCdf(-d), 1e-15);
  // A target a hundredth of a cent away is all but certain to be touched, but
  // not exactly certain — the cap only binds for a target at spot itself.
  near(T.touchProbability(232, 232.0001, 0.28, 38), 1, 1e-5);
  assert.ok(T.touchProbability(232, 232.0001, 0.28, 38) < 1);
  assert.strictEqual(T.touchProbability(232, 232, 0.28, 38), 1);
  // Distance is what makes a level unreachable, not the clock alone: a target
  // 0.0001 away is hit even in a minute, while 300 is not.
  assert.ok(T.touchProbability(232, 300, 0.28, 0.01) < 1e-6,
            "a far target in almost no time should be all but impossible");
});

test("touch probability is symmetric in log space, up or down", () => {
  const up = T.touchProbability(100, 110, 0.3, 30);
  const dn = T.touchProbability(100, 100 / 1.1, 0.3, 30);
  near(up, dn, 1e-15);
});

test("probabilities are null, not zero, when the inputs cannot support one", () => {
  assert.strictEqual(T.touchProbability(232, 250, 0.28, 0), null);
  assert.strictEqual(T.finishProbability(232, 250, 0.28, -1, 0.045), null);
  assert.strictEqual(T.finishProbability(0, 250, 0.28, 38, 0.045), null);
});

/* ------------------------------------------------------ move ladder */

test("the move ladder is centred on no move at all", () => {
  const rows = T.moveLadder(POS, { stepPct: 1, maxPct: 5 });
  assert.strictEqual(rows.length, 11);
  const flat = rows.find(r => Math.abs(r.movePct) < 1e-9);
  near(flat.spot, 232);
  near(flat.pTouch, 1);
  const g = T.blackScholes(232, 230, 38, 0.28, 0.045, "call");
  near(flat.premium, g.price, 1e-12);
});

test("a move ladder walks the curve, so it beats a delta approximation", () => {
  const rows = T.moveLadder(POS, { stepPct: 10, maxPct: 10 });
  const up = rows.find(r => Math.abs(r.movePct - 10) < 1e-9);
  const g = T.blackScholes(232, 230, 38, 0.28, 0.045, "call");
  const linear = g.price + g.delta * 232 * 0.1;
  // Gamma is positive, so the real price is above the straight-line guess.
  assert.ok(up.premium > linear, up.premium + " should exceed " + linear);
});

test("ladder P&L uses the contract multiplier and the size", () => {
  const one = T.moveLadder(POS, { stepPct: 5, maxPct: 5 });
  const five = T.moveLadder({ ...POS, contracts: 5 }, { stepPct: 5, maxPct: 5 });
  near(five[0].pnl, one[0].pnl * 5, 1e-9);
  const row = one.find(r => Math.abs(r.movePct - 5) < 1e-9);
  near(row.pnl, (row.premium - 9.4) * 100, 1e-9);
});

test("holding time is priced into the ladder, not just the move", () => {
  const now = T.moveLadder(POS, { stepPct: 5, maxPct: 5, holdDays: 0 });
  const later = T.moveLadder(POS, { stepPct: 5, maxPct: 5, holdDays: 14 });
  const pick = rows => rows.find(r => Math.abs(r.movePct) < 1e-9).premium;
  assert.ok(pick(later) < pick(now), "two weeks of decay should cost something");
});

test("a ladder past expiry falls back to intrinsic instead of blank", () => {
  const rows = T.moveLadder(POS, { stepPct: 10, maxPct: 10, holdDays: 38 });
  const up = rows.find(r => Math.abs(r.movePct - 10) < 1e-9);
  near(up.premium, Math.max(0, 232 * 1.1 - 230), 1e-9);
  const dn = rows.find(r => Math.abs(r.movePct + 10) < 1e-9);
  near(dn.premium, 0);
  near(dn.pnl, -940);
});

/* ---------------------------------------------------- profit ladder */

test("each profit rung reprices to exactly the premium it names", () => {
  const rows = T.profitLadder(POS, { stepPct: 25, maxPct: 100 });
  assert.deepStrictEqual(rows.map(r => r.gainPct), [25, 50, 75, 100]);
  rows.forEach(r => {
    near(r.sellPremium, 9.4 * (1 + r.gainPct / 100), 1e-12);
    const g = T.blackScholes(r.spotNeeded, 230, 38, 0.28, 0.045, "call");
    near(g.price, r.sellPremium, 1e-6);
  });
});

test("a bigger gain needs a bigger move and is less likely to happen", () => {
  const rows = T.profitLadder(POS, { stepPct: 25, maxPct: 100 });
  for (let i = 1; i < rows.length; i++) {
    assert.ok(rows[i].spotNeeded > rows[i - 1].spotNeeded, "spot should rise");
    assert.ok(rows[i].pTouch < rows[i - 1].pTouch, "probability should fall");
    assert.ok(rows[i].pTouch >= rows[i].pFinish, "touching is easier than finishing");
  }
});

test("R multiples come off the risk recorded, not off the cost", () => {
  const rows = T.profitLadder(POS, { stepPct: 100, maxPct: 100 });
  near(rows[0].gain, 940);
  near(rows[0].rMultiple, 940 / 376);
  const noRisk = T.profitLadder({ ...POS, risk: 0 }, { stepPct: 100, maxPct: 100 });
  assert.strictEqual(noRisk[0].rMultiple, null);
});

test("the scale-out column says how much to sell to bank the cost basis", () => {
  const rows = T.profitLadder({ ...POS, contracts: 4 }, { stepPct: 100, maxPct: 100 });
  // Cost 3,760 on four lots. At double, each lot is worth 1,880, so two of
  // them return the entire cost and the other two ride for free.
  assert.strictEqual(rows[0].contractsToRecoverCost, 2);
});

test("scale-out is whole contracts, capped at the position", () => {
  // Half a contract is not a trade. On a one-lot at a 10% gain the only way to
  // bank the cost is to sell the whole thing, and the column has to say so
  // rather than reporting 0.91.
  const rows = T.profitLadder(POS, { stepPct: 10, maxPct: 10 });
  assert.strictEqual(rows[0].contractsToRecoverCost, 1);
  T.profitLadder({ ...POS, contracts: 7 }, { stepPct: 10, maxPct: 200 }).forEach(r => {
    assert.strictEqual(r.contractsToRecoverCost, Math.round(r.contractsToRecoverCost),
      "expected a whole number, got " + r.contractsToRecoverCost);
    assert.ok(r.contractsToRecoverCost <= 7);
  });
});

test("the centre of the move ladder is exactly flat, not picodollars off", () => {
  // Only when the volatility was solved out of the premium, which is what the
  // journal does — then the model reprices the premium to within 1e-11, and
  // that residue used to format as "-$0.00".
  const vol = T.impliedVol(9.4, 232, 230, 38, 0.045, "call");
  const rows = T.moveLadder({ ...POS, vol: vol }, { stepPct: 1, maxPct: 2 });
  const flat = rows.find(r => Math.abs(r.movePct) < 1e-9);
  assert.strictEqual(flat.pnl, 0);
  assert.strictEqual(flat.premiumPct, 0);
  // A vol that genuinely disagrees with the premium must still show the gap.
  const off = T.moveLadder(POS, { stepPct: 1, maxPct: 2 })
    .find(r => Math.abs(r.movePct) < 1e-9);
  assert.ok(Math.abs(off.pnl) > 1, "a real mismatch must not be rounded away");
});

/* ----------------------------------------------------- decay strip */

test("the decay strip costs more the longer you wait", () => {
  const strip = T.decayStrip(POS);
  assert.ok(strip.length >= 4);
  for (let i = 1; i < strip.length; i++) {
    assert.ok(strip[i].lost > strip[i - 1].lost, strip[i].label);
  }
  assert.ok(strip[0].lost > 0, "an hour should still cost something");
});

test("an hour of decay is roughly a twenty-fourth of a day's", () => {
  const strip = T.decayStrip(POS);
  const hour = strip.find(r => r.label === "1 hour");
  const day = strip.find(r => r.label === "1 session");
  const ratio = day.lost / hour.lost;
  assert.ok(ratio > 20 && ratio < 26, "ratio was " + ratio);
});

test("the strip drops horizons past expiry rather than pricing nonsense", () => {
  const strip = T.decayStrip({ ...POS, days: 2 });
  assert.deepStrictEqual(strip.map(r => r.label), ["1 hour", "1 session"]);
});

test("decay is scaled by position size", () => {
  const one = T.decayStrip(POS);
  const five = T.decayStrip({ ...POS, contracts: 5 });
  near(five[0].lost, one[0].lost * 5, 1e-9);
});

/* --------------------------------------------------------- hurdle */

test("a short-dated contract rents time more expensively", () => {
  const near7 = T.hurdleRatio(232, 230, 7, 0.28, 0.045, "call");
  const far45 = T.hurdleRatio(232, 230, 45, 0.28, 0.045, "call");
  assert.ok(near7 > far45, near7 + " should exceed " + far45);
});


/* ---------------------------------------------------- attribution */

test("attribution's total is exact and its parts reconcile", () => {
  const pos = { spot: 640, strike: 640, days: 1, vol: 0.18, rate: 0.045, kind: "call", contracts: 1 };
  const a = T.attribution(pos, { movePct: 0.5, minutes: 30, ivChange: -1 });
  const g0 = T.blackScholes(640, 640, 1, 0.18, 0.045, "call");
  const g1 = T.blackScholes(640 * 1.005, 640, 1 - 30 / 1440, 0.17, 0.045, "call");
  near(a.total, (g1.price - g0.price) * 100, 1e-9);
  near(a.total, a.delta + a.gamma + a.theta + a.vega + a.residual, 1e-9);
});

test("small scenarios leave only a small residual", () => {
  const pos = { spot: 640, strike: 640, days: 2, vol: 0.18, rate: 0.045, kind: "call", contracts: 1 };
  const a = T.attribution(pos, { movePct: 0.25, minutes: 15, ivChange: 0 });
  assert.ok(Math.abs(a.residual) < Math.abs(a.total) * 0.05 + 0.01,
    "residual " + a.residual + " vs total " + a.total);
});

test("each greek's sign tells its story for a long call", () => {
  const pos = { spot: 640, strike: 640, days: 1, vol: 0.18, rate: 0.045, kind: "call", contracts: 1 };
  const a = T.attribution(pos, { movePct: 1, minutes: 60, ivChange: -2 });
  assert.ok(a.delta > 0, "up-move pays delta");
  assert.ok(a.gamma > 0, "gamma always adds on a move");
  assert.ok(a.theta < 0, "time always costs a long");
  assert.ok(a.vega < 0, "an IV drop costs a long");
});

test("attribution scales with position size", () => {
  const pos = { spot: 640, strike: 640, days: 1, vol: 0.18, rate: 0.045, kind: "call", contracts: 1 };
  const one = T.attribution(pos, { movePct: 0.5, minutes: 30, ivChange: 0 });
  const five = T.attribution({ ...pos, contracts: 5 }, { movePct: 0.5, minutes: 30, ivChange: 0 });
  near(five.total, one.total * 5, 1e-9);
});

test("a scenario past expiry settles to intrinsic instead of erroring", () => {
  const pos = { spot: 640, strike: 630, days: 0.2, vol: 0.18, rate: 0.045, kind: "call", contracts: 1 };
  const a = T.attribution(pos, { movePct: 0, minutes: 600, ivChange: 0 });
  near(a.endPremium, 10, 1e-9);
});

failures.forEach(f => console.error("FAIL  " + f));
console.log(passed + " passed, " + failures.length + " failed");
process.exit(failures.length ? 1 : 0);
