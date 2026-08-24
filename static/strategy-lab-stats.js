/* Strategy Lab — the arithmetic behind the dashboard.
 *
 * Pure functions over a run record's trade list. No DOM, no dependencies, so
 * node can test it and `strategy-lab.html` can inline it verbatim — the same
 * arrangement `option-lab.js` has with the trade journal. Edit here, run the
 * tests, then re-inline; never patch the inlined copy.
 *
 * Everything the dashboard shows is derived from `trades` by these functions, so
 * a run record cannot carry a headline number that disagrees with its own rows.
 *
 * A trade is {day, direction, entry_ts, entry, exit_ts, exit, target, stop,
 * reason, r, points}. `r` is the R-multiple; `direction` is 1 long / -1 short.
 */

(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.StrategyLabStats = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const num = (x) => (typeof x === "number" && isFinite(x) ? x : 0);

  /** Cumulative R after each trade, with the running peak and drawdown.
   *
   * Drawdown is measured in R off the running peak of the cumulative curve, so
   * it is comparable between runs of different size and different instruments —
   * unlike a dollar drawdown, which mostly measures position sizing.
   */
  function equityCurve(trades) {
    let cum = 0;
    let peak = 0;
    return trades.map((t, i) => {
      cum += num(t.r);
      if (cum > peak) peak = cum;
      return {
        i: i + 1,
        day: t.day,
        r: num(t.r),
        cum: cum,
        peak: peak,
        drawdown: cum - peak,
      };
    });
  }

  /** Longest run of consecutive trades satisfying `pred`. */
  function longestStreak(trades, pred) {
    let best = 0;
    let run = 0;
    for (const t of trades) {
      if (pred(t)) {
        run += 1;
        if (run > best) best = run;
      } else {
        run = 0;
      }
    }
    return best;
  }

  const isWin = (t) => num(t.r) > 0;
  const isLoss = (t) => num(t.r) < 0;

  /** Headline statistics. Every field is derived from the trade rows. */
  function summarize(trades) {
    const n = trades.length;
    const wins = trades.filter(isWin);
    const losses = trades.filter(isLoss);
    const grossWin = wins.reduce((s, t) => s + num(t.r), 0);
    const grossLoss = -losses.reduce((s, t) => s + num(t.r), 0);
    const curve = equityCurve(trades);
    const totalR = curve.length ? curve[curve.length - 1].cum : 0;
    const maxDD = curve.reduce((m, p) => Math.min(m, p.drawdown), 0);

    const avgWin = wins.length ? grossWin / wins.length : 0;
    const avgLoss = losses.length ? -grossLoss / losses.length : 0;
    const winRate = n ? wins.length / n : 0;

    return {
      trades: n,
      wins: wins.length,
      losses: losses.length,
      scratches: n - wins.length - losses.length,
      winRate: winRate,
      // Infinity when nothing lost and something was won: real, and rendered as
      // an em dash rather than a number the eye would try to compare.
      profitFactor: grossLoss === 0 ? (grossWin > 0 ? Infinity : 0) : grossWin / grossLoss,
      expectancyR: n ? totalR / n : 0,
      totalR: totalR,
      avgWinR: avgWin,
      avgLossR: avgLoss,
      payoff: avgLoss === 0 ? (avgWin > 0 ? Infinity : 0) : avgWin / -avgLoss,
      maxDrawdownR: maxDD,
      longestWinStreak: longestStreak(trades, isWin),
      longestLossStreak: longestStreak(trades, isLoss),
      grossWinR: grossWin,
      grossLossR: grossLoss,
    };
  }

  /** Money view of the same run. `pointValue` is currency per index point. */
  function money(trades, pointValue) {
    const pv = num(pointValue);
    const pnl = trades.map((t) => num(t.points) * pv);
    let cum = 0;
    let peak = 0;
    let maxDD = 0;
    for (const p of pnl) {
      cum += p;
      if (cum > peak) peak = cum;
      if (cum - peak < maxDD) maxDD = cum - peak;
    }
    return { net: cum, maxDrawdown: maxDD, perTrade: pnl };
  }

  /** Fixed-width histogram of R-multiples, always including a bin at zero.
   *
   * Bins are anchored on multiples of `width` rather than on the data's own
   * minimum, so two runs binned the same way line up and can be compared.
   */
  function histogram(values, width) {
    const w = width > 0 ? width : 0.5;
    if (!values.length) return [];
    const idx = values.map((v) => Math.floor(num(v) / w));
    const lo = Math.min(...idx, 0);
    const hi = Math.max(...idx, 0);
    const bins = [];
    for (let k = lo; k <= hi; k++) {
      bins.push({ lo: k * w, hi: (k + 1) * w, count: 0, mid: (k + 0.5) * w });
    }
    for (const i of idx) bins[i - lo].count += 1;
    return bins;
  }

  /** Group trades by a key, reporting count, total R and win rate per group. */
  function groupBy(trades, keyFn) {
    const map = new Map();
    for (const t of trades) {
      const k = keyFn(t);
      if (k === null || k === undefined) continue;
      if (!map.has(k)) map.set(k, []);
      map.get(k).push(t);
    }
    return Array.from(map, ([key, rows]) => ({
      key: key,
      trades: rows.length,
      totalR: rows.reduce((s, t) => s + num(t.r), 0),
      wins: rows.filter(isWin).length,
      winRate: rows.length ? rows.filter(isWin).length / rows.length : 0,
    }));
  }

  /** Hour of day (ET) an entry filled, from an ISO timestamp. */
  function entryHour(t) {
    const m = /T(\d{2}):/.exec(t.entry_ts || "");
    return m ? Number(m[1]) : null;
  }

  /** Weekday name from a YYYY-MM-DD day string, without a timezone shift.
   *
   * `new Date("2026-08-17")` parses as UTC midnight and then prints in local
   * time, which lands on the previous day west of Greenwich — the classic way a
   * Monday becomes a Sunday in a dashboard.
   */
  function weekday(day) {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(day || "");
    if (!m) return null;
    const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
    return ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][d.getUTCDay()];
  }

  /** The day funnel: how many sessions survived each gate.
   *
   * This is the diagnostic that separates "the rules found nothing" from "the
   * chart had nothing to find" — a run with no Candle 1 anywhere is a data
   * problem, not a strategy result.
   */
  function funnel(run) {
    const f = (run && run.funnel) || {};
    const stages = [
      { key: "days", label: "Sessions in range", value: num(f.days) },
      { key: "days_with_candle_1", label: "Had Candle 1", value: num(f.days_with_candle_1) },
      { key: "days_committed", label: "Committed to a setup", value: num(f.days_committed) },
      { key: "trades", label: "Entry filled", value: num(f.trades) },
    ];
    const top = stages[0].value || 1;
    return stages.map((s, i) => ({
      ...s,
      share: s.value / top,
      dropFromPrevious: i === 0 ? 0 : stages[i - 1].value - s.value,
    }));
  }

  /** Per-trade R against the run's own reward:risk contract.
   *
   * A stop-out should lose about 1R. Anything materially worse escaped its stop —
   * a gap, or a bracket that was not armed when the entry filled. Surfacing the
   * count is how a risk-control bug gets noticed instead of being read as a bad
   * market.
   */
  function riskBreaches(trades, tolerance) {
    const tol = tolerance > 0 ? tolerance : 1.15;
    return trades.filter((t) => num(t.r) < -tol);
  }

  return {
    equityCurve,
    summarize,
    money,
    histogram,
    groupBy,
    entryHour,
    weekday,
    funnel,
    longestStreak,
    riskBreaches,
  };
});
