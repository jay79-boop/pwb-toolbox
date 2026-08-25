/* Tests for the Strategy Lab stats core.  Run: node static/strategy-lab-stats.test.js
 * Also run under pytest by tests/test_strategy_lab_stats.py, which additionally
 * requires these numbers to agree with pwb_toolbox.performance.trade_stats.
 */

const S = require("./strategy-lab-stats.js");

let failures = 0;
function check(name, got, want, tol) {
  const eps = tol === undefined ? 1e-9 : tol;
  const near = (a, b) =>
    typeof a === "number" && typeof b === "number"
      ? // Infinity is a legitimate profit factor, and Infinity - Infinity is NaN,
        // so non-finite values compare exactly rather than by tolerance.
        isFinite(a) && isFinite(b)
        ? Math.abs(a - b) <= eps
        : Object.is(a, b)
      : JSON.stringify(a) === JSON.stringify(b);
  const ok = Array.isArray(want)
    ? Array.isArray(got) && got.length === want.length && want.every((w, i) => near(got[i], w))
    : near(got, want);
  if (!ok) {
    failures += 1;
    console.error(`FAIL ${name}\n  got  ${JSON.stringify(got)}\n  want ${JSON.stringify(want)}`);
  }
}

const trade = (day, r, extra) =>
  Object.assign(
    {
      day: day,
      direction: 1,
      entry_ts: `${day}T10:00:00-04:00`,
      exit_ts: `${day}T11:00:00-04:00`,
      r: r,
      points: r * 10,
      reason: r > 0 ? "target" : "stop",
    },
    extra || {}
  );

// ---------------------------------------------------------------- equity curve
{
  const t = [trade("2026-08-03", 2.4), trade("2026-08-04", -1), trade("2026-08-05", -1)];
  const c = S.equityCurve(t);
  check("curve cum", c.map((p) => p.cum), [2.4, 1.4, 0.4]);
  check("curve peak holds the high-water mark", c.map((p) => p.peak), [2.4, 2.4, 2.4]);
  check("drawdown is never positive", c.map((p) => p.drawdown), [0, -1, -2]);
}

// ------------------------------------------------------------------- summarize
{
  // 3 wins at 2.4R, 7 losses at -1R: the textbook shape of this strategy.
  const t = [];
  for (let i = 0; i < 3; i++) t.push(trade(`2026-08-0${i + 1}`, 2.4));
  for (let i = 0; i < 7; i++) t.push(trade(`2026-08-1${i}`, -1));
  const s = S.summarize(t);
  check("trades", s.trades, 10);
  check("win rate", s.winRate, 0.3);
  check("total R", s.totalR, 3 * 2.4 - 7, 1e-12);
  check("profit factor", s.profitFactor, 7.2 / 7, 1e-12);
  check("expectancy", s.expectancyR, 0.02, 1e-12);
  check("payoff ratio", s.payoff, 2.4, 1e-12);
  check("max drawdown in R", s.maxDrawdownR, -7);
  check("longest losing streak", s.longestLossStreak, 7);
  check("longest winning streak", s.longestWinStreak, 3);
}

// Breakeven check: at 2.4:1 the theoretical breakeven win rate is 1/3.4.
{
  const t = [];
  for (let i = 0; i < 10; i++) t.push(trade("2026-08-01", i < 10 / 3.4 ? 2.4 : -1));
  const s = S.summarize(t);
  check("breakeven-ish run lands near zero expectancy", Math.abs(s.expectancyR) < 0.25, true);
}

// Degenerate cases must not produce NaN — a dashboard showing NaN is worse than
// one showing nothing.
{
  const s = S.summarize([]);
  check("empty run trades", s.trades, 0);
  check("empty run win rate", s.winRate, 0);
  check("empty run expectancy", s.expectancyR, 0);
  check("empty run profit factor", s.profitFactor, 0);
  const allWins = S.summarize([trade("2026-08-01", 2.4)]);
  check("no losses gives infinite profit factor", allWins.profitFactor, Infinity);
}

// ------------------------------------------------------------------------ money
{
  const t = [trade("2026-08-03", 2.4), trade("2026-08-04", -1)];
  // points = r * 10, NQ point value 20 -> 24*20 and -10*20
  const m = S.money(t, 20);
  check("net currency", m.net, 2.4 * 10 * 20 + -1 * 10 * 20, 1e-9);
  check("currency drawdown", m.maxDrawdown, -200, 1e-9);
}

// -------------------------------------------------------------------- histogram
{
  const bins = S.histogram([-1, -1, 2.4], 0.5);
  const total = bins.reduce((s, b) => s + b.count, 0);
  check("every value lands in a bin", total, 3);
  // Bins are anchored on multiples of the width, so -1 sits on a boundary.
  const at = bins.find((b) => b.lo === -1);
  check("boundary value falls in the bin it opens", at.count, 2);
  check("bins are contiguous", bins.every((b, i) => i === 0 || Math.abs(b.lo - bins[i - 1].hi) < 1e-12), true);
  check("zero is always covered", bins.some((b) => b.lo <= 0 && b.hi > 0), true);
}

// ---------------------------------------------------------------------- groupBy
{
  const t = [
    trade("2026-08-03", 2.4, { reason: "target" }),
    trade("2026-08-04", -1, { reason: "stop" }),
    trade("2026-08-05", -1, { reason: "stop" }),
    trade("2026-08-06", 0.3, { reason: "flatten" }),
  ];
  const g = S.groupBy(t, (x) => x.reason).sort((a, b) => a.key.localeCompare(b.key));
  check("group keys", g.map((x) => x.key), ["flatten", "stop", "target"]);
  check("group counts", g.map((x) => x.trades), [1, 2, 1]);
  check("stop group total R", g[1].totalR, -2);
  check("target group win rate", g[2].winRate, 1);
}

// ------------------------------------------------------------ dates and hours
{
  // 2026-08-17 is a Monday. Parsed naively this prints as Sunday west of UTC.
  check("weekday does not shift timezone", S.weekday("2026-08-17"), "Mon");
  check("weekday of a Friday", S.weekday("2026-08-21"), "Fri");
  check("weekday of nonsense", S.weekday("nope"), null);
  check("entry hour", S.entryHour({ entry_ts: "2026-08-17T09:45:00-04:00" }), 9);
  check("entry hour missing", S.entryHour({}), null);
}

// ----------------------------------------------------------------------- funnel
{
  const f = S.funnel({ funnel: { days: 144, days_with_candle_1: 116, days_committed: 39, trades: 38 } });
  check("funnel stages", f.map((s) => s.value), [144, 116, 39, 38]);
  check("funnel share of top", f[3].share, 38 / 144, 1e-12);
  check("drop from previous stage", f[2].dropFromPrevious, 116 - 39);
  // A regular-hours chart: sessions exist, none carry Candle 1.
  const rth = S.funnel({ funnel: { days: 144, days_with_candle_1: 0, days_committed: 0, trades: 0 } });
  check("regular-hours funnel collapses at Candle 1", rth[1].value, 0);
}

// ---------------------------------------------------------------- risk breaches
{
  const t = [trade("2026-08-03", -1), trade("2026-08-04", -1.02), trade("2026-08-05", -6.1)];
  const b = S.riskBreaches(t);
  check("only the real breach is flagged", b.length, 1);
  check("the flagged trade is the outsized one", b[0].r, -6.1);
}

if (failures) {
  console.error(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log("strategy-lab-stats: all checks passed");
