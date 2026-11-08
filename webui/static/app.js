// 新しい画面（Link 1.0）。字と欄の並びはすべてサーバーの表から＝ここに日本語を書かない。
"use strict";

const TOKEN = location.hash.slice(1);
const HEAD = { "X-Token": TOKEN, "Content-Type": "application/json" };
const DEBOUNCE_MS = 300;

let labels = {};
let breakdownRows = [];
let dbDigits = 1;                          // dB の桁（サーバーの `units.DB_DECIMALS` で上書き）
let revision = 0;
let map = null;
let path = null;
const pending = {};
let timer = null;

function fmt(text, values) {
  return text.replace(/\{(\w+)\}/g, (m, k) => (k in values ? values[k] : m));
}

function applyLabels() {
  document.querySelectorAll("[data-label]").forEach((el) => {
    el.textContent = labels[el.dataset.label] || "";
  });
}

// ---- 入力の列（サーバーの `form()` を並べる） ------------------------------
function buildField(f) {
  const label = document.createElement("label");
  // 座標（文字で打つ欄）は長い＝語の下に全幅。数と選択式は語の右に置いて 1 行（990px に収める）
  label.className = f.mode === "text" ? "wide" : "row";
  const name = document.createElement("span");
  name.textContent = f.label;
  label.appendChild(name);
  let el;
  if (f.options) {
    el = document.createElement("select");
    f.options.forEach(([value, text]) => {
      const o = document.createElement("option");
      o.value = value;
      o.textContent = text;
      el.appendChild(o);
    });
    el.addEventListener("change", onEdit);
  } else {
    el = document.createElement("input");
    el.spellcheck = false;
    el.autocomplete = "off";
    el.inputMode = f.mode === "decimal" ? "decimal" : "text";
    el.addEventListener("input", onEdit);
  }
  el.dataset.key = f.key;
  if (f.tip) label.title = f.tip;
  label.appendChild(el);
  return label;
}

function buildForm(groups) {
  const root = document.getElementById("form");
  root.replaceChildren();
  groups.forEach((g) => {
    let box;
    if (g.advanced) {
      box = document.createElement("details");
      const s = document.createElement("summary");
      s.textContent = g.title;
      box.appendChild(s);
    } else {
      box = document.createElement("fieldset");
      const legend = document.createElement("legend");
      legend.textContent = g.title;
      box.appendChild(legend);
    }
    box.className = "group";
    g.fields.forEach((f) => box.appendChild(buildField(f)));
    root.appendChild(box);
  });
}

function buildBreakdown(rows) {
  breakdownRows = rows;
  const table = document.getElementById("breakdown");
  table.replaceChildren();
  rows.forEach((r) => {
    const tr = document.createElement("tr");
    tr.dataset.name = r.name;
    const th = document.createElement("th");
    th.textContent = r.label;
    const td = document.createElement("td");
    td.className = "num";
    const unit = document.createElement("td");
    unit.className = "unit";
    unit.textContent = r.unit;
    tr.append(th, td, unit);
    table.appendChild(tr);
  });
}

function showBreakdown(h) {
  const fresh = h.kind === "verdict";
  document.getElementById("budget").dataset.stale = fresh ? "" : "1";
  if (!fresh) return;                      // 古い値は残して薄くする（今の版の値ではない）
  breakdownRows.forEach((r) => {
    const td = document.querySelector(`#breakdown tr[data-name="${r.name}"] td.num`);
    if (td) td.textContent = r.name in h ? Number(h[r.name]).toFixed(r.digits) : "";
  });
}

// ---- 見出し ---------------------------------------------------------------
function showHeadline(h) {
  showProfile(h);
  const band = document.getElementById("band");
  const word = document.getElementById("band-word");
  const detail = document.getElementById("band-detail");
  const range = document.getElementById("band-range");
  const flipped = document.getElementById("band-flipped");
  band.dataset.kind = h.kind;
  band.dataset.word = h.word || "";
  range.textContent = "";
  flipped.textContent = "";
  if (h.kind === "verdict") {
    const dB = (v) => Number(v).toFixed(dbDigits);
    word.textContent = h.word;
    detail.textContent = `${labels.margin || ""} ${dB(h.margin)} dB · ${labels.p_rx || ""} ${dB(h.p_rx)} dBm`;
    if ("worst" in h && "best" in h) {
      range.textContent = fmt(labels.margin_range || "", { worst: dB(h.worst), best: dB(h.best) });
    }
    flipped.textContent = h.flipped || "";   // REVIEW を裏返す前提（字はサーバーが帳票と同じ形で作る）
  } else if (h.kind === "computing") {
    word.textContent = labels.computing || "";
    detail.textContent = h.total
      ? fmt(labels.fetching_pct || "", { pct: Math.floor((100 * h.done) / h.total) })
      : "";
  } else {
    word.textContent = labels[h.kind] || "";
    detail.textContent = h.message || "";
  }
  showBreakdown(h);
}

function parseLatLon(text) {
  const parts = String(text).split(",").map((s) => parseFloat(s));
  return parts.length === 2 && parts.every(Number.isFinite) ? parts : null;
}

function showInputs(inputs) {
  document.querySelectorAll("[data-key]").forEach((el) => {
    const key = el.dataset.key;
    if (document.activeElement !== el && !(key in pending)) el.value = inputs[key] || "";
  });
  if (dragging) return;                    // ドラッグ中のピンを古い値へ戻さない
  const a = parseLatLon(inputs.start);
  const b = parseLatLon(inputs.end);
  if (map && a && b) {
    const first = !path;
    if (first) {
      path = L.polyline([a, b], { color: "#1f5fbf", weight: 3 }).addTo(map);
      path.on("mousemove", onPathHover);
      path.on("mouseout", () => setHover(null));
      pins.start = makePin("start", "T", a);
      pins.end = makePin("end", "R", b);
    }
    path.setLatLngs([a, b]);
    pins.start.setLatLng(a);
    pins.end.setLatLng(b);
    if (first) map.fitBounds(path.getBounds(), { padding: [60, 60], maxZoom: 15 });
  }
}

// ---- 地図 -----------------------------------------------------------------
const pins = {};
let cursor = null;
let dragging = false;

function initMap(basemap) {
  map = L.map("map", { zoomControl: true });
  L.tileLayer(`/tile/${basemap}/{z}/{x}/{y}.png`, { maxZoom: 18 }).addTo(map);
  map.setView([35.0, 135.0], 5);
  cursor = L.marker([0, 0], {
    interactive: false, opacity: 0, keyboard: false,
    icon: L.divIcon({ className: "", iconSize: [10, 10], iconAnchor: [5, 5], html: '<div class="cursor"></div>' }),
  }).addTo(map);
  new ResizeObserver(() => { map.invalidateSize(); drawProfile(); }).observe(document.getElementById("view"));
}

const latLonText = (ll) => `${ll.lat.toFixed(5)}, ${ll.lng.toFixed(5)}`;

// 端点のピン＝ドラッグの間は欄と線だけ動かし、離したときに 1 回だけ送る（地形の取得は端点が決まったときだけ）
function makePin(key, text, at) {
  const marker = L.marker(at, {
    draggable: true,
    icon: L.divIcon({ className: "", iconSize: [22, 22], iconAnchor: [11, 11], html: `<div class="pin">${text}</div>` }),
  }).addTo(map);
  const field = () => document.querySelector(`[data-key="${key}"]`);
  marker.on("dragstart", () => { dragging = true; });
  marker.on("drag", (e) => {
    const el = field();
    if (el) el.value = latLonText(e.latlng);
    path.setLatLngs([pins.start.getLatLng(), pins.end.getLatLng()]);
  });
  marker.on("dragend", () => {
    dragging = false;
    const value = latLonText(marker.getLatLng());
    const el = field();
    if (el) el.value = value;
    pending[key] = value;
    if (timer) clearTimeout(timer);
    flush();
  });
  return marker;
}

function onPathHover(e) {
  const a = map.latLngToLayerPoint(pins.start.getLatLng());
  const b = map.latLngToLayerPoint(pins.end.getLatLng());
  const p = e.layerPoint;
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len2 = dx * dx + dy * dy;
  if (len2 > 0) setHover(Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / len2)));
}

// ---- 断面図（地図と連動＝ホバーの位置を両方に出す） -----------------------
let profile = null;
let hoverFrac = null;
const PAD = { l: 56, r: 16, t: 10, b: 34 };

function showProfile(h) {
  const box = document.getElementById("profile");
  if (h.kind === "verdict" && h.profile) profile = h.profile;
  box.dataset.stale = h.kind === "verdict" ? "" : "1";   // 古い断面は残して薄くする（内訳と同じ）
  drawProfile();
}

function cssColor(name) {
  return getComputedStyle(document.getElementById("profile")).getPropertyValue(name).trim();
}

// 目盛りの刻み＝1・2・5 × 10^n
function niceStep(span, count) {
  const raw = span / Math.max(1, count);
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const r = raw / mag;
  return (r <= 1 ? 1 : r <= 2 ? 2 : r <= 5 ? 5 : 10) * mag;
}

function profileExtent(p) {
  const ys = [];
  p.ground.forEach((v) => { if (v !== null) { ys.push(v); if (p.veg_h) ys.push(v + p.veg_h); } });
  p.los.forEach((v, i) => {
    if (v !== null && p.f1[i] !== null) ys.push(v + p.f1[i], v - p.f1[i]);
  });
  ys.push(p.tx[2], p.rx[2]);
  const finite = ys.filter(Number.isFinite);
  let lo = Math.min(...finite);
  let hi = Math.max(...finite);
  const margin = Math.max((hi - lo) * 0.06, 2);
  return [lo - margin, hi + margin];
}

// 地表の高さ（ホバーの字）＝折れ線を距離で補間
function groundAt(p, d) {
  const xs = p.ground_d;
  let i = 1;
  while (i < xs.length - 1 && xs[i] < d) i++;
  const x0 = xs[i - 1], x1 = xs[i], y0 = p.ground[i - 1], y1 = p.ground[i];
  if (y0 === null || y1 === null) return null;
  return x1 > x0 ? y0 + ((y1 - y0) * (d - x0)) / (x1 - x0) : y0;
}

function drawProfile() {
  const canvas = document.getElementById("profile-canvas");
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  if (!w || !h) return;
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  const g = canvas.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, h);
  const fg = getComputedStyle(document.body).color;
  const muted = cssColor("color");
  g.font = '12px "Segoe UI", "Yu Gothic UI", sans-serif';
  if (!profile) {
    g.fillStyle = muted;
    g.textAlign = "center";
    g.fillText(labels.profile_later || "", w / 2, h / 2);
    return;
  }
  const p = profile;
  const xMax = p.rx[0] || 1;
  const [lo, hi] = profileExtent(p);
  const px = (d) => PAD.l + (d / xMax) * (w - PAD.l - PAD.r);
  const py = (v) => h - PAD.b - ((v - lo) / (hi - lo)) * (h - PAD.t - PAD.b);

  // 目盛りと格子
  g.lineWidth = 1;
  g.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue("--line").trim();
  g.fillStyle = muted;
  const yStep = niceStep(hi - lo, 4);
  g.textAlign = "right";
  for (let v = Math.ceil(lo / yStep) * yStep; v <= hi; v += yStep) {
    const y = py(v);
    g.beginPath(); g.moveTo(PAD.l, y); g.lineTo(w - PAD.r, y); g.stroke();
    g.fillText(String(Math.round(v)), PAD.l - 6, y + 4);
  }
  const xStep = niceStep(xMax, 8);
  g.textAlign = "center";
  for (let d = 0; d <= xMax + 1e-9; d += xStep) {
    g.fillText(String(Math.round(d)), px(d), h - PAD.b + 14);
  }
  g.fillText(labels.dist_axis || "", PAD.l + (w - PAD.l - PAD.r) / 2, h - 4);
  g.save();
  g.translate(12, PAD.t + (h - PAD.t - PAD.b) / 2);
  g.rotate(-Math.PI / 2);
  g.fillText(labels.alt_axis || "", 0, 0);
  g.restore();

  // 地表と植生（帳票と同じ＝地表は下端から塗る・植生は地表の上に veg_h）
  const area = (top, bottom, color) => {
    g.beginPath();
    p.ground_d.forEach((d, i) => (i ? g.lineTo(px(d), py(top(i))) : g.moveTo(px(d), py(top(i)))));
    for (let i = p.ground_d.length - 1; i >= 0; i--) g.lineTo(px(p.ground_d[i]), py(bottom(i)));
    g.closePath();
    g.fillStyle = color;
    g.fill();
  };
  const gr = (i) => (p.ground[i] === null ? lo : p.ground[i]);
  area(gr, () => lo, cssColor("--pf-terrain"));
  if (p.veg_h) area((i) => gr(i) + p.veg_h, gr, cssColor("--pf-vegetation"));

  // 第 1 フレネルゾーン（標本の位置で ±F1）と見通し線
  g.beginPath();
  p.d.forEach((d, i) => (i ? g.lineTo(px(d), py(p.los[i] + p.f1[i])) : g.moveTo(px(d), py(p.los[i] + p.f1[i]))));
  for (let i = p.d.length - 1; i >= 0; i--) g.lineTo(px(p.d[i]), py(p.los[i] - p.f1[i]));
  g.closePath();
  g.fillStyle = cssColor("--pf-fresnel");
  g.fill();
  g.beginPath();
  g.setLineDash([6, 4]);
  g.strokeStyle = cssColor("--pf-los");
  g.lineWidth = 1.5;
  p.d.forEach((d, i) => (i ? g.lineTo(px(d), py(p.los[i])) : g.moveTo(px(d), py(p.los[i]))));
  g.stroke();
  g.setLineDash([]);

  // 空中線＝足元から絶対高まで
  g.strokeStyle = cssColor("--pf-antenna");
  g.lineWidth = 3;
  [p.tx, p.rx].forEach(([d, foot, top]) => {
    g.beginPath(); g.moveTo(px(d), py(foot)); g.lineTo(px(d), py(top)); g.stroke();
  });

  // ホバーの位置
  if (hoverFrac !== null) {
    const d = hoverFrac * xMax;
    const x = px(d);
    g.strokeStyle = fg;
    g.lineWidth = 1;
    g.beginPath(); g.moveTo(x, PAD.t); g.lineTo(x, h - PAD.b); g.stroke();
    const ground = groundAt(p, d);
    const text = `${Math.round(d)} m · ${labels.legend_terrain || ""} ${ground === null ? "-" : ground.toFixed(1)} m`;
    g.fillStyle = fg;
    const tw = g.measureText(text).width;
    g.textAlign = "left";
    g.fillText(text, Math.min(x + 6, w - PAD.r - tw), PAD.t + 12);
  }
}

function setHover(frac) {
  hoverFrac = frac;
  if (cursor) {
    if (frac === null || !pins.start) {
      cursor.setOpacity(0);
    } else {
      const a = pins.start.getLatLng();
      const b = pins.end.getLatLng();
      cursor.setLatLng([a.lat + (b.lat - a.lat) * frac, a.lng + (b.lng - a.lng) * frac]).setOpacity(1);
    }
  }
  drawProfile();
}

function hookProfile() {
  const canvas = document.getElementById("profile-canvas");
  canvas.addEventListener("mousemove", (e) => {
    if (!profile) return;
    const right = canvas.clientWidth - PAD.r;
    setHover(Math.max(0, Math.min(1, (e.offsetX - PAD.l) / (right - PAD.l))));
  });
  canvas.addEventListener("mouseleave", () => setHover(null));
}
hookProfile();

// ---- 窓の絵（I-238）＝`brand.svg` の `<g id="icon">` を単独の SVG にして favicon へ ----------
// 元は brand.svg の 1 つ（exe の icon.ico も同じ `icon` から焼く）＝ここで絵を書き写さない。
async function setFavicon() {
  try {
    const text = await (await fetch("brand.svg")).text();
    const doc = new DOMParser().parseFromString(text, "image/svg+xml");
    const icon = doc.getElementById("icon");
    const defs = doc.querySelector("defs");
    if (!icon || !defs) return;
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", icon.getAttribute("data-viewbox") || "0 0 256 256");
    svg.appendChild(defs.cloneNode(true));                 // `<use href="#mark">` の参照先
    const use = document.createElementNS(ns, "use");
    use.setAttribute("href", "#icon");
    svg.appendChild(use);
    const blob = new Blob([new XMLSerializer().serializeToString(svg)], { type: "image/svg+xml" });
    const link = document.createElement("link");
    link.rel = "icon";
    link.href = URL.createObjectURL(blob);
    document.head.appendChild(link);
  } catch (e) {
    // 絵が無くても画面は動く（窓の絵は既定のまま）
  }
}
setFavicon();

async function flush() {
  timer = null;
  const changes = Object.assign({}, pending);
  Object.keys(pending).forEach((k) => delete pending[k]);
  await fetch("/api/inputs", { method: "POST", headers: HEAD, body: JSON.stringify({ changes }) });
}

function onEdit(ev) {
  pending[ev.target.dataset.key] = ev.target.value;
  if (timer) clearTimeout(timer);
  // 選択式は打ち間違いの途中が無い＝待たずに送る
  timer = setTimeout(flush, ev.target.tagName === "SELECT" ? 0 : DEBOUNCE_MS);
}

async function poll() {
  for (;;) {
    try {
      const r = await fetch(`/api/state?after=${revision}`, { headers: HEAD });
      if (!r.ok) throw new Error(String(r.status));
      const s = await r.json();
      if (s.labels) {
        labels = s.labels;
        applyLabels();
        buildForm(s.form || []);
        buildBreakdown(s.breakdown || []);
        if (Number.isInteger(s.db_digits)) dbDigits = s.db_digits;
        if (!map) initMap(s.basemap || "pale");
      }
      revision = s.revision;
      showInputs(s.inputs);
      showHeadline(s.headline);
    } catch (e) {
      await new Promise((ok) => setTimeout(ok, 1000));
    }
  }
}

window.addEventListener("pagehide", () => {
  navigator.sendBeacon(`/api/bye?t=${encodeURIComponent(TOKEN)}`);
});
poll();
