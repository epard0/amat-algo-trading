/* Kalshi 15m bot dashboard.
 *
 * No build step and no external libraries: the charts are drawn on a canvas by
 * hand. This page is served from a trading host where a CDN fetch is both a
 * dependency on the network and a third party that gets told when you are
 * looking at your P&L.
 *
 * Money arrives from the API as strings (Decimal server-side) and is parsed to
 * float only for pixel positions and display formatting, never re-serialised.
 */

const $ = (sel) => document.querySelector(sel);
const state = {
  overview: null,
  trades: [],
  chart: null,
  equity: [],
  // Overwritten from /api/config at startup; the server's --live/--paper flag
  // decides which book opens first. Paper is the safe default if config fails.
  mode: "paper",
  // The bot's paper/live switch, as reported by /api/mode. Distinct from
  // `mode` above, which only chooses which book this page displays.
  trading: null,
  dimension: "asset",
  asset: "BTC",
  hours: 12,
  timer: null,
};

const num = (v) => (v === null || v === undefined || v === "" ? NaN : parseFloat(v));
const money = (v) => {
  const n = num(v);
  return isNaN(n) ? "—" : (n < 0 ? "-$" : "$") + Math.abs(n).toFixed(2);
};
const pct = (v, digits = 1) => {
  const n = num(v);
  return isNaN(n) ? "—" : (n * 100).toFixed(digits) + "%";
};
const signClass = (v) => {
  const n = num(v);
  if (isNaN(n) || n === 0) return "neutral";
  return n > 0 ? "pos" : "neg";
};
const fmtTime = (iso) =>
  iso ? new Date(iso).toLocaleString(undefined, { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—";
const fmtClock = (iso) =>
  iso ? new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }) : "";

async function getJSON(url) {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`${url} → ${res.status}`);
  return res.json();
}

/* ============================================================== overview === */

function renderHealth(health, generatedAt) {
  const pill = $("#health-pill");
  pill.textContent = health.ok ? "healthy" : `${health.issues.length} issue(s)`;
  pill.className = "pill " + (health.ok ? "pill-ok" : "pill-bad");

  const age = health.seconds_since_decision;
  $("#generated").textContent =
    `updated ${fmtTime(generatedAt)}` +
    (age != null ? ` · last decision ${(age / 60).toFixed(1)} min ago` : " · no decisions yet");

  const box = $("#issues");
  if (health.ok) {
    box.hidden = true;
  } else {
    box.hidden = false;
    box.innerHTML =
      "<strong>Health</strong><ul>" +
      health.issues.map((i) => `<li>${escapeHTML(i)}</li>`).join("") +
      "</ul>";
  }
}

function tile(label, value, cls = "neutral", meta = "") {
  return `<div class="tile">
    <div class="label">${label}</div>
    <div class="value ${cls}">${value}</div>
    ${meta ? `<div class="meta">${meta}</div>` : ""}
  </div>`;
}

function renderTiles() {
  const o = state.overview;
  if (!o) return;
  const m = o.modes[state.mode];
  const model = o.model;

  $("#tiles").innerHTML = [
    tile("Net P&L", money(m.net_pnl), signClass(m.net_pnl), `${m.settled} settled`),
    tile("ROI on stake", pct(m.roi, 2), signClass(m.roi), `${money(m.staked)} staked`),
    tile("Win rate", pct(m.win_rate), "neutral", `${m.wins}W · ${m.losses}L · ${m.voids}V`),
    tile("Max drawdown", money(m.max_drawdown), num(m.max_drawdown) < 0 ? "neg" : "neutral",
         `longest losing run ${m.longest_losing_streak}`),
    tile("Fees paid", money(m.fees_paid), "neutral", `${pct(m.fee_drag, 2)} of stake`),
    tile("Avg per trade", money(m.average_pnl), signClass(m.average_pnl),
         `streak ${escapeHTML(m.streak.kind)} ×${m.streak.length}`),
    tile("Brier score", isNaN(num(model.brier_score)) ? "—" : num(model.brier_score).toFixed(4),
         num(model.brier_score) < 0.25 ? "pos" : "neg", "0.25 = coin flip"),
    tile("Edge shortfall", pct(model.shortfall, 2), signClass(model.shortfall),
         `claimed ${pct(model.claimed_mean_edge, 1)}`),
    tile("Open now", String(m.open), m.open ? "neutral" : "neutral", `${o.funnel.evaluations.toLocaleString()} evals`),
  ].join("");

  // Win rate above 50% with negative P&L is the failure mode this dashboard
  // exists to make impossible to miss: it means the payoffs are wrong, not the
  // direction. Call it out in words rather than leaving it in the numbers.
  const note = $("#mode-note");
  const wr = num(m.win_rate), pnl = num(m.net_pnl);
  if (m.settled >= 3 && wr > 0.5 && pnl < 0) {
    note.innerHTML = `<strong class="neg">Winning ${pct(m.win_rate)} of trades while losing ${money(m.net_pnl)}.</strong>
      Entry prices are too high for the hit rate: each contract must win more often than its price implies just to break even.
      Check the calibration panel and fee drag (${pct(m.fee_drag, 2)}).`;
  } else if (m.settled === 0) {
    note.textContent = "No settled trades in this mode yet.";
  } else {
    note.textContent = "";
  }
}

/* ========================================================== calibration === */

function renderCalibration() {
  const model = state.overview?.model;
  const buckets = model?.calibration ?? [];
  $("#brier-badge").textContent =
    isNaN(num(model?.brier_score)) ? "Brier —" : `Brier ${num(model.brier_score).toFixed(4)}`;

  const canvas = $("#calibration");
  const ctx = setupCanvas(canvas);
  const W = canvas.clientWidth, H = 240;
  const pad = { l: 46, r: 14, t: 14, b: 34 };
  const pw = W - pad.l - pad.r, ph = H - pad.t - pad.b;

  ctx.clearRect(0, 0, W, H);
  const css = getComputedStyle(document.documentElement);
  const cLine = css.getPropertyValue("--line").trim();
  const cMuted = css.getPropertyValue("--muted").trim();

  // axes + perfect-calibration diagonal
  ctx.strokeStyle = cLine; ctx.lineWidth = 1;
  for (let f = 0; f <= 1.0001; f += 0.25) {
    const y = pad.t + ph - f * ph;
    ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(pad.l + pw, y); ctx.stroke();
    ctx.fillStyle = cMuted; ctx.font = "10px ui-monospace, monospace"; ctx.textAlign = "right";
    ctx.fillText((f * 100).toFixed(0) + "%", pad.l - 6, y + 3);
  }
  ctx.save();
  ctx.setLineDash([4, 4]); ctx.strokeStyle = cMuted; ctx.globalAlpha = 0.6;
  ctx.beginPath(); ctx.moveTo(pad.l, pad.t + ph); ctx.lineTo(pad.l + pw, pad.t); ctx.stroke();
  ctx.restore();

  if (!buckets.length) {
    ctx.fillStyle = cMuted; ctx.textAlign = "center"; ctx.font = "12px system-ui";
    ctx.fillText("no settled trades to calibrate yet", pad.l + pw / 2, pad.t + ph / 2);
    $("#calibration-table").innerHTML = "";
    return;
  }

  const bw = pw / buckets.length;
  buckets.forEach((b, i) => {
    const x = pad.l + i * bw;
    const predicted = num(b.predicted), actual = num(b.actual);
    // predicted marker
    const py = pad.t + ph - predicted * ph;
    ctx.fillStyle = cMuted;
    ctx.fillRect(x + bw * 0.25, py - 1, bw * 0.5, 2);
    // actual bar
    const ay = pad.t + ph - actual * ph;
    const over = actual < predicted;
    ctx.fillStyle = over ? css.getPropertyValue("--loss").trim() : css.getPropertyValue("--win").trim();
    ctx.globalAlpha = 0.75;
    ctx.fillRect(x + bw * 0.32, ay, bw * 0.36, pad.t + ph - ay);
    ctx.globalAlpha = 1;
    // label
    ctx.fillStyle = cMuted; ctx.font = "9.5px ui-monospace, monospace"; ctx.textAlign = "center";
    ctx.fillText(b.label, x + bw / 2, H - 18);
    ctx.fillText(`n=${b.count}`, x + bw / 2, H - 6);
  });

  $("#calibration-table").innerHTML = `<table><thead><tr>
      <th>Bucket</th><th class="num">n</th><th class="num">Predicted</th>
      <th class="num">Actual</th><th class="num">Gap</th><th class="num">P&L</th>
    </tr></thead><tbody>${buckets.map((b) => `<tr>
      <td>${b.label}</td>
      <td class="num">${b.count}</td>
      <td class="num">${pct(b.predicted)}</td>
      <td class="num">${pct(b.actual)}</td>
      <td class="num ${signClass(b.gap)}">${num(b.gap) > 0 ? "+" : ""}${pct(b.gap)}</td>
      <td class="num ${signClass(b.net_pnl)}">${money(b.net_pnl)}</td>
    </tr>`).join("")}</tbody></table>`;
}

/* =============================================================== funnel === */

function renderFunnel() {
  const f = state.overview?.funnel;
  if (!f) return;
  const total = f.evaluations || 1;
  const rows = [
    { name: "evaluated", count: f.evaluations, kind: "" },
    ...f.blocked.map((b) => ({ name: b.gate, count: b.count, kind: "" })),
    { name: "traded", count: f.traded, kind: "gap-pos" },
    ...(f.errors ? [{ name: "errored", count: f.errors, kind: "gap-neg" }] : []),
  ];
  $("#funnel").innerHTML = rows.map((r) => `
    <div class="funnel-row">
      <div class="name">${escapeHTML(r.name)}</div>
      <div class="bar ${r.kind}"><i style="width:${Math.max(0.6, (r.count / total) * 100)}%"></i></div>
      <div class="count">${r.count.toLocaleString()} <span style="color:var(--dim)">${((r.count / total) * 100).toFixed(2)}%</span></div>
    </div>`).join("");
}

/* ============================================================ breakdown === */

function renderBreakdown() {
  const data = state.overview?.breakdowns?.[state.dimension] ?? {};
  const entries = Object.entries(data).filter(([, v]) => v.settled > 0);
  if (!entries.length) { $("#breakdown").innerHTML = `<div class="empty">no settled trades yet</div>`; return; }
  entries.sort((a, b) => num(b[1].net_pnl) - num(a[1].net_pnl));
  $("#breakdown").innerHTML = `<table><thead><tr>
      <th>${state.dimension}</th><th class="num">Trades</th><th class="num">W/L</th>
      <th class="num">Win rate</th><th class="num">ROI</th><th class="num">Net P&L</th>
    </tr></thead><tbody>${entries.map(([k, v]) => `<tr>
      <td>${escapeHTML(k)}</td>
      <td class="num">${v.settled}</td>
      <td class="num">${v.wins}/${v.losses}</td>
      <td class="num">${pct(v.win_rate)}</td>
      <td class="num ${signClass(v.roi)}">${pct(v.roi, 1)}</td>
      <td class="num ${signClass(v.net_pnl)}">${money(v.net_pnl)}</td>
    </tr>`).join("")}</tbody></table>`;
}

/* =============================================================== equity === */

function renderEquity() {
  const canvas = $("#equity");
  const ctx = setupCanvas(canvas);
  const W = canvas.clientWidth, H = 260;
  const pad = { l: 54, r: 14, t: 14, b: 26 };
  const pw = W - pad.l - pad.r, ph = H - pad.t - pad.b;
  const css = getComputedStyle(document.documentElement);
  ctx.clearRect(0, 0, W, H);

  const pts = state.equity;
  if (pts.length < 1) {
    ctx.fillStyle = css.getPropertyValue("--muted").trim();
    ctx.textAlign = "center"; ctx.font = "12px system-ui";
    ctx.fillText("no settled trades yet", W / 2, H / 2);
    $("#equity-note").textContent = "";
    return;
  }

  const vals = pts.map((p) => num(p.cumulative)).concat([0]);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  const span = (hi - lo) || 1;
  lo -= span * 0.12; hi += span * 0.12;
  const X = (i) => pad.l + (pts.length === 1 ? pw / 2 : (i / (pts.length - 1)) * pw);
  const Y = (v) => pad.t + ph - ((v - lo) / (hi - lo)) * ph;

  ctx.strokeStyle = css.getPropertyValue("--line").trim();
  for (let g = 0; g <= 4; g++) {
    const v = lo + ((hi - lo) * g) / 4, y = Y(v);
    ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(pad.l + pw, y); ctx.stroke();
    ctx.fillStyle = css.getPropertyValue("--muted").trim();
    ctx.font = "10px ui-monospace, monospace"; ctx.textAlign = "right";
    ctx.fillText("$" + v.toFixed(2), pad.l - 6, y + 3);
  }
  // break-even line: the only threshold that matters
  ctx.save(); ctx.setLineDash([3, 3]);
  ctx.strokeStyle = css.getPropertyValue("--muted").trim();
  ctx.beginPath(); ctx.moveTo(pad.l, Y(0)); ctx.lineTo(pad.l + pw, Y(0)); ctx.stroke();
  ctx.restore();

  const last = num(pts[pts.length - 1].cumulative);
  const col = css.getPropertyValue(last >= 0 ? "--win" : "--loss").trim();
  const grad = ctx.createLinearGradient(0, pad.t, 0, pad.t + ph);
  grad.addColorStop(0, col + "44"); grad.addColorStop(1, col + "05");
  ctx.beginPath(); ctx.moveTo(X(0), Y(0));
  pts.forEach((p, i) => ctx.lineTo(X(i), Y(num(p.cumulative))));
  ctx.lineTo(X(pts.length - 1), Y(0)); ctx.closePath();
  ctx.fillStyle = grad; ctx.fill();

  ctx.beginPath();
  pts.forEach((p, i) => (i ? ctx.lineTo(X(i), Y(num(p.cumulative))) : ctx.moveTo(X(i), Y(num(p.cumulative)))));
  ctx.strokeStyle = col; ctx.lineWidth = 2; ctx.stroke();

  pts.forEach((p, i) => {
    ctx.beginPath(); ctx.arc(X(i), Y(num(p.cumulative)), 3, 0, Math.PI * 2);
    ctx.fillStyle = css.getPropertyValue(p.status === "WON" ? "--win" : p.status === "LOST" ? "--loss" : "--muted").trim();
    ctx.fill();
  });

  const dd = Math.min(...pts.map((p) => num(p.drawdown)));
  $("#equity-note").textContent =
    `${pts.length} settled · final ${money(last)} · deepest drawdown ${money(dd)} (${state.mode})`;
}

/* ================================================================ chart === */

function setupCanvas(canvas) {
  // Match the backing store to device pixels or every line renders blurry.
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.height / (canvas._dpr || dpr);
  canvas._dpr = dpr;
  canvas.width = Math.max(1, Math.floor(w * dpr));
  canvas.height = Math.max(1, Math.floor(h * dpr));
  canvas.style.height = h + "px";
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return ctx;
}

const chartGeom = { candles: [], x: null, priceTop: 0, priceH: 0, lo: 0, hi: 1, cw: 0, pad: null };

function renderChart() {
  const canvas = $("#chart");
  const data = state.chart;
  const ctx = setupCanvas(canvas);
  const W = canvas.clientWidth, H = 620;
  ctx.clearRect(0, 0, W, H);
  const css = getComputedStyle(document.documentElement);
  const C = (n) => css.getPropertyValue(n).trim();

  if (!data || data.error || !data.candles?.length) {
    ctx.fillStyle = C("--muted"); ctx.textAlign = "center"; ctx.font = "13px system-ui";
    ctx.fillText(data?.error || "no candle data", W / 2, H / 2);
    $("#chart-note").textContent = data?.error || "";
    return;
  }

  const candles = data.candles.map((c) => ({
    t: new Date(c.t), o: +c.o, h: +c.h, l: +c.l, c: +c.c, v: +c.v,
  }));
  const ind = data.indicators || [];

  // Panels: price dominates; volatility and regime get just enough room to
  // read as context rather than competing for attention.
  const pad = { l: 62, r: 16, t: 12, b: 22 };
  const priceH = 360, volH = 92, regH = 26, gap = 16;
  const priceTop = pad.t;
  const volTop = priceTop + priceH + gap;
  const regTop = volTop + volH + gap;
  const pw = W - pad.l - pad.r;

  const n = candles.length;
  const cw = pw / n;
  const X = (i) => pad.l + i * cw + cw / 2;

  let lo = Math.min(...candles.map((c) => c.l));
  let hi = Math.max(...candles.map((c) => c.h));
  // Strikes must be visible or a dot can sit off-canvas and silently vanish.
  (data.trades || []).forEach((t) => {
    const p = num(t.target_price);
    if (!isNaN(p)) { lo = Math.min(lo, p); hi = Math.max(hi, p); }
  });
  const pspan = (hi - lo) || 1;
  lo -= pspan * 0.06; hi += pspan * 0.06;
  const Y = (p) => priceTop + priceH - ((p - lo) / (hi - lo)) * priceH;

  Object.assign(chartGeom, { candles, x: X, priceTop, priceH, lo, hi, cw, pad, ind, data });

  /* --- 15-minute market bands: the whole point of the view --------------- */
  const stepMin = data.candle_interval_minutes;
  const perMarket = 15 / stepMin;           // candles per 15-minute market
  if (perMarket >= 1) {
    candles.forEach((c, i) => {
      const m = c.t.getUTCMinutes();
      if (m % 15 !== 0) return;             // boundary candle only
      const marketIndex = Math.floor((c.t.getTime() / 60000) / 15);
      const x0 = pad.l + i * cw;
      const x1 = Math.min(pad.l + pw, x0 + perMarket * cw);
      if (marketIndex % 2 === 0) {
        ctx.fillStyle = "rgba(88,166,255,0.05)";
        ctx.fillRect(x0, priceTop, x1 - x0, priceH);
      }
      ctx.strokeStyle = "rgba(88,166,255,0.22)"; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x0, priceTop); ctx.lineTo(x0, regTop + regH); ctx.stroke();
      // Label every other boundary when space is tight.
      if (perMarket * cw > 44 || marketIndex % 2 === 0) {
        ctx.fillStyle = C("--dim"); ctx.font = "9px ui-monospace, monospace"; ctx.textAlign = "left";
        ctx.fillText(fmtClock(c.t.toISOString()), x0 + 3, priceTop + 10);
      }
    });
  }

  /* --- price grid ------------------------------------------------------- */
  ctx.strokeStyle = C("--line"); ctx.lineWidth = 1;
  for (let g = 0; g <= 5; g++) {
    const p = lo + ((hi - lo) * g) / 5, y = Y(p);
    ctx.globalAlpha = 0.5;
    ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(pad.l + pw, y); ctx.stroke();
    ctx.globalAlpha = 1;
    ctx.fillStyle = C("--muted"); ctx.font = "10px ui-monospace, monospace"; ctx.textAlign = "right";
    ctx.fillText(p >= 1000 ? p.toFixed(0) : p.toFixed(4), pad.l - 7, y + 3);
  }

  /* --- candles ---------------------------------------------------------- */
  const bodyW = Math.max(1, Math.min(9, cw * 0.68));
  candles.forEach((c, i) => {
    const up = c.c >= c.o;
    const col = C(up ? "--win" : "--loss");
    ctx.strokeStyle = col; ctx.fillStyle = col;
    const x = X(i);
    ctx.beginPath(); ctx.moveTo(x, Y(c.h)); ctx.lineTo(x, Y(c.l));
    ctx.lineWidth = Math.max(1, bodyW * 0.16); ctx.stroke();
    const yO = Y(c.o), yC = Y(c.c);
    ctx.fillRect(x - bodyW / 2, Math.min(yO, yC), bodyW, Math.max(1, Math.abs(yC - yO)));
  });

  /* --- trade markers ---------------------------------------------------- */
  const t0 = candles[0].t.getTime(), tEnd = candles[n - 1].t.getTime() + stepMin * 60000;
  const timeToX = (ms) => pad.l + ((ms - t0) / (tEnd - t0)) * pw;

  (data.evaluations || []).forEach((e) => {
    const ms = new Date(e.timestamp).getTime();
    if (ms < t0 || ms > tEnd) return;
    const p = num(e.target_price);
    if (isNaN(p)) return;
    ctx.beginPath(); ctx.arc(timeToX(ms), Y(p), 2.2, 0, Math.PI * 2);
    ctx.fillStyle = C("--dim"); ctx.globalAlpha = 0.5; ctx.fill(); ctx.globalAlpha = 1;
  });

  const markers = [];
  (data.trades || []).forEach((t) => {
    const ms = new Date(t.timestamp).getTime();
    if (ms < t0 || ms > tEnd) return;
    const p = num(t.target_price);
    if (isNaN(p)) return;
    const x = timeToX(ms), y = Y(p);
    const col = C(t.status === "WON" ? "--win" : t.status === "LOST" ? "--loss" : "--open");

    // strike line: the level the market settles against
    ctx.save();
    ctx.setLineDash([3, 3]); ctx.strokeStyle = col; ctx.globalAlpha = 0.5; ctx.lineWidth = 1;
    const closeMs = t.close_time ? new Date(t.close_time).getTime() : ms + 15 * 60000;
    ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(Math.min(timeToX(closeMs), pad.l + pw), y); ctx.stroke();
    ctx.restore();

    // YES points up, NO points down — direction is readable without the legend
    ctx.beginPath();
    if (t.outcome === "yes") { ctx.moveTo(x, y - 7); ctx.lineTo(x + 6, y + 4); ctx.lineTo(x - 6, y + 4); }
    else { ctx.moveTo(x, y + 7); ctx.lineTo(x + 6, y - 4); ctx.lineTo(x - 6, y - 4); }
    ctx.closePath();
    ctx.fillStyle = col; ctx.fill();
    ctx.strokeStyle = C("--bg"); ctx.lineWidth = 1.5; ctx.stroke();
    markers.push({ x, y, trade: t });
  });
  chartGeom.markers = markers;

  /* --- volatility panel ------------------------------------------------- */
  const fast = ind.map((d) => (d && d.realized_vol_fast) || null);
  const slow = ind.map((d) => (d && d.realized_vol_slow) || null);
  const vMax = Math.max(...fast.concat(slow).filter((v) => v != null), 1e-9);
  const VY = (v) => volTop + volH - (v / vMax) * volH;

  ctx.fillStyle = C("--muted"); ctx.font = "10px ui-monospace, monospace"; ctx.textAlign = "left";
  ctx.fillText("realised volatility / min  — fast (5)  ·· slow (20)", pad.l, volTop - 3);
  ctx.strokeStyle = C("--line");
  ctx.beginPath(); ctx.moveTo(pad.l, volTop + volH); ctx.lineTo(pad.l + pw, volTop + volH); ctx.stroke();

  const drawSeries = (series, colour, dash) => {
    ctx.save(); ctx.setLineDash(dash); ctx.strokeStyle = colour; ctx.lineWidth = 1.4;
    ctx.beginPath(); let started = false;
    series.forEach((v, i) => {
      if (v == null) { started = false; return; }
      const x = X(i), y = VY(v);
      started ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
      started = true;
    });
    ctx.stroke(); ctx.restore();
  };
  drawSeries(slow, C("--dim"), [3, 3]);
  drawSeries(fast, C("--accent"), []);

  /* --- regime strip ----------------------------------------------------- */
  const regimeColour = {
    TREND: "#3fb950", CONSOLIDATION: "#58a6ff", REVERSAL: "#d29922",
    VOLATILE_CHOP: "#f85149", NEUTRAL: "#3d4759",
  };
  ctx.fillStyle = C("--muted"); ctx.font = "10px ui-monospace, monospace"; ctx.textAlign = "left";
  ctx.fillText("regime", pad.l, regTop - 3);
  ind.forEach((d, i) => {
    if (!d || !d.regime) return;
    ctx.fillStyle = regimeColour[d.regime] || "#3d4759";
    ctx.globalAlpha = 0.55 + 0.45 * (d.confidence || 0);
    ctx.fillRect(pad.l + i * cw, regTop, Math.max(1, cw + 0.5), regH);
    ctx.globalAlpha = 1;
  });
  ctx.strokeStyle = C("--line");
  ctx.strokeRect(pad.l, regTop, pw, regH);

  const legend = Object.entries(regimeColour);
  let lx = pad.l;
  ctx.font = "9.5px ui-monospace, monospace"; ctx.textBaseline = "middle";
  legend.forEach(([name, colour]) => {
    ctx.fillStyle = colour; ctx.fillRect(lx, regTop + regH + 8, 9, 9);
    ctx.fillStyle = C("--muted"); ctx.textAlign = "left";
    ctx.fillText(name, lx + 13, regTop + regH + 13);
    lx += 15 + ctx.measureText(name).width + 14;
  });
  ctx.textBaseline = "alphabetic";

  $("#chart-note").textContent =
    `${data.asset} · ${data.pair} · ${n} × ${stepMin}m candles · ` +
    `${(data.trades || []).length} bet(s), ${(data.evaluations || []).length} near-miss evaluation(s) shown. ` +
    data.indicator_note;
}

/* -- hover ---------------------------------------------------------------- */
function onChartMove(ev) {
  const canvas = $("#chart"), tip = $("#tooltip");
  const g = chartGeom;
  if (!g.candles?.length) return;
  const rect = canvas.getBoundingClientRect();
  const mx = ev.clientX - rect.left, my = ev.clientY - rect.top;

  const hit = (g.markers || []).find((m) => Math.hypot(m.x - mx, m.y - my) < 11);
  if (hit) {
    const t = hit.trade;
    tip.innerHTML = `<div class="tt-title">${escapeHTML(t.asset || state.asset)} ${t.outcome.toUpperCase()} · ${t.status}</div>
      <div class="tt-row"><span>ticker</span><span>${escapeHTML(t.ticker || "")}</span></div>
      <div class="tt-row"><span>placed</span><span>${fmtTime(t.timestamp)}</span></div>
      <div class="tt-row"><span>strike</span><span>${escapeHTML(t.target_price ?? "—")}</span></div>
      <hr>
      <div class="tt-row"><span>model P</span><span>${pct(t.model_probability)}</span></div>
      <div class="tt-row"><span>edge</span><span>${pct(t.model_edge)}</span></div>
      <div class="tt-row"><span>entry</span><span>${escapeHTML(t.entry_price ?? "—")}</span></div>
      <div class="tt-row"><span>size</span><span>${escapeHTML(t.contracts ?? "—")}</span></div>
      <div class="tt-row"><span>regime</span><span>${escapeHTML(t.regime || "—")}</span></div>
      <hr>
      <div class="tt-row"><span>P&L</span><span class="${signClass(t.pnl)}">${money(t.pnl)}</span></div>
      <div class="tt-row"><span>mode</span><span>${escapeHTML(t.mode)}</span></div>`;
    positionTip(tip, canvas, mx, my);
    return;
  }

  const i = Math.round((mx - g.pad.l - g.cw / 2) / g.cw);
  if (i < 0 || i >= g.candles.length || mx < g.pad.l) { tip.hidden = true; return; }
  const c = g.candles[i], d = (g.ind || [])[i] || {};
  tip.innerHTML = `<div class="tt-title">${fmtTime(c.t.toISOString())}</div>
    <div class="tt-row"><span>O</span><span>${c.o}</span></div>
    <div class="tt-row"><span>H</span><span>${c.h}</span></div>
    <div class="tt-row"><span>L</span><span>${c.l}</span></div>
    <div class="tt-row"><span>C</span><span>${c.c}</span></div>
    ${d.regime ? `<hr>
    <div class="tt-row"><span>regime</span><span>${d.regime} ${(d.confidence * 100).toFixed(0)}%</span></div>
    <div class="tt-row"><span>vol fast</span><span>${(d.realized_vol_fast * 100).toFixed(4)}%</span></div>
    <div class="tt-row"><span>vol ratio</span><span>${d.vol_ratio}</span></div>
    <div class="tt-row"><span>efficiency</span><span>${d.efficiency_ratio}</span></div>
    <div class="tt-row"><span>drift ×</span><span>${d.drift_multiplier}</span></div>
    <div class="tt-row"><span>shrinkage</span><span>${d.extra_shrinkage}</span></div>` : ""}`;
  positionTip(tip, canvas, mx, my);
}

function positionTip(tip, canvas, mx, my) {
  tip.hidden = false;
  const w = tip.offsetWidth || 230;
  tip.style.left = Math.min(mx + 16, canvas.clientWidth - w - 8) + "px";
  tip.style.top = Math.max(8, my - 12) + "px";
}

/* =============================================================== trades === */

function renderTrades() {
  const q = ($("#trade-filter").value || "").toLowerCase();
  const rows = state.trades.filter((t) =>
    !q || [t.asset, t.ticker, t.status, t.mode, t.outcome, t.regime].join(" ").toLowerCase().includes(q));
  const body = $("#trades tbody");
  if (!rows.length) { body.innerHTML = `<tr><td colspan="13" class="empty">no trades</td></tr>`; return; }
  // Every interpolated value is escaped, including the SQL CHECK-constrained
  // ones. `ticker` and `asset` originate from Kalshi API responses, so the
  // table is rendering third-party strings; constraining the others by schema
  // is not a reason to trust them at the sink.
  const tag = (v) => escapeHTML(v).replace(/[^A-Za-z]/g, "");
  body.innerHTML = rows.map((t) => `<tr>
    <td>${fmtTime(t.placed_at)}</td>
    <td><span class="tag ${tag(t.mode)}">${escapeHTML(t.mode)}</span></td>
    <td>${escapeHTML(t.asset)}</td>
    <td>${escapeHTML(String(t.outcome).toUpperCase())}</td>
    <td class="num">${pct(t.model_probability)}</td>
    <td class="num">${pct(t.model_edge)}</td>
    <td class="num">${escapeHTML(t.entry_price ?? "—")}</td>
    <td class="num">${escapeHTML(t.contracts ?? "—")}</td>
    <td class="num">${money(t.total_debit)}</td>
    <td class="num">${money(t.fees_paid)}</td>
    <td>${escapeHTML(t.regime)}</td>
    <td><span class="tag ${tag(t.status)}">${escapeHTML(t.status)}</span></td>
    <td class="num ${signClass(t.pnl)}">${t.pnl == null ? "—" : money(t.pnl)}</td>
  </tr>`).join("");
}

function escapeHTML(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ============================================================== loading === */

async function loadCore() {
  const [overview, trades, equity] = await Promise.all([
    getJSON("/api/overview"),
    getJSON("/api/trades"),
    getJSON(`/api/equity?mode=${state.mode}`),
  ]);
  state.overview = overview;
  state.trades = trades.trades;
  state.equity = equity.points;

  renderHealth(overview.health, overview.generated_at);
  renderTiles(); renderCalibration(); renderFunnel(); renderBreakdown();
  renderEquity(); renderTrades();
}

async function loadChart() {
  $("#chart-note").textContent = "loading candles…";
  try {
    state.chart = await getJSON(`/api/chart?asset=${state.asset}&hours=${state.hours}`);
  } catch (err) {
    state.chart = { error: String(err) };
  }
  renderChart();
}

async function refresh() {
  loadTradingMode();
  try { await loadCore(); } catch (err) {
    $("#health-pill").textContent = "API error";
    $("#health-pill").className = "pill pill-bad";
    console.error(err);
  }
}

/* ========================================================= trading mode ===
 *
 * The page cannot switch the bot itself; it writes a request to a file the bot
 * reads between trades, and the bot re-checks every live prerequisite before
 * acting on it. So there are three states to show, not two: what is running,
 * what has been asked for, and whether anything is listening. A dashboard that
 * showed only the request would report success against a stopped bot.
 */

async function loadTradingMode() {
  try {
    state.trading = await getJSON("/api/mode");
  } catch {
    state.trading = null;
  }
  renderTradingMode();
}

function renderTradingMode() {
  const box = $("#mode-control");
  const t = state.trading;
  if (!t || !t.control_enabled) {
    box.hidden = true;
    return;
  }
  box.hidden = false;

  const active = t.active || "paper";
  const label = $("#trading-state");
  const buttons = [...$("#trading-switch").children];

  // The buttons track what is *running*, so a pending request never looks
  // applied. The pending state is spelled out in words beside them instead.
  buttons.forEach((b) => {
    b.classList.toggle("active", !!t.active && b.dataset.trading === active);
    b.disabled = false;
  });

  label.className = "mode-state";
  if (!t.bot_seen) {
    label.textContent = "no bot running";
    label.classList.add("stale");
    buttons.forEach((b) => (b.disabled = true));
  } else if (t.stale) {
    label.textContent = "bot not reporting";
    label.classList.add("stale");
  } else if (t.pending) {
    label.textContent = `→ ${t.requested} pending`;
    label.classList.add("pending");
  } else {
    label.textContent = active === "live" ? "real money" : "simulated";
  }

  // The whole chrome turns red when the bot is live or about to be, whichever
  // is the more alarming of the two.
  document.body.classList.toggle(
    "trading-live", active === "live" || t.requested === "live");
}

function showModeError(payload) {
  const box = $("#mode-error");
  if (!payload) { box.hidden = true; box.innerHTML = ""; return; }
  const detail = payload.detail || payload.error || "The switch was refused.";
  const hint = payload.hint ? `<span class="hint">${escapeHTML(payload.hint)}</span>` : "";
  box.innerHTML =
    `<b>Cannot switch to live trading</b>${escapeHTML(detail)}${hint}`;
  box.hidden = false;
}

async function requestTradingMode(mode, confirm) {
  showModeError(null);
  const body = confirm ? { mode, confirm } : { mode };
  let res;
  try {
    res = await fetch("/api/mode", {
      method: "POST",
      cache: "no-store",
      // Not decoration: a cross-origin page cannot set this header without a
      // preflight the server never answers, which is what stops another tab
      // from flipping this bot to live.
      headers: { "Content-Type": "application/json", "X-KX15-Control": "1" },
      body: JSON.stringify(body),
    });
  } catch (err) {
    showModeError({ detail: `Could not reach the dashboard API: ${err}` });
    return;
  }
  const payload = await res.json().catch(() => ({}));
  if (!res.ok) { showModeError(payload); return; }

  state.trading = payload;
  renderTradingMode();
  const box = $("#mode-error");
  box.className = "issues";
  box.innerHTML =
    `<b>Switching to ${escapeHTML(payload.requested)}</b>` +
    `Applies ${escapeHTML(payload.applies_when || "shortly")}.`;
  box.hidden = false;
  setTimeout(() => { box.hidden = true; box.className = "issues issues-error"; }, 8000);
}

function wireTradingMode() {
  const dialog = $("#live-dialog");
  const field = $("#live-confirm");
  const go = $("#live-go");

  $("#trading-switch").addEventListener("click", (e) => {
    const btn = e.target.closest("button");
    if (!btn || btn.disabled) return;
    const wanted = btn.dataset.trading;
    if (wanted === "live") {
      const t = state.trading || {};
      field.value = "";
      go.disabled = true;
      $("#live-dialog-detail").textContent = t.reason
        ? `Bot reports: ${t.reason}`
        : `Config: ${t.env?.path ?? "unknown"}`;
      dialog.showModal();
      field.focus();
    } else {
      requestTradingMode("paper");
    }
  });

  field.addEventListener("input", () => {
    go.disabled = field.value.trim().toUpperCase() !== "LIVE";
  });

  $("#live-form").addEventListener("submit", (e) => {
    // `method="dialog"` closes the dialog for us; returnValue carries which
    // button did it.
    if (e.submitter && e.submitter.value === "go") {
      requestTradingMode("live", "LIVE");
    }
  });
}

// Populated from /api/config, which derives it from config.SERIES_BY_ASSET.
// The literal is a fallback for a page loaded while the server is unreachable,
// not a second source of truth - it is deliberately the assets that have
// existed longest rather than a copy anyone would think to update.
function initAssets(assets) {
  const list = Array.isArray(assets) && assets.length
    ? assets
    : ["BTC", "ETH", "SOL", "HYPE", "DOGE"];
  $("#chart-asset").innerHTML = list.map((a) => `<option value="${a}">${a}</option>`).join("");
  if (!list.includes(state.asset)) state.asset = list[0];
  $("#chart-asset").value = state.asset;
}

function wire() {
  wireTradingMode();
  $("#refresh").addEventListener("click", () => { refresh(); loadChart(); });
  $("#trade-filter").addEventListener("input", renderTrades);

  $("#mode-switch").addEventListener("click", async (e) => {
    const btn = e.target.closest("button"); if (!btn) return;
    applyMode(btn.dataset.mode);
    state.equity = (await getJSON(`/api/equity?mode=${state.mode}`)).points;
    renderTiles(); renderEquity();
  });

  $("#breakdown-switch").addEventListener("click", (e) => {
    const btn = e.target.closest("button"); if (!btn) return;
    state.dimension = btn.dataset.dim;
    [...$("#breakdown-switch").children].forEach((b) => b.classList.toggle("active", b === btn));
    renderBreakdown();
  });

  $("#chart-asset").addEventListener("change", (e) => { state.asset = e.target.value; loadChart(); });
  $("#chart-hours").addEventListener("change", (e) => { state.hours = +e.target.value; loadChart(); });

  const canvas = $("#chart");
  canvas.addEventListener("mousemove", onChartMove);
  canvas.addEventListener("mouseleave", () => ($("#tooltip").hidden = true));

  $("#autorefresh").addEventListener("change", (e) => setAuto(e.target.checked));

  let resizeTimer;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      renderChart(); renderEquity(); renderCalibration();
    }, 150);
  });
}

function setAuto(on) {
  clearInterval(state.timer);
  // 15s: fast enough to watch a market resolve, slow enough that the Kraken
  // fetch behind /api/chart is not hammered.
  if (on) state.timer = setInterval(refresh, 15000);
}

function applyMode(mode) {
  state.mode = mode === "live" ? "live" : "paper";
  [...$("#mode-switch").children].forEach((b) =>
    b.classList.toggle("active", b.dataset.mode === state.mode));
  // Make a live view visually unmistakable — this book is real money.
  document.body.classList.toggle("live-mode", state.mode === "live");
}

(async function main() {
  initAssets(null); wire(); setAuto(true);   // fallback list; /api/config refines it below
  try {
    const config = await getJSON("/api/config");
    applyMode(config.default_mode);
    initAssets(config.assets);
  } catch {
    applyMode("paper");   // config unreachable: stay on the safe book
  }
  await refresh();
  await loadChart();
})();
