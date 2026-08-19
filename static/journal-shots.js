/*
 * journal-shots.js — chart screenshots for a single-file trade journal.
 *
 * The chart you were looking at is part of the thesis, so the journal stores it
 * alongside one and locks it at the same moment. Everything here exists to make
 * that survivable in a file that has no server and no disk of its own.
 *
 * The constraint that shapes all of it: a page opened from file:// gets one
 * localStorage bucket of roughly 5 MB, shared with every trade record. A raw
 * screenshot off a 4K monitor is 1-3 MB, so three of them would end the
 * journal — and it would end it by throwing a quota error at save time, after
 * the trade is already in memory and appears to have been logged.
 *
 * So images are re-encoded on the way in rather than stored as handed over:
 * downscaled to a long edge that still reads a chart, re-encoded as JPEG, and
 * pushed under a per-image byte cap by walking quality and then size down until
 * they fit. A 2.4 MB PNG lands around 90 KB. The budget is then accounted
 * against the serialized store, not guessed, and an image that would not fit is
 * refused before it can displace a trade.
 *
 * shrink() needs a canvas and so only runs in a browser. Everything else is
 * arithmetic and is tested under node by static/journal-shots.test.js.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.JournalShots = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // 1280 on the long edge keeps candle bodies and axis labels legible while
  // costing a tenth of the original. Past ~1600 the extra pixels are showing
  // detail no screenshot of a chart actually carries.
  const MAX_EDGE = 1280;
  const MIN_EDGE = 640;
  const QUALITY = [0.72, 0.62, 0.52, 0.42];
  // 140 KB, chosen against the budget rather than by taste: base64 inflates it
  // to ~187 K characters, so twenty worst-case screenshots still leave room in
  // the 4 MB store for the trade records they belong to.
  const PER_IMAGE_CAP = 140 * 1024;

  /*
   * Chrome gives file:// documents about 5 MB of localStorage, counted in
   * characters of the stored string. Base64 is ASCII, so a character is a byte
   * and the arithmetic stays honest. Stopping at 4 MB leaves room for the trade
   * records themselves plus whatever the next screenshot turns out to weigh —
   * a budget with no headroom fails on the save that matters.
   */
  const BUDGET = 4 * 1024 * 1024;

  /*
   * Fit within a bounding square, preserving aspect ratio, never upscaling.
   * Returns integers, because a canvas with fractional dimensions silently
   * rounds and then the image is a pixel off its own aspect ratio.
   */
  function fitWithin(width, height, maxEdge) {
    const w = Number(width), h = Number(height);
    const max = Number(maxEdge) || MAX_EDGE;
    if (!(w > 0) || !(h > 0)) return null;
    const scale = Math.min(1, max / Math.max(w, h));
    return {
      width: Math.max(1, Math.round(w * scale)),
      height: Math.max(1, Math.round(h * scale)),
      scale: scale,
      scaled: scale < 1,
    };
  }

  /* Decoded size of a data URI's payload — the number a person recognises as
     "how big is that image", as opposed to how much store it occupies. */
  function dataUriBytes(uri) {
    const s = String(uri || "");
    const at = s.indexOf(",");
    if (at === -1) return 0;
    const body = s.slice(at + 1);
    if (!/;base64$|;base64,/.test(s.slice(0, at + 8))) return body.length;
    const pad = (body.match(/=+$/) || [""])[0].length;
    return Math.max(0, Math.floor(body.length * 3 / 4) - pad);
  }

  /* What the store actually costs, measured rather than estimated. */
  function storageChars(trades) {
    try { return JSON.stringify(trades || []).length; }
    catch (e) { return 0; }
  }

  function budget(trades, total) {
    const cap = total || BUDGET;
    const used = storageChars(trades);
    return {
      used: used,
      total: cap,
      free: Math.max(0, cap - used),
      pct: cap > 0 ? Math.min(100, used / cap * 100) : 0,
    };
  }

  /*
   * Would adding `chars` more characters fit?
   *
   * Asked before the trade is pushed, not after the save throws. By then the
   * record is in memory and looks saved, which is the failure mode this whole
   * file is arranged to avoid.
   */
  function canAccept(trades, chars, total) {
    const b = budget(trades, total);
    const need = Number(chars) || 0;
    return { ok: need <= b.free, free: b.free, need: need, over: Math.max(0, need - b.free) };
  }

  function fmtBytes(n) {
    const v = Number(n) || 0;
    if (v < 1024) return v + " B";
    if (v < 1024 * 1024) return (v / 1024).toFixed(0) + " KB";
    return (v / (1024 * 1024)).toFixed(1) + " MB";
  }

  /* -------------------------------------------------------------- browser */

  function loadImage(blob) {
    if (typeof createImageBitmap === "function") {
      return createImageBitmap(blob);
    }
    return new Promise(function (resolve, reject) {
      const url = URL.createObjectURL(blob);
      const img = new Image();
      img.onload = function () { URL.revokeObjectURL(url); resolve(img); };
      img.onerror = function () { URL.revokeObjectURL(url); reject(new Error("not an image")); };
      img.src = url;
    });
  }

  /*
   * Re-encode a pasted or dropped image down to something a journal can hold.
   *
   * Walks quality down first and only then gives up pixels, because a chart
   * survives JPEG artefacts far better than it survives being shrunk — losing
   * the axis labels costs more than a little mush around the candles. Resolves
   * to { uri, bytes, width, height, from } or rejects with a readable reason.
   */
  function shrink(blob, opts) {
    const o = opts || {};
    const cap = o.cap || PER_IMAGE_CAP;
    const originalBytes = blob && blob.size ? blob.size : 0;

    return loadImage(blob).then(function (img) {
      const sw = img.width, sh = img.height;
      let edge = o.maxEdge || MAX_EDGE;
      let best = null;

      while (edge >= MIN_EDGE) {
        const fit = fitWithin(sw, sh, edge);
        const canvas = document.createElement("canvas");
        canvas.width = fit.width;
        canvas.height = fit.height;
        const ctx = canvas.getContext("2d");
        // A screenshot with transparency composited straight to JPEG comes out
        // black. Charts are opaque, but a snip with rounded corners is not.
        ctx.fillStyle = "#ffffff";
        ctx.fillRect(0, 0, fit.width, fit.height);
        ctx.drawImage(img, 0, 0, fit.width, fit.height);

        for (let i = 0; i < QUALITY.length; i++) {
          const uri = canvas.toDataURL("image/jpeg", QUALITY[i]);
          const bytes = dataUriBytes(uri);
          if (!best || bytes < best.bytes) {
            best = { uri: uri, bytes: bytes, width: fit.width, height: fit.height };
          }
          if (bytes <= cap) {
            return { uri: uri, bytes: bytes, width: fit.width, height: fit.height,
                     from: originalBytes, chars: uri.length };
          }
        }
        edge = Math.round(edge * 0.75);
      }
      // Nothing got under the cap. Hand back the smallest attempt rather than
      // failing outright — the caller still has the budget check ahead of it.
      best.from = originalBytes;
      best.chars = best.uri.length;
      return best;
    });
  }

  /* Pull image blobs out of a paste or drop event, ignoring everything else. */
  function blobsFrom(event) {
    const out = [];
    const dt = (event.clipboardData || event.dataTransfer);
    if (!dt) return out;
    if (dt.files && dt.files.length) {
      for (let i = 0; i < dt.files.length; i++) {
        if (/^image\//.test(dt.files[i].type)) out.push(dt.files[i]);
      }
    }
    if (!out.length && dt.items) {
      for (let i = 0; i < dt.items.length; i++) {
        const it = dt.items[i];
        if (it.kind === "file" && /^image\//.test(it.type)) {
          const f = it.getAsFile();
          if (f) out.push(f);
        }
      }
    }
    return out;
  }

  return {
    MAX_EDGE: MAX_EDGE,
    MIN_EDGE: MIN_EDGE,
    QUALITY: QUALITY,
    PER_IMAGE_CAP: PER_IMAGE_CAP,
    BUDGET: BUDGET,
    fitWithin: fitWithin,
    dataUriBytes: dataUriBytes,
    storageChars: storageChars,
    budget: budget,
    canAccept: canAccept,
    fmtBytes: fmtBytes,
    shrink: shrink,
    blobsFrom: blobsFrom,
  };
});
