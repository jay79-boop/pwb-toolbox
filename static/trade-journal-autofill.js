/*
 * trade-journal-autofill.js — derived fields for a single-file trade journal.
 *
 * Two jobs, in order of how much they matter.
 *
 * 1. A long position cannot lose more than it cost. If you paid 9.40 for one
 *    contract, the most the market can take is 940 — the contract going to zero
 *    is the floor, not a step on the way down. A journal that lets you type
 *    1,500 into "money at risk" is recording a number that cannot happen, and
 *    every risk statistic you later compute from that column is wrong in the
 *    direction that flatters you. So the risk field is clamped to the cost
 *    basis, and the stop price is clamped to the interval [0, entry).
 *
 *    The clamp is deliberately NOT applied to short positions. A naked short
 *    has no such floor, and silently capping its risk at the credit received
 *    would be the same lie pointed the other way. Shorts get an explicit
 *    "unbounded" instead of a number.
 *
 * 2. Everything derivable from what you already typed gets filled in, so entry
 *    is four fields instead of fifteen: symbol, entry price, quantity, account.
 *    The rest — multiplier, cost, max loss, stop, scale-out, breakeven, DTE,
 *    hard-exit date, position sizing, and on close the P/L and R multiple —
 *    follows arithmetically and is computed here rather than typed.
 *
 * The house numbers match tools/trade_card.py so the journal and the pre-trade
 * card never disagree: stop at -40%, scale out half at +50%, hard exit at
 * 21 DTE, premium capped at 4% of account.
 *
 * No dependencies, no build step, no network. Drop it beside your journal:
 *
 *     <script src="trade-journal-autofill.js"></script>
 *     <script>TradeJournal.attach(document);</script>
 *
 * attach() finds your inputs by name/id/data-field using a generous alias
 * table, so it usually needs no markup changes. Where it guesses wrong, add
 * data-field="entry" (etc.) to the input and it stops guessing.
 *
 * Self-test:  node static/trade-journal-autofill.js --selftest
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.TradeJournal = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // Shared with tools/trade_card.py. Change them in both places or not at all.
  const STOP_PCT = 40;
  const SCALE_OUT_PCT = 50;
  const EXIT_DTE = 21;
  const MAX_PREMIUM_PCT = 4;
  const MIN_DTE = 30;
  const MAX_DTE = 45;
  const DAY_MS = 86400000;

  // ---------------------------------------------------------------- symbols

  // OCC:    "AAPL  260918C00230000"  (root, yymmdd, C/P, strike x1000)
  const OCC = /^([A-Z][A-Z0-9.]{0,5})\s*(\d{2})(\d{2})(\d{2})([CP])(\d{8})$/;
  // Schwab: "AAPL 09/18/2026 230.00 C"  — the format pwb_toolbox.journal parses.
  const SCHWAB = /^([A-Z.]{1,6})\s+(\d{2})\/(\d{2})\/(\d{4})\s+([\d.]+)\s+([CP])$/;
  // ISO-ish: "AAPL 2026-09-18 230 C"  — what a human types when left alone.
  const LOOSE = /^([A-Z.]{1,6})\s+(\d{4})-(\d{2})-(\d{2})\s+([\d.]+)\s*([CP])$/;

  function iso(y, m, d) {
    return (
      String(y).padStart(4, "0") +
      "-" +
      String(m).padStart(2, "0") +
      "-" +
      String(d).padStart(2, "0")
    );
  }

  /**
   * Read an instrument out of whatever the symbol box contains.
   *
   * The multiplier is the point of this function. Getting it wrong by 100x is
   * the single easiest way to record a position size that is nonsense, and it
   * is knowable from the symbol itself, so nobody should have to type it.
   */
  function parseSymbol(raw) {
    const s = String(raw == null ? "" : raw)
      .trim()
      .toUpperCase()
      .replace(/\s+/g, " ");
    if (!s) return null;

    let m = OCC.exec(s.replace(/\s+/g, ""));
    if (m) {
      return {
        instrument: "option",
        underlying: m[1],
        expiry: iso(2000 + +m[2], +m[3], +m[4]),
        kind: m[5] === "C" ? "call" : "put",
        strike: +m[6] / 1000,
        multiplier: 100,
      };
    }
    m = SCHWAB.exec(s);
    if (m) {
      return {
        instrument: "option",
        underlying: m[1],
        expiry: iso(+m[4], +m[2], +m[3]),
        kind: m[6] === "C" ? "call" : "put",
        strike: +m[5],
        multiplier: 100,
      };
    }
    m = LOOSE.exec(s);
    if (m) {
      return {
        instrument: "option",
        underlying: m[1],
        expiry: iso(+m[2], +m[3], +m[4]),
        kind: m[6] === "C" ? "call" : "put",
        strike: +m[5],
        multiplier: 100,
      };
    }
    if (/^[A-Z][A-Z.\-]{0,9}$/.test(s)) {
      return {
        instrument: "stock",
        underlying: s,
        expiry: null,
        kind: null,
        strike: null,
        multiplier: 1,
      };
    }
    return null;
  }

  // ------------------------------------------------------------------ dates

  function parseDate(v) {
    if (v == null || v === "") return null;
    if (v instanceof Date) return isNaN(v) ? null : v;
    const s = String(v).trim();
    let m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s);
    if (m) return new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
    m = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/.exec(s);
    if (m) return new Date(Date.UTC(+m[3], +m[1] - 1, +m[2]));
    return null;
  }

  function daysBetween(a, b) {
    if (!a || !b) return null;
    return Math.round((b.getTime() - a.getTime()) / DAY_MS);
  }

  function shiftIso(date, days) {
    if (!date) return null;
    const d = new Date(date.getTime() + days * DAY_MS);
    return iso(d.getUTCFullYear(), d.getUTCMonth() + 1, d.getUTCDate());
  }

  function num(v) {
    if (v == null || v === "") return null;
    if (typeof v === "number") return isFinite(v) ? v : null;
    // Tolerate what people actually paste: "$9.40", "1,200", "(940)".
    let s = String(v).trim().replace(/[$,\s]/g, "");
    let sign = 1;
    if (/^\(.*\)$/.test(s)) {
      sign = -1;
      s = s.slice(1, -1);
    }
    if (s.endsWith("%")) s = s.slice(0, -1);
    const n = parseFloat(s);
    return isFinite(n) ? sign * n : null;
  }

  // ------------------------------------------------------------------ core

  /**
   * Turn what the user typed into every field that follows from it.
   *
   * Returns a flat object of derived values plus two annotation lists:
   *   notes  — things worth telling the user (house-rule breaches, sizing)
   *   clamps — values this function refused to accept as given, and why.
   *
   * Nothing here throws. A half-filled form is the normal case during entry,
   * and the right response to a missing field is a null, not an exception.
   */
  function derive(input) {
    const inp = input || {};
    const out = {
      notes: [],
      clamps: [],
      instrument: null,
      underlying: null,
      expiry: null,
      strike: null,
      kind: null,
      multiplier: null,
      side: null,
      costBasis: null,
      maxLoss: null,
      maxLossUnbounded: false,
      riskDollars: null,
      riskPctAccount: null,
      stopPrice: null,
      scaleOutPrice: null,
      breakeven: null,
      dte: null,
      hardExitDate: null,
      pctAccount: null,
      suggestedQty: null,
      targetPrice: null,
      rewardDollars: null,
      rewardToRisk: null,
      exitPrice: null,
      pnl: null,
      pnlPct: null,
      rMultiple: null,
      holdDays: null,
    };

    const sym = parseSymbol(inp.symbol);
    if (sym) {
      out.instrument = sym.instrument;
      out.underlying = sym.underlying;
      out.expiry = sym.expiry;
      out.strike = sym.strike;
      out.kind = sym.kind;
      out.multiplier = sym.multiplier;
    }
    // An explicit expiry or multiplier on the form always wins over the guess.
    const expiryOverride = parseDate(inp.expiry);
    if (expiryOverride) {
      out.expiry = shiftIso(expiryOverride, 0);
      if (out.instrument == null) out.instrument = "option";
      if (out.multiplier == null) out.multiplier = 100;
    }
    const multOverride = num(inp.multiplier);
    if (multOverride && multOverride > 0) out.multiplier = multOverride;
    if (out.multiplier == null) out.multiplier = 1;

    const side = String(inp.side || "long").trim().toLowerCase();
    out.side = side.startsWith("s") ? "short" : "long";
    const isLong = out.side === "long";

    const entry = num(inp.entry);
    const qty = num(inp.qty);
    const account = num(inp.account);
    const fees = num(inp.fees) || 0;

    if (entry != null && qty != null) {
      out.costBasis = entry * Math.abs(qty) * out.multiplier;
    }

    // -- the clamp -----------------------------------------------------
    //
    // For a long, zero is a real price and the floor is the debit paid. For a
    // short there is no floor, so refusing to print a number is the honest
    // answer; the journal shows "unbounded" and leaves the risk column empty
    // rather than recording a comfortable fiction.
    if (isLong) {
      out.maxLoss = out.costBasis;
    } else if (out.costBasis != null) {
      out.maxLossUnbounded = true;
      out.notes.push(
        "Short position: loss is not bounded by the " +
          fmtMoney(out.costBasis) +
          " credit. Size it off a stop, not off max loss."
      );
    }

    // -- stop and risk -------------------------------------------------
    //
    // Precedence, most specific first. A stop you typed is a decision, so it
    // fixes the risk. A risk you typed is also a decision, so it fixes the
    // stop — filling that box backwards is most of what makes entry quick.
    // With neither, the house -40% applies and both fall out of it.
    let stop = num(inp.stopPrice);
    if (stop != null && entry != null && isLong) {
      if (stop < 0) {
        out.clamps.push({
          field: "stopPrice",
          given: stop,
          used: 0,
          why: "A price cannot be negative; zero is the floor.",
        });
        stop = 0;
      } else if (stop >= entry) {
        const capped = entry * (1 - STOP_PCT / 100);
        out.clamps.push({
          field: "stopPrice",
          given: stop,
          used: capped,
          why:
            "A stop at or above the " +
            entry +
            " entry is not a stop. Fell back to the house -" +
            STOP_PCT +
            "%.",
        });
        stop = capped;
      }
    }

    let risk = null;
    const typedRisk = num(inp.riskDollars);
    const sized = entry != null && qty != null && Math.abs(qty) > 0;

    if (stop != null && sized && isLong) {
      risk = (entry - stop) * Math.abs(qty) * out.multiplier;
    } else if (typedRisk != null) {
      risk = typedRisk;
    } else if (entry != null && isLong) {
      stop = entry * (1 - STOP_PCT / 100); // house rule, matches trade_card.py
      if (sized) risk = (entry - stop) * Math.abs(qty) * out.multiplier;
    }

    // The clamp itself. Everything above only decided which number to clamp.
    if (risk != null && out.maxLoss != null && risk > out.maxLoss + 1e-9) {
      out.clamps.push({
        field: "riskDollars",
        given: risk,
        used: out.maxLoss,
        why:
          "A long " +
          (out.instrument === "option" ? "option" : "position") +
          " bought for " +
          fmtMoney(out.maxLoss) +
          " cannot lose more than that \u2014 zero is the floor.",
      });
      risk = out.maxLoss;
    }
    if (risk != null && risk < 0) risk = 0;

    // A typed risk backfills the stop it implies, so the exit order is on the
    // card even though nobody worked it out by hand.
    if (stop == null && risk != null && sized && isLong) {
      stop = entry - risk / (Math.abs(qty) * out.multiplier);
      if (stop < 0) stop = 0;
    }

    out.stopPrice = stop;
    out.riskDollars = risk;
    if (entry != null) out.scaleOutPrice = entry * (1 + SCALE_OUT_PCT / 100);

    if (account != null && account > 0) {
      if (out.costBasis != null) out.pctAccount = (out.costBasis / account) * 100;
      if (risk != null) out.riskPctAccount = (risk / account) * 100;
      if (out.pctAccount != null && out.pctAccount > MAX_PREMIUM_PCT) {
        out.notes.push(
          "Position is " +
            out.pctAccount.toFixed(1) +
            "% of account, over the " +
            MAX_PREMIUM_PCT +
            "% cap."
        );
      }
      // What size the stop actually permits, given a risk budget in percent.
      const budgetPct = num(inp.riskBudgetPct);
      if (budgetPct != null && entry != null && stop != null && stop < entry) {
        const perUnit = (entry - stop) * out.multiplier;
        if (perUnit > 0) {
          out.suggestedQty = Math.floor(
            (account * (budgetPct / 100)) / perUnit
          );
        }
      }
    }

    // -- breakeven -----------------------------------------------------
    if (out.instrument === "option" && out.strike != null && entry != null) {
      out.breakeven =
        out.kind === "put" ? out.strike - entry : out.strike + entry;
    } else if (entry != null) {
      out.breakeven = entry;
    }

    // -- the clock -----------------------------------------------------
    const entryDate = parseDate(inp.entryDate);
    const expiryDate = parseDate(out.expiry);
    if (entryDate && expiryDate) {
      out.dte = daysBetween(entryDate, expiryDate);
      out.hardExitDate = shiftIso(expiryDate, -EXIT_DTE);
      if (out.dte < MIN_DTE || out.dte > MAX_DTE) {
        out.notes.push(
          out.dte +
            " DTE at entry is outside the " +
            MIN_DTE +
            "-" +
            MAX_DTE +
            " window."
        );
      }
    }

    // -- reward --------------------------------------------------------
    // Target is a price on the instrument you hold, not on the underlying, so
    // it means the same thing for shares and for contracts.
    let target = num(inp.targetPrice);
    if (target == null && out.scaleOutPrice != null && isLong) {
      target = out.scaleOutPrice;
    }
    out.targetPrice = target;
    if (target != null && entry != null && qty != null) {
      const dir = isLong ? 1 : -1;
      out.rewardDollars =
        (target - entry) * dir * Math.abs(qty) * out.multiplier;
      if (risk != null && risk > 0) out.rewardToRisk = out.rewardDollars / risk;
    }

    // -- the close -----------------------------------------------------
    const exit = num(inp.exitPrice);
    if (exit != null && entry != null && qty != null) {
      out.exitPrice = exit;
      const dir = isLong ? 1 : -1;
      out.pnl = (exit - entry) * dir * Math.abs(qty) * out.multiplier - fees;
      if (out.costBasis) out.pnlPct = (out.pnl / out.costBasis) * 100;
      if (risk != null && risk > 0) out.rMultiple = out.pnl / risk;
    }
    const exitDate = parseDate(inp.exitDate);
    if (entryDate && exitDate) out.holdDays = daysBetween(entryDate, exitDate);

    return out;
  }

  // ------------------------------------------------------------ formatting

  function fmtMoney(v) {
    if (v == null || !isFinite(v)) return "";
    const sign = v < 0 ? "-" : "";
    return (
      sign +
      "$" +
      Math.abs(v)
        .toFixed(2)
        .replace(/\B(?=(\d{3})+(?!\d))/g, ",")
    );
  }

  const FORMAT = {
    costBasis: fmtMoney,
    maxLoss: fmtMoney,
    riskDollars: fmtMoney,
    rewardDollars: fmtMoney,
    pnl: fmtMoney,
    stopPrice: (v) => (v == null ? "" : v.toFixed(2)),
    scaleOutPrice: (v) => (v == null ? "" : v.toFixed(2)),
    targetPrice: (v) => (v == null ? "" : v.toFixed(2)),
    breakeven: (v) => (v == null ? "" : v.toFixed(2)),
    strike: (v) => (v == null ? "" : String(v)),
    pctAccount: (v) => (v == null ? "" : v.toFixed(2) + "%"),
    riskPctAccount: (v) => (v == null ? "" : v.toFixed(2) + "%"),
    pnlPct: (v) => (v == null ? "" : v.toFixed(1) + "%"),
    rewardToRisk: (v) => (v == null ? "" : v.toFixed(2) + "R"),
    rMultiple: (v) => (v == null ? "" : (v >= 0 ? "+" : "") + v.toFixed(2) + "R"),
    multiplier: (v) => (v == null ? "" : String(v)),
    dte: (v) => (v == null ? "" : String(v)),
    holdDays: (v) => (v == null ? "" : String(v)),
  };

  function format(field, value, derived) {
    if (field === "maxLoss" && derived && derived.maxLossUnbounded) {
      return "unbounded";
    }
    const f = FORMAT[field];
    return f ? f(value) : value == null ? "" : String(value);
  }

  // ------------------------------------------------------------ DOM wiring

  // Normalising to bare letters and digits means "Entry Price", "entry_price",
  // "entryPrice" and "entry-price" are all the same key, which is what makes
  // attach() work on a form it has never seen.
  function key(s) {
    return String(s || "").toLowerCase().replace(/[^a-z0-9]/g, "");
  }

  const ALIASES = {
    symbol: ["symbol", "ticker", "contract", "instrument", "underlying"],
    side: ["side", "direction", "longshort", "position", "buysell"],
    entry: [
      "entry", "entryprice", "price", "premium", "entrypremium",
      "avgprice", "averageprice", "fillprice", "costpershare", "openprice",
    ],
    qty: [
      "qty", "quantity", "shares", "contracts", "size", "positionsize",
      "numcontracts", "numberofcontracts", "units",
    ],
    account: [
      "account", "accountsize", "accountequity", "accountvalue", "equity",
      "capital", "portfolio", "portfoliovalue", "buyingpower", "networth",
    ],
    stopPrice: ["stop", "stopprice", "stoploss", "stopat", "stoplossprice"],
    riskDollars: [
      "risk", "riskdollars", "maxrisk", "maxmoneyatrisk", "moneyatrisk",
      "amountatrisk", "atrisk", "riskamount", "dollarrisk", "maxloss",
      "riskcapital",
    ],
    riskBudgetPct: ["riskbudgetpct", "riskpercent", "riskpct", "riskofaccount"],
    targetPrice: ["target", "targetprice", "takeprofit", "tp", "exittarget"],
    exitPrice: ["exit", "exitprice", "closeprice", "sellprice", "fillexit"],
    entryDate: ["entrydate", "dateentered", "opendate", "dateopened", "date"],
    exitDate: ["exitdate", "closedate", "dateclosed", "dateexited"],
    expiry: ["expiry", "expiration", "expdate", "expirationdate", "expires"],
    multiplier: ["multiplier", "contractmultiplier", "pointvalue"],
    fees: ["fees", "commission", "commissions", "feesandcomm", "cost"],
  };

  const INPUT_KEYS = {};
  Object.keys(ALIASES).forEach((field) => {
    ALIASES[field].forEach((a) => {
      if (!(a in INPUT_KEYS)) INPUT_KEYS[a] = field;
    });
  });

  const DERIVED_FIELDS = [
    "instrument", "underlying", "expiry", "strike", "kind", "multiplier",
    "costBasis", "maxLoss", "riskDollars", "riskPctAccount", "stopPrice",
    "scaleOutPrice", "breakeven", "dte", "hardExitDate", "pctAccount",
    "suggestedQty", "targetPrice", "rewardDollars", "rewardToRisk",
    "pnl", "pnlPct", "rMultiple", "holdDays",
  ];

  function fieldOf(el) {
    const explicit = el.getAttribute("data-field");
    if (explicit) return explicit;
    const k = key(el.getAttribute("name") || el.id || "");
    return INPUT_KEYS[k] || null;
  }

  function readValue(el) {
    if (el.type === "checkbox") return el.checked;
    return el.value;
  }

  /**
   * Bind a form so every derived field updates as you type.
   *
   * Inputs are located by data-field, then name, then id, through the alias
   * table. Outputs are any element carrying data-derived="<field>"; if the
   * element takes a value it is written there, otherwise into textContent.
   *
   * Returns { recompute, detach, snapshot }.
   */
  function attach(rootEl, options) {
    const opts = options || {};
    const root = rootEl || document;
    const scope = root.querySelector ? root : document;

    function inputs() {
      const found = {};
      scope.querySelectorAll("input, select, textarea").forEach((el) => {
        if (el.hasAttribute("data-derived")) return;
        const f = fieldOf(el);
        if (f && !(f in found)) found[f] = el;
      });
      return found;
    }

    function snapshot() {
      const els = inputs();
      const raw = {};
      Object.keys(els).forEach((f) => (raw[f] = readValue(els[f])));
      if (opts.defaults) {
        Object.keys(opts.defaults).forEach((f) => {
          if (raw[f] == null || raw[f] === "") raw[f] = opts.defaults[f];
        });
      }
      return raw;
    }

    function write(el, text) {
      if ("value" in el && /^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName)) {
        if (el.value !== text) el.value = text;
      } else if (el.textContent !== text) {
        el.textContent = text;
      }
    }

    // A clamp explains itself for exactly as long as its number is on screen.
    // Without this the reason survives one keystroke: the corrected value gets
    // written back, the next recompute reads a value that no longer needs
    // clamping, and the sentence saying why the number moved vanishes just as
    // the user looks up to read it.
    const sticky = {};

    function recompute() {
      const els = inputs();
      const raw = snapshot();
      const d = derive(raw);

      // Write the clamp back into the field the user typed into. Seeing the
      // number correct itself under the cursor is the whole lesson; a warning
      // beside a wrong value that stays wrong teaches nothing.
      d.clamps.forEach((c) => {
        const shown = c.used.toFixed(2);
        const el = els[c.field];
        if (el && "value" in el) {
          if (el.value !== shown) el.value = shown;
          sticky[c.field] = { value: shown, why: c.why };
        }
      });
      // Drop a remembered reason once its field no longer holds the value the
      // clamp put there — the user has moved on, and so should the warning.
      Object.keys(sticky).forEach((f) => {
        const el = els[f];
        if (!el || !("value" in el) || el.value !== sticky[f].value) {
          delete sticky[f];
        }
      });

      DERIVED_FIELDS.forEach((f) => {
        scope
          .querySelectorAll('[data-derived="' + f + '"]')
          .forEach((el) => write(el, format(f, d[f], d)));
      });

      const held = Object.keys(sticky).map((f) => sticky[f].why);
      const notes = d.notes.concat(
        held.concat(d.clamps.map((c) => c.why)).filter((w, i, a) => a.indexOf(w) === i)
      );
      scope.querySelectorAll("[data-derived-notes]").forEach((el) => {
        el.textContent = notes.join(" ");
        el.hidden = notes.length === 0;
      });

      if (typeof opts.onChange === "function") opts.onChange(d, raw);
      return d;
    }

    const handler = () => recompute();
    scope.addEventListener("input", handler, true);
    scope.addEventListener("change", handler, true);
    recompute();

    return {
      recompute,
      snapshot,
      detach() {
        scope.removeEventListener("input", handler, true);
        scope.removeEventListener("change", handler, true);
      },
    };
  }

  return {
    STOP_PCT, SCALE_OUT_PCT, EXIT_DTE, MAX_PREMIUM_PCT, MIN_DTE, MAX_DTE,
    parseSymbol, parseDate, derive, format, fmtMoney, attach,
    ALIASES, DERIVED_FIELDS,
  };
});
