/* Finviz Research Dashboard -- no framework, no build step.
   Every render is driven by /api/* responses; there is no stored state in the
   page, so nothing here can go stale the way the old dashboard did. */

"use strict";

/* ---------------- tiny helpers ---------------- */

function $(sel, root) { return (root || document).querySelector(sel); }
function $$(sel, root) { return Array.from((root || document).querySelectorAll(sel)); }

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function numval(s) {
  /* Loose numeric value of a Finviz string for sorting: strips $, %, commas
     and turns 1.5B/41.4M into magnitudes. Falls back to raw lowercased text. */
  if (s == null) return -Infinity;
  if (typeof s === "number") return s;
  var t = String(s).replace(/[$%,()]/g, "").trim();
  var m = t.match(/^[-+]?[\d.]+([KMBT])?$/i);
  if (!m) return String(s).toLowerCase();
  var mult = { K: 1e3, M: 1e6, B: 1e9, T: 1e12 }[m[1] ? m[1].toUpperCase() : 0];
  return parseFloat(t) * (mult || 1);
}

function fmtPct(x) {
  if (x == null || isNaN(x)) return "n/a";
  return (x > 0 ? "+" : "") + Number(x).toFixed(2) + "%";
}

function el(html) {
  var t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstChild;
}

/* ---------------- shared state ---------------- */

var tabLoaded = {};

/* ---------------- fetch + banners ---------------- */

function fetchJSON(url) {
  return fetch(url, { cache: "no-store" }).then(function (r) {
    return r.json().then(function (data) {
      if (!r.ok || (data && data.ok === false)) {
        throw new Error((data && data.error) || ("HTTP " + r.status));
      }
      return data;
    });
  });
}

function showBanner(msg, kind) {
  var b = $("#banner");
  b.hidden = false;
  b.className = "banner" + (kind === "ok" ? " ok" : "");
  b.innerHTML = "";
  b.appendChild(el("<span>" + esc(msg) + "</span>"));
  var close = el('<button class="btn small ghost">Dismiss</button>');
  close.addEventListener("click", function () { b.hidden = true; });
  b.appendChild(close);
}

function failBanner(where, err) {
  var detail = "No AI tokens were spent -- this is a data source (Finviz/Yahoo) "
    + "being slow or rate-limited. Refresh in a minute. Errors are also logged on "
    + "your Desktop at finviz-research\\error.log.";
  showBanner(where + (err && err.message ? ": " + err.message : "") + " — " + detail);
  console.error(where, err);
}

/* ---------------- market status chip ---------------- */

function setMarket(m) {
  var chip = $("#market-status");
  if (!m) return;
  var cls = m.status === "open" ? "chip-good" : m.status === "pre" ? "chip-warn" : "chip-muted";
  chip.className = "chip " + cls;
  chip.textContent = m.label + " · " + m.et;
  chip.title = "US market hours, Eastern time";
}

/* ---------------- tabs ---------------- */

function switchTab(name) {
  $$(".tab").forEach(function (t) { t.classList.toggle("active", t.dataset.tab === name); });
  $$(".pane").forEach(function (p) { p.classList.toggle("active", p.id === "pane-" + name); });
  if (!tabLoaded[name]) { tabLoaded[name] = true; load[name](); }
}

/* ---------------- watchlist board ---------------- */

function sparkSVG(values, up) {
  /* Inline SVG sparkline; no external libs. Color by the overall move. */
  if (!values || values.length < 2) return "";
  var w = 260, h = 50, pad = 2;
  var min = Math.min.apply(null, values), max = Math.max.apply(null, values);
  var span = max - min || 1;
  var pts = values.map(function (v, i) {
    var x = pad + (i / (values.length - 1)) * (w - 2 * pad);
    var y = h - pad - ((v - min) / span) * (h - 2 * pad);
    return x.toFixed(1) + "," + y.toFixed(1);
  });
  var last = values[values.length - 1], first = values[0];
  var good = typeof up === "boolean" ? up : last >= first;
  var stroke = good ? getComputedStyle(document.documentElement).getPropertyValue("--good").trim() || "#00701f"
                    : getComputedStyle(document.documentElement).getPropertyValue("--bad").trim() || "#b5292a";
  var lastPt = pts[pts.length - 1].split(",");
  return '<svg class="spark" viewBox="0 0 ' + w + " " + h + '" aria-hidden="true">'
    + '<polyline fill="none" stroke="' + stroke + '" stroke-width="1.6" points="' + pts.join(" ") + '"/>'
    + '<circle cx="' + lastPt[0] + '" cy="' + lastPt[1] + '" r="2.4" fill="' + stroke + '"/></svg>';
}

function renderWatchlist(data) {
  var pane = $("#pane-watchlist");
  setMarket(data && data.market);

  var head = el(
    '<div class="panehead"><h2>My Watchlist</h2>'
    + '<div class="headertools"><span class="meta" id="wl-meta"></span>'
    + '<button class="btn ghost small" id="wl-refresh">Refresh now</button></div></div>'
  );
  pane.innerHTML = "";
  pane.appendChild(head);

  if (data.empty) {
    pane.appendChild(el(
      '<div class="empty"><div class="big">No tickers being tracked yet.</div>'
      + '<p>Add one below, or open the Screener tab and click a stock to save it here.</p></div>'
    ));
  } else {
    var grid = el('<div class="cards"></div>');
    data.items.forEach(function (it) {
      grid.appendChild(watchCard(it, data));
    });
    pane.appendChild(grid);
    if (data.flagged && data.flagged.length) {
      pane.appendChild(el(
        '<p class="muted" style="margin-top:12px">⚠ <b>' + data.flagged.length
        + ' flagged</b> — ' + esc(data.flag_reason || "") + ".</p>"
      ));
    }
  }

  pane.appendChild(addRowPanel());

  var meta = $("#wl-meta");
  if (meta) {
    var n = data.items.length;
    meta.textContent = "fetched " + (data.fetched_at || "") + " · " + n + (n === 1 ? " symbol" : " symbols");
  }
  $("#wl-refresh").addEventListener("click", function () { loadWatchlist(true); });
}

function watchCard(it, data) {
  var c = el('<div class="card"></div>');
  var flag = data.flagged && data.flagged.indexOf(it.ticker) !== -1;

  var changeCls = it.change >= 0 ? "chip-good" : "chip-bad";
  var changeWord = it.change >= 0 ? "▲ Up" : "▼ Down";
  var signals = it.signals || {};

  c.className = "card" + (flag ? " flag" : "");
  c.innerHTML =
    '<div class="card-top"><span class="sym">' + esc(it.ticker)
    + '</span><span class="remove" title="Remove from watchlist">✕</span>'
    + '<span class="price">' + esc(it.price != null ? it.price.toFixed(2) : "—") + "</span></div>"
    + '<div class="card-top"><span class="card-change ' + changeCls + '">' + changeWord + " "
    + fmtPct(it.change_pct) + "</span>"
    + (flag ? '<span class="chip chip-warn">● Flagged</span>' : '') + "</div>"
    + sparkSVG(it.spark, it.change >= 0);

  var chips = "";
  (it.signal_labels || []).forEach(function (lab) {
    // the word already encodes direction -- glyph + word per page-style
    chips += '<span class="mini ' + (lab.toLowerCase().indexOf("down") !== -1 ? "bad" : "good") + '">'
      + esc(lab) + "</span>";
  });
  if (it.signals) {
    var cnf = it.signals.confluence || {};
    chips += '<span class="mini ' + (cnf.direction === "bullish" ? "good" : cnf.direction === "bearish" ? "bad" : "mut") + '">confluence '
      + esc(cnf.direction || "mixed") + " (" + cnf.bullish_count + "▲/" + cnf.bearish_count + "▼)</span>";
    chips += '<span class="mini mut">RSI ' + esc(it.signals.rsi14 != null ? it.signals.rsi14.toFixed(1) : "—") + "</span>";
  }
  if (chips) c.innerHTML += '<div class="chiprow">' + chips + "</div>";
  if (it.error) c.innerHTML += '<div class="err">' + esc(it.error) + "</div>";

  c.querySelector(".remove").addEventListener("click", function (ev) {
    ev.stopPropagation();
    fetchJSON("/api/watchlist-remove?t=" + encodeURIComponent(it.ticker))
      .then(loadWatchlist)
      .catch(function (e) { failBanner("Removing " + it.ticker, e); });
  });
  c.addEventListener("click", function () { openTicker(it.ticker); });
  return c;
}

function addRowPanel() {
  var wrap = el('<div class="addrow"></div>');
  var input = el('<input type="text" placeholder="Add ticker, e.g. AMD" spellcheck="false">');
  var btn = el('<button class="btn small">Add</button>');
  function add() {
    var sym = input.value.trim().toUpperCase();
    if (!sym) return;
    fetchJSON("/api/watchlist-add?t=" + encodeURIComponent(sym))
      .then(function () { input.value = ""; return loadWatchlist(); })
      .catch(function (e) { failBanner("Adding " + sym, e); });
  }
  btn.addEventListener("click", add);
  input.addEventListener("keydown", function (ev) { if (ev.key === "Enter") add(); });
  wrap.appendChild(input);
  wrap.appendChild(btn);
  return wrap;
}

function loadWatchlist() {
  var pane = $("#pane-watchlist");
  if (!pane.children.length) {
    pane.innerHTML = '<div class="loading"><span class="spin"></span> Loading your watchlist…</div>';
  }
  return fetchJSON("/api/watchlist")
    .then(function (data) { renderWatchlist(data); return data; })
    .catch(function (e) { failBanner("Loading your watchlist", e); });
}

/* ---------------- market map ---------------- */

function tileClass(change) {
  if (change >= 2) return "tile up-s";
  if (change >= 0.8) return "tile up";
  if (change >= 0.3) return "tile up-m";
  if (change > -0.3) return "tile flat";
  if (change > -0.8) return "tile down-m";
  if (change > -2) return "tile down";
  return "tile down-s";
}

function renderMap(data) {
  var pane = $("#pane-map");
  var head = el(
    '<div class="panehead"><h2>Market Map — S&amp;P 500</h2>'
    + '<span class="meta">fetched ' + esc(data.fetched_at || "") + " · " + esc(data.sectors ? data.sectors.length + " sectors" : 0) + "</span></div>"
  );
  pane.innerHTML = "";
  pane.appendChild(head);

  var legend = el(
    '<div class="legend">'
    + '<span class="tile up-s"><span class="t">+2</span><span class="pc">%</span></span>'
    + '<span class="tile up"><span class="t">+1</span><span class="pc">%</span></span>'
    + '<span class="tile up-m"><span class="t">+0.3</span><span class="pc">%</span></span>'
    + '<span class="tile flat"><span class="t">0</span></span>'
    + '<span class="tile down-m"><span class="t">−0.3</span></span>'
    + '<span class="tile down"><span class="t">−1</span><span class="pc">%</span></span>'
    + '<span class="tile down-s"><span class="t">−2</span><span class="pc">%</span></span>'
    + "</div><p class='muted'>Up to date: " + esc(data.fetched_at || "") + ". Click any tile for the full view.</p>"
  );
  pane.appendChild(legend);

  (data.sectors || []).forEach(function (sec) {
    var s = el('<div class="sector"></div>');
    var cls = sec.avg_change >= 0 ? "chip-good" : "chip-bad";
    s.innerHTML = '<div class="sector-head"><h3>' + esc(sec.sector) + "</h3><span class='chip " + cls + "'>"
      + fmtPct(sec.avg_change) + "</span><span class='muted'>" + sec.count + " names</span></div>";
    var grid = el('<div class="mapgrid"></div>');
    sec.tiles.forEach(function (tile) {
      var t = el(
        '<div class="' + tileClass(tile.c) + '" title="' + esc(tile.n + " · " + tile.t + " · $" + (typeof tile.p === "number" ? tile.p : "—") + " · " + fmtPct(tile.c) + " · " + tile.mc) + '">'
        + '<span class="t">' + esc(tile.t) + '</span><span class="pc">' + fmtPct(tile.c) + "</span></div>"
      );
      t.addEventListener("click", function () { openTicker(tile.t); });
      grid.appendChild(t);
    });
    s.appendChild(grid);
    pane.appendChild(s);
  });
}

function loadMap() {
  var pane = $("#pane-map");
  pane.innerHTML = '<div class="loading"><span class="spin"></span> Loading the S&amp;P 500 map…</div>';
  fetchJSON("/api/map")
    .then(function (data) { renderMap(data); return data; })
    .catch(function (e) { failBanner("Loading the market map", e); });
}

/* ---------------- screener ---------------- */

var screenState = { preset: null, custom: [], rows: [], sortKey: null, sortAsc: true, cols: [] };

var PREFERRED_COLS = [
  "Ticker", "Company", "Sector", "Industry", "Market Cap", "P/E",
  "Forward P/E", "PEG", "EPS (ttm)", "RSI (14)", "Price", "Change", "Change %",
  "Volume", "Avg Volume", "52W High", "52W Low", "Shs Float"
];

// Finviz sends these as raw floats (47160000000.0); show 47.16B. Sorting
// still reads the raw value, so order is unaffected.
var BIG_NUMBER_COLS = ["Market Cap", "Volume", "Avg Volume", "Shs Float"];
var CHANGE_COLS = ["Change", "Change %", "Percent Change"];

function humanNumber(x) {
  var n = Number(x);
  if (!isFinite(n)) return String(x);
  var units = [[1e12, "T"], [1e9, "B"], [1e6, "M"], [1e3, "K"]];
  for (var i = 0; i < units.length; i++) {
    if (Math.abs(n) >= units[i][0]) return (n / units[i][0]).toFixed(2) + units[i][1];
  }
  return String(n);
}

function screenQuery() {
  var q = [];
  if (screenState.preset) q.push("preset=" + encodeURIComponent(screenState.preset));
  screenState.custom.forEach(function (f) { q.push("f=" + encodeURIComponent(f)); });
  return q.join("&");
}

function screenLabel() {
  var parts = [];
  if (screenState.preset) parts.push("preset “" + screenState.preset + "”");
  if (screenState.custom.length) parts.push("+ " + screenState.custom.join(", "));
  return parts.join(" ") || "—";
}

function renderScreener() {
  var pane = $("#pane-screener");
  pane.innerHTML = "";

  var head = el('<div class="panehead"><h2>Stock Screener</h2>'
    + '<div class="headertools"><span class="meta" id="sc-meta"></span></div></div>');
  pane.appendChild(head);

  var bar = el('<div class="presetbar"></div>');
  (window.__presets || []).forEach(function (p) {
    var b = el('<button class="presetbtn" data-preset="' + esc(p.name) + '">' + esc(p.name) + "<small>" + esc(p.labels) + "</small></button>");
    b.addEventListener("click", function () {
      screenState.preset = p.name;
      screenState.custom = [];
      $("#filter-input").value = "";
      runScreener();
    });
    bar.appendChild(b);
  });
  pane.appendChild(bar);

  var box = el('<div class="addrow"><input type="text" id="filter-input" placeholder="e.g. Sector=Technology; P/E=Under 20" spellcheck="false">'
    + '<button class="btn small" id="filter-add">Apply</button></div>');
  pane.appendChild(box);

  var note = el('<p class="muted" id="sc-status">Pick a preset above to filter the market to a shortlist.'
    + " Or type your own filters as <b>Name=Value</b>, separated by <b>;</b> — they stack on top of the"
    + " preset you last picked. Names must match Finviz's own labels exactly.</p>");
  pane.appendChild(note);

  function applyCustom() {
    var parts = $("#filter-input").value.split(";").map(function (s) { return s.trim(); })
      .filter(function (s) { return s; });
    var bad = parts.filter(function (s) { return s.indexOf("=") < 1 || s.indexOf("=") === s.length - 1; });
    if (bad.length) { showBanner("Each filter needs the form Name=Value. Could not read: " + bad.join(", ")); return; }
    screenState.custom = parts;
    runScreener();
  }
  box.querySelector("#filter-add").addEventListener("click", applyCustom);
  box.querySelector("#filter-input").addEventListener("keydown", function (ev) { if (ev.key === "Enter") applyCustom(); });

  if (!screenState.rows.length) return;
  pane.appendChild(renderTable(screenState.rows, screenState.cols));
}

// Runs whatever screenState holds: a preset, hand-typed filters, or both.
function runScreener() {
  var status = $("#sc-status");
  var meta = $("#sc-meta");
  if (status) status.textContent = "Fetching " + screenLabel() + "… big screens take up to a minute (Finviz serves 20 rows per page).";
  fetchJSON("/api/screener?" + screenQuery())
    .then(function (data) {
      screenState.rows = data.rows || [];
      screenState.cols = pickCols(screenState.rows);
      setMeta(meta, screenLabel() + " · " + data.count + " match(es), largest first"
        + (data.truncated ? " (fetch cap hit — smallest names dropped)" : "") + " · " + (data.fetched_at || ""));
      updateActivePreset();
      var statusEl = $("#sc-status");
      if (statusEl) statusEl.textContent = data.count
        ? (data.count === 1 ? "1 match." : data.count + " matches.") + " Click a row for the full view."
        : "No matches. Try a different preset or loosen a filter.";
      renderResultTable();
    })
    .catch(function (e) {
      if (status) status.textContent = "";
      failBanner("Running the screener", e);
    });
}

function pickCols(rows) {
  if (!rows.length) return [];
  var keys = Object.keys(rows[0]);
  var chosen = PREFERRED_COLS.filter(function (c) { return keys.indexOf(c) !== -1; });
  (keys).forEach(function (k) { if (chosen.indexOf(k) === -1) chosen.push(k); });
  return chosen;
}

function setMeta(elm, text) { if (elm) elm.textContent = text; }

function updateActivePreset() {
  $$(".presetbtn").forEach(function (b) {
    b.classList.toggle("active", b.dataset.preset === screenState.preset);
  });
}

function sortRows(rows, key, asc) {
  return rows.slice().sort(function (a, b) {
    var u = numval(a[key]), v = numval(b[key]);
    if (typeof u === "number" && typeof v === "number") return asc ? u - v : v - u;
    return asc ? String(u).localeCompare(String(v)) : String(v).localeCompare(String(u));
  });
}

function renderResultTable() {
  askExportButton();
  var wrap = renderTable(screenState.rows, screenState.cols);
  var note = $("#sc-status");
  var pane = $("#pane-screener");
  // replace any existing tablewrap (created by renderScreener or previous run)
  var old = pane.querySelector(".tablewrap");
  if (old) old.remove();
  if (note) note.parentNode.insertBefore(wrap, note.nextSibling);
}

function askExportButton() {
  var metaWrap = $("#sc-meta");
  var existing = $("#sc-export");
  if (!metaWrap || existing) return;
  var b = el('<button class="btn small ghost" id="sc-export">Save CSV on Desktop</button>');
  b.addEventListener("click", function () {
    if (!screenState.preset && !screenState.custom.length) return;
    b.disabled = true; b.textContent = "Saving…";
    fetchJSON("/api/screener-export?" + screenQuery())
      .then(function (d) {
        b.disabled = false; b.textContent = "Saved ✓ " + d.count + " rows";
        showBanner("Saved " + d.count + " rows → " + d.path, "ok");
      })
      .catch(function (e) { b.disabled = false; b.textContent = "Save CSV on Desktop"; failBanner("Saving CSV", e); });
  });
  metaWrap.parentNode.appendChild(b);
}

function renderTable(rows, cols) {
  var wrap = el('<div class="tablewrap" tabindex="0" role="region" aria-label="Screener results"></div>');
  var table = el("<table class='dt'></table>");
  var thead = el("<thead><tr>" + cols.map(function (c) { return "<th data-k='" + esc(c) + "'>" + esc(c) + "</th>"; }).join("") + "</tr></thead>");
  var tbody = el("<tbody></tbody>");
  var pending = screenState.sortKey ? sortRows(rows, screenState.sortKey, screenState.sortAsc) : rows;
  pending.forEach(function (r) {
    var tr = el("<tr>" + cols.map(function (c) {
      var raw = r[c];
      if (raw == null || raw === "") return "<td>—</td>";
      var txt = BIG_NUMBER_COLS.indexOf(c) !== -1 ? humanNumber(raw) : String(raw).trim();
      var n = numval(txt);
      var cls = "";
      if (typeof n === "number" && CHANGE_COLS.indexOf(c) !== -1) cls = n >= 0 ? ' class="pos"' : ' class="neg"';
      return "<td" + cls + ">" + esc(txt) + "</td>";
    }).join("") + "</tr>");
    tr.addEventListener("click", function () { openTicker(String(r.Ticker || "").toUpperCase()); });
    tbody.appendChild(tr);
  });
  table.appendChild(thead);
  table.appendChild(tbody);
  wrap.appendChild(table);
  $$("th", thead).forEach(function (th) {
    th.addEventListener("click", function () {
      var k = th.dataset.k;
      if (screenState.sortKey === k) screenState.sortAsc = !screenState.sortAsc;
      else { screenState.sortKey = k; screenState.sortAsc = true; }
      renderResultTable();
    });
  });
  return wrap;
}

function loadScreener() {
  var pane = $("#pane-screener");
  pane.innerHTML = '<div class="loading"><span class="spin"></span> Loading screener presets…</div>';
  fetchJSON("/api/presets")
    .then(function (data) {
      window.__presets = data.presets || [];
      renderScreener();
    })
    .catch(function (e) { failBanner("Loading the screener", e); });
}

/* ---------------- ticker detail ---------------- */

function openTicker(sym) {
  if (!sym) return;
  var ov = $("#ticker-view");
  $("#ticker-body").innerHTML = '<div class="loading" style="padding-top:60px"><span class="spin"></span> Loading ' + esc(sym) + "…</div>";
  ov.hidden = false;
  $("#ticker-watch").textContent = "Add to watchlist";
  fetchJSON("/api/ticker/" + encodeURIComponent(sym))
    .then(function (data) { return drawTicker(data); })
    .catch(function (e) {
      $("#ticker-body").innerHTML = "";
      failBanner("Loading " + sym, e);
    });
}

function drawTicker(data) {
  var d = data;
  setMarket(d.market);
  $("#ticker-name").innerHTML = "<h3>" + esc(d.ticker) + "</h3><div class='sub'>Open on Finviz:</div>";
  $("#ticker-name").querySelector(".sub").innerHTML =
    'Open on Finviz: <a href="https://finviz.com/quote.ashx?t=' + encodeURIComponent(d.ticker)
    + '" target="_blank" rel="noopener">' + esc(d.ticker) + "</a>";

  var watch = $("#ticker-watch");
  watch.onclick = function () {
    fetchJSON("/api/watchlist-add?t=" + encodeURIComponent(d.ticker))
      .then(function (r) { watch.textContent = "Added ✓"; showBanner("Added " + d.ticker + " to your watchlist.", "ok"); })
      .catch(function (e) { failBanner("Adding " + d.ticker, e); });
  };

  var body = $("#ticker-body");
  body.innerHTML = "";

  var last = d.bars && d.bars.length ? d.bars[d.bars.length - 1].c : (d.fundament && d.fundament["Price"] != null ? Number(String(d.fundament["Price"]).replace(/[$%,]/g, "")) || null : null);
  var change = d.bars && d.bars.length >= 2 ? d.bars[d.bars.length - 1].c - d.bars[d.bars.length - 2].c : null;
  var pct = change != null && last ? (change / (last - change) * 100) : null;

  var header = el('<div class="qt"><span class="price">' + (last != null ? esc(last.toFixed(2)) : "—") + "</span>"
    + '<span class="change ' + (change != null ? (change >= 0 ? "chip-good" : "chip-bad") : "chip-muted") + '">'
    + (change != null ? (change >= 0 ? "▲ Up " : "▼ Down ") + (change >= 0 ? "+" : "") + change.toFixed(2) + " (" + (pct >= 0 ? "+" : "") + pct.toFixed(2) + "%)" : "—") + "</span></div>");
  body.appendChild(header);

  var gridEl = el('<div class="two-col"></div>');
  var left = el('<div class="dpanel"><h4>Price chart</h4><div id="chart-box"></div>'
    + '<p class="muted" style="margin:8px 0 0">Daily candles, ~12 months, via Yahoo Finance. '
    + (d.signals_error ? "Signals unavailable: " + esc(d.signals_error) : "") + "</p></div>");
  var right = el('<div class="dpanel"><h4>Signals (same math as the daily check)</h4><div id="sig-grid"></div>'
    + '<h4 style="margin-top:18px">Key stats</h4><dl class="kv" id="fund-kv"></dl></div>');
  gridEl.appendChild(left);
  gridEl.appendChild(right);
  body.appendChild(gridEl);

  drawChart(d.bars || []);

  var sigGrid = $("#sig-grid");
  var sig = d.signals;
  if (sig) {
    sigGrid.appendChild(sigCell("Confluence", (sig.confluence.direction || "mixed") + " · " + sig.confluence.bullish_count + "▲ / " + sig.confluence.bearish_count + "▼", sig.confluence.direction === "bullish" ? "good" : sig.confluence.direction === "bearish" ? "bad" : ""));
    sigGrid.appendChild(sigCell("EMA 20/50", sig.ema_cross || "none", sig.ema_cross === "bullish" ? "good" : sig.ema_cross === "bearish" ? "bad" : ""));
    sigGrid.appendChild(sigCell("50/200 SMA", sig.ma_cross || "none", sig.ma_cross === "golden" ? "good" : sig.ma_cross === "death" ? "bad" : ""));
    sigGrid.appendChild(sigCell("RSI (14)", sig.rsi14 != null ? sig.rsi14.toFixed(1) + " " + sig.rsi_signal : "—", sig.rsi_signal === "oversold" ? "good" : sig.rsi_signal === "overbought" ? "bad" : ""));
    sigGrid.appendChild(sigCell("80-EMA", sig.ema80_react || "none", sig.ema80_react === "bounce" ? "good" : sig.ema80_react === "reject" ? "bad" : ""));
    // from_52w_high is a fraction (-0.0387), not a percent
    sigGrid.appendChild(sigCell("52-week", (sig.near_52w || "none") + " · "
      + fmtPct(sig.from_52w_high != null ? sig.from_52w_high * 100 : null) + " from high", ""));
  } else if (d.signals_error) {
    sigGrid.appendChild(el('<p class="muted">' + esc(d.signals_error) + "</p>"));
  } else {
    sigGrid.appendChild(el('<p class="muted">No signals — not enough price history.</p>'));
  }

  var kv = $("#fund-kv");
  var pair = Object.keys(d.fundament).slice(0, 26);
  pair.forEach(function (f) {
    var v = d.fundament[f];
    kv.appendChild(el("<dt>" + esc(f) + "</dt><dd>" + esc(v == null ? "—" : v) + "</dd>"));
  });

  // news
  if (d.news && d.news.length) {
    var np = el('<div class="dpanel"><h4>Recent news (' + d.news.length + ')</h4><ul class="newslist"></ul></div>');
    var ul = $("ul", np);
    d.news.slice(0, 10).forEach(function (n) {
      var li = el("<li></li>");
      var a = el('<a target="_blank" rel="noopener"></a>');
      a.textContent = String(n.Title || "").replace(/\s+/g, " ").trim();
      a.href = n.Link || "#";
      li.appendChild(a);
      if (n.Date) li.appendChild(el('<span class="d"> — ' + esc(n.Date) + "</span>"));
      ul.appendChild(li);
    });
    body.appendChild(np);
  } else if (d.news_error) {
    body.appendChild(el('<div class="dpanel"><h4>News</h4><p class="muted">Could not load news: ' + esc(d.news_error) + "</p></div>"));
  }

  // insider trades
  if (d.insiders && d.insiders.length) {
    var ip = el('<div class="dpanel"><h4>Insider trades (most recent)</h4><div class="tablewrap" tabindex="0"><table class="dt"></table></div></div>');
    var cols = ["Insider Trading", "Relationship", "Date", "Transaction", "Cost", "#Shares", "Value ($)"];
    var colsThere = cols.filter(function (c) { return d.insiders[0][c] != null; });
    var th = el("<thead><tr>" + colsThere.map(function (c) { return "<th>" + esc(c) + "</th>"; }).join("") + "</tr></thead>");
    var tb = el("<tbody>" + d.insiders.slice(0, 8).map(function (r) {
      return "<tr>" + colsThere.map(function (c) { return "<td>" + esc(r[c] != null ? r[c] : "—") + "</td>"; }).join("") + "</tr>";
    }).join("") + "</tbody>");
    var table = $("table", ip);
    table.appendChild(th); table.appendChild(tb);
    body.appendChild(ip);
  }
}

function sigCell(label, value, cls) {
  return el('<div class="sigcell ' + cls + '"><span class="k">' + esc(label) + "</span>" + esc(value) + "</div>");
}

function drawChart(bars) {
  var box = $("#chart-box");
  box.innerHTML = "";
  if (!bars || bars.length < 2) {
    box.appendChild(el('<p class="muted">No price history to chart.</p>'));
    return;
  }
  if (window.LightweightCharts) {
    try {
      var chart = LightweightCharts.createChart(box, {
        height: 380,
        layout: { background: { type: "solid", color: "#ffffff" }, textColor: "#57564f" },
        grid: { vertLines: { color: "#f0efec" }, horzLines: { color: "#f0efec" } },
        timeScale: { borderColor: "#e6e5e0" },
      });
      var series = chart.addCandlestickSeries({
        upColor: "#00701f", downColor: "#b5292a",
        borderUpColor: "#00701f", borderDownColor: "#b5292a",
        wickUpColor: "#00701f", wickDownColor: "#b5292a",
      });
      series.setData(bars.map(function (b) {
        return { time: String(b.t).slice(0, 10), open: b.o, high: b.h, low: b.l, close: b.c };
      }));
      chart.timeScale().fitContent();
      return;
    } catch (e) { console.error("chart error", e); box.innerHTML = ""; }
  }
  // SVG fallback line
  var closes = bars.map(function (b) { return b.c; });
  var w = 980, h = 380, pad = 8;
  var min = Math.min.apply(null, closes), max = Math.max.apply(null, closes);
  var span = (max - min) || 1;
  var line = closes.map(function (v, i) {
    return (pad + (i / (closes.length - 1)) * (w - 2 * pad)).toFixed(1) + "," + (h - pad - ((v - min) / span) * (h - 2 * pad)).toFixed(1);
  });
  var good = closes[closes.length - 1] >= closes[0];
  var stroke = good ? "#00701f" : "#b5292a";
  var svg = el('<svg viewBox="0 0 ' + w + " " + h + '" aria-label="Price, last ~12 months">'
    + '<polyline fill="none" stroke="' + stroke + '" stroke-width="2" points="' + line.join(" ") + '"/></svg>');
  box.appendChild(svg);
}

/* ---------------- wiring ---------------- */

var load = { watchlist: loadWatchlist, map: loadMap, screener: loadScreener };

function boot() {
  $$(".tab").forEach(function (t) {
    t.addEventListener("click", function () { switchTab(t.dataset.tab); });
  });
  $("#search-form").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var sym = $("#search-input").value.trim().toUpperCase();
    if (sym) openTicker(sym);
  });
  $("#ticker-back").addEventListener("click", function () { $("#ticker-view").hidden = true; });
  $("#text-menu-btn").addEventListener("click", function () {
    fetchJSON("/api/text-menu")
      .then(function () { showBanner("Opened the Text menu in a new window.", "ok"); })
      .catch(function (e) { failBanner("Opening the Text menu", e); });
  });
  $("#footer-note").textContent = "Finviz Research · numbers fetched live from Finviz & Yahoo · no AI tokens spent · screenshots/CSVs save to Desktop → finviz-research";
  switchTab("watchlist");
}

document.addEventListener("DOMContentLoaded", boot);