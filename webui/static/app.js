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
    if (f.hint) el.placeholder = f.hint;   // 空にしたときだけ見える書式の例（I-237）
    el.addEventListener("input", onEdit);
  }
  el.dataset.key = f.key;
  label.dataset.key = f.key;
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
  scopeForm();
}

// 複数経路と中継では端点と空中線高を行（点）が持つ＝左の列の同じ欄は効かないので隠す
// （欄が全部隠れた枠も隠す）。条件探索は左の列の値が基準＝隠さない
const ROW_OWNED = { batch: ["start", "end", "h_tx", "h_rx"], multihop: ["start", "end", "h_tx", "h_rx"] };

function scopeForm() {
  const owned = ROW_OWNED[document.body.dataset.ws] || [];
  document.querySelectorAll("#form .group").forEach((box) => {
    const fields = box.querySelectorAll("label[data-key]");
    fields.forEach((l) => { l.hidden = owned.includes(l.dataset.key); });
    box.hidden = fields.length > 0 && [...fields].every((l) => l.hidden);
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
  headline = h;
  showSheetResults(h);
  showProfile(h);
  const band = document.getElementById("band");
  const word = document.getElementById("band-word");
  const detail = document.getElementById("band-detail");
  const range = document.getElementById("band-range");
  const flipped = document.getElementById("band-flipped");
  band.dataset.kind = h.kind;
  band.dataset.word = h.kind === "sheet" ? h.overall || "" : h.word || "";
  range.textContent = "";
  flipped.textContent = "";
  if (h.kind === "sheet") {
    // 作業面＝全件そろえば全体の語と語ごとの件数（0 件も言う）・途中は進み具合
    word.textContent = h.overall || fmt(labels.sheet_progress || "", { done: h.done, total: h.total });
    detail.textContent = h.counts
      ? Object.keys(h.counts).map((k) => `${k} ${h.counts[k]}`).join(" · ")
      : "";
  } else if (h.kind === "verdict") {
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
  current = inputs;
  document.querySelectorAll("[data-key]").forEach((el) => {
    const key = el.dataset.key;
    if (document.activeElement !== el && !(key in pending)) el.value = inputs[key] || "";
  });
  if (dragging || workspace !== "single") return;   // ドラッグ中のピンを古い値へ戻さない
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
  sheetLayer = L.layerGroup().addTo(map);
  // 作業面を変えると下の段の高さが変わる＝地図と断面図の両方を見張る
  const resized = new ResizeObserver(() => { map.invalidateSize(); drawProfile(); });
  resized.observe(document.getElementById("map"));
  resized.observe(document.getElementById("profile"));
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

// ホバーの位置を測る線の両端＝1 回線はピン・作業面は選んだ件の両端（無ければ null）
function hoverEnds() {
  if (workspace === "single") {
    return pins.start ? [pins.start.getLatLng(), pins.end.getLatLng()] : null;
  }
  const it = selected === null ? null : sheetItems[selected];
  return it && it.tx && it.rx ? [L.latLng(it.tx), L.latLng(it.rx)] : null;
}

function onPathHover(e) {
  const ends = hoverEnds();
  if (!ends) return;
  const a = map.latLngToLayerPoint(ends[0]);
  const b = map.latLngToLayerPoint(ends[1]);
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
  if (workspace === "single") {
    if (h.kind === "verdict" && h.profile) profile = h.profile;
    box.dataset.stale = h.kind === "verdict" ? "" : "1";   // 古い断面は残して薄くする（内訳と同じ）
  } else {
    // 作業面＝選んだ行の断面（`/api/profile` で版つきで取る）。版が進めば薄くする
    box.dataset.stale = h.kind === "sheet" && profileFor === `${h.version}:${selected}` ? "" : "1";
    fetchRowProfile(h);
  }
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
    const pick = workspace !== "single" && selected === null;
    g.fillText((pick ? labels.pick_row : labels.profile_later) || "", w / 2, h / 2);
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
  const ends = hoverEnds();
  if (cursor) {
    if (frac === null || !ends) {
      cursor.setOpacity(0);
    } else {
      const [a, b] = ends;
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

// ---- 作業面（ステージ10c）＝切り替え・面の表・地図の全経路・行の断面図 ------------
let spec = null;                           // サーバーの `sheet_spec()`
let workspace = "single";
let current = {};                          // 入力の集合（基準の行と区間の薄字）
let sheets = null;                         // 面の入力＝画面で直した写し（送る元）
const dirty = new Set();                   // 送りかけの面
let sheetTimer = null;
let inflight = 0;
let wsPending = 0;
let headline = { kind: "computing" };
let sheetItems = [];                       // 直近の作業面の結果（表と地図）
let sheetItemsWs = null;
let selected = null;                       // 選んだ行（件の番号）
let profileFor = null;                     // 手元の断面の "版:件"
let sheetLayer = null;
let fittedSig = "";

const dB = (v) => Number(v).toFixed(dbDigits);

function el(tag, props, children) {
  const e = document.createElement(tag);
  Object.assign(e, props || {});
  (children || []).forEach((c) => e.append(c));
  return e;
}

function button(text, onClick, disabled) {
  const b = el("button", { type: "button", textContent: text || "", disabled: !!disabled });
  b.addEventListener("click", onClick);
  return b;
}

// 表の欄＝打ったら面の入力を丸ごと送る（300ms 待つ）
function cellInput(obj, key, cls, placeholder) {
  const i = el("input", { value: obj[key] || "", className: cls || "", spellcheck: false, autocomplete: "off" });
  if (placeholder) i.placeholder = placeholder;
  i.addEventListener("input", () => { obj[key] = i.value; markDirty(DEBOUNCE_MS); });
  return i;
}

function cellSelect(obj, key, options, blank) {
  const s = el("select");
  if (blank) s.append(el("option", { value: "", textContent: "" }));
  options.forEach(([v, t]) => s.append(el("option", { value: v, textContent: t })));
  s.value = obj[key] || "";
  s.addEventListener("change", () => { obj[key] = s.value; markDirty(0); });
  return s;
}

function td(child, cls) {
  const c = el("td", { className: cls || "" });
  if (child !== undefined) c.append(child);
  return c;
}

function gridTable(heads) {
  const table = el("table", { className: "grid" });
  table.append(el("thead", {}, [el("tr", {}, heads.map((h) => el("th", { textContent: h || "" })))]));
  const body = el("tbody");
  table.append(body);
  return [table, body];
}

// 結果の 3 列（判定・マージン・受信レベル）は名前の列のすぐ右＝入力の列が多くても横に送らずに見える
const resultHeads = () => [labels.col_verdict, labels.margin, labels.p_rx];
const withResults = (heads) => heads.slice(0, 1).concat(resultHeads(), heads.slice(1));

// 結果の 3 列を持つ行（先頭の欄＝名前の後ろに差す）＝件の番号で結果を差す
function itemRow(item, cells) {
  const results = ["word result", "num result", "num result"].map((c) => td(undefined, c));
  const tr = el("tr", {}, cells.slice(0, 1).concat(results, cells.slice(1)));
  tr.dataset.item = String(item);
  tr.addEventListener("click", () => selectRow(item));
  return tr;
}

function markDirty(delay) {
  dirty.add(workspace);
  if (sheetTimer) clearTimeout(sheetTimer);
  sheetTimer = setTimeout(flushSheet, delay);
}

async function flushSheet() {
  sheetTimer = null;
  const body = {};
  dirty.forEach((name) => { body[name] = sheets[name]; });
  dirty.clear();
  inflight++;
  try {
    await postInputs({ sheets: body });
  } finally {
    inflight--;
  }
}

function postInputs(body) {
  return fetch("/api/inputs", { method: "POST", headers: HEAD, body: JSON.stringify(body) });
}

function sheetBusy() {
  const a = document.activeElement;
  const typing = a && document.getElementById("sheet").contains(a) && /^(INPUT|SELECT)$/.test(a.tagName);
  return sheetTimer !== null || inflight > 0 || typing;
}

// ポーリングの面の入力＝表の中に焦点が無く送りかけも無いときだけ描き直す
function receiveSheets(s) {
  if (!s || (sheets && sheetBusy())) return;
  if (sheets && JSON.stringify(s) === JSON.stringify(sheets)) return;
  sheets = s;
  renderSheet();
}

function buildTabs() {
  const nav = document.getElementById("tabs");
  nav.replaceChildren();
  spec.workspaces.forEach((w) => {
    const b = button(w.label, () => {
      if (w.key === workspace) return;
      setWorkspace(w.key);
      wsPending++;                         // 送り終えるまでポーリングの古い面で戻さない
      postInputs({ workspace: w.key }).finally(() => { wsPending--; });
    });
    b.setAttribute("role", "tab");
    b.dataset.ws = w.key;
    nav.append(b);
  });
}

function setWorkspace(key) {
  const changed = key !== workspace;
  workspace = key;
  document.body.dataset.ws = key;
  scopeForm();
  document.querySelectorAll("#tabs button").forEach((b) => {
    b.setAttribute("aria-selected", String(b.dataset.ws === key));
  });
  if (!changed) return;
  selected = null;
  profile = null;
  profileFor = null;
  hoverFrac = null;
  if (key !== sheetItemsWs) sheetItems = [];
  document.getElementById("sheet").hidden = key === "single";
  document.getElementById("sheet-note").textContent = "";
  [path, pins.start, pins.end].forEach((layer) => {
    if (!layer || !map) return;
    if (key === "single") layer.addTo(map); else map.removeLayer(layer);
  });
  if (key === "single") showInputs(current);
  renderSheet();
  drawSheetLines();
  drawProfile();
}

function renderSheet() {
  const tools = document.getElementById("sheet-tools");
  const body = document.getElementById("sheet-body");
  tools.replaceChildren();
  body.replaceChildren();
  if (workspace === "single" || !spec || !sheets) return;
  if (workspace === "batch") renderBatch(tools, body);
  else if (workspace === "scenario") renderScenario(tools, body);
  else renderMultihop(tools, body);
  showSheetResults(headline);
}

// 複数経路＝行の追加・削除（選んだ行・無ければ末尾）・CSV の読み込み
function renderBatch(tools, body) {
  const rows = sheets.batch;
  tools.append(
    button(labels.add_row, () => {
      const used = rows.map((r) => parseInt(r.id, 10)).filter(Number.isFinite);
      rows.push({ id: String(used.length ? Math.max(...used) + 1 : rows.length + 1) });
      renderSheet();
      markDirty(0);
    }),
    button(labels.del_row, () => {
      rows.splice(selected !== null && selected < rows.length ? selected : rows.length - 1, 1);
      selected = null;
      renderSheet();
      markDirty(0);
    }, !rows.length),
    el("span", { className: "sep" }),
    button(labels.import_csv, () => document.getElementById("csv-file").click()),
  );
  const cols = spec.batch;
  const [table, tbody] = gridTable(withResults(cols.map((c) => c.label)));
  rows.forEach((r, i) => {
    tbody.append(itemRow(i, cols.map((c) => {
      const cls = c.key === "start" || c.key === "end" ? "coord" : c.key === "note" || c.key === "id" ? "text" : "num";
      return td(cellInput(r, c.key, cls));
    })));
  });
  body.append(table);
}

async function importCsv(file) {
  const note = document.getElementById("sheet-note");
  note.className = "";
  note.textContent = "";
  const r = await fetch("/api/batch_csv", { method: "POST", headers: HEAD, body: JSON.stringify({ text: await file.text() }) });
  if (!r.ok) {
    const e = await r.json().catch(() => ({}));
    note.textContent = e.message || e.error || String(r.status);   // 見出しの誤りは表の上に出す
  }
}

document.getElementById("csv-file").addEventListener("change", (ev) => {
  const f = ev.target.files[0];
  ev.target.value = "";
  if (f) importCsv(f);
});

// 条件探索＝比較（行＝条件・先頭の基準は読むだけ）と掃引（軸・開始・終了・点数）
function renderScenario(tools, body) {
  const sc = sheets.scenario;
  const mode = (m, text) => {
    const b = button(text, () => { sc.mode = m; renderSheet(); markDirty(0); });
    b.setAttribute("aria-pressed", String(sc.mode === m));
    return b;
  };
  tools.append(mode("compare", labels.tab_compare), mode("sweep", labels.tab_sweep), el("span", { className: "sep" }));
  if (sc.mode === "compare") {
    tools.append(
      button(labels.add_cond, () => { sc.compare.push({}); renderSheet(); markDirty(0); },
        sc.compare.length >= spec.limits.compare),
      button(labels.del_cond, () => {
        const at = selected !== null && selected >= 1 && selected <= sc.compare.length ? selected - 1 : sc.compare.length - 1;
        sc.compare.splice(at, 1);
        selected = null;
        renderSheet();
        markDirty(0);
      }, !sc.compare.length),
    );
    const cols = spec.compare;
    const [table, tbody] = gridTable(withResults([""].concat(cols.map((c) => c.label))));
    const choice = (c, v) => ((c.options || []).find(([k]) => k === v) || [v, v])[1];
    const base = itemRow(0, [td(labels.base, "name")].concat(cols.map((c) => td(choice(c, current[c.input] || ""), "num"))));
    base.className = "base";
    tbody.append(base);
    sc.compare.forEach((cond, i) => {
      tbody.append(itemRow(i + 1, [td(fmt(labels.cond_n || "", { n: i + 1 }), "name")].concat(cols.map((c) => td(
        c.options ? cellSelect(cond, c.key, c.options, true) : cellInput(cond, c.key, "num", current[c.input] || ""),
      )))));
    });
    body.append(table);
  } else {
    const sw = sc.sweep;
    tools.append(
      el("label", {}, [labels.axis || "", cellSelect(sw, "axis", spec.sweep_axes, false)]),
      el("label", {}, [labels.from || "", cellInput(sw, "from", "num")]),
      el("label", {}, [labels.to || "", cellInput(sw, "to", "num")]),
      el("label", {}, [labels.points || "", cellInput(sw, "points", "num")]),
    );
    tools.querySelectorAll("input").forEach((i) => { i.style.width = "64px"; });
    const axis = (spec.sweep_axes.find(([k]) => k === sw.axis) || ["", ""])[1];
    const [table, tbody] = gridTable(withResults([axis]));
    tbody.id = "sweep-rows";
    body.append(table);
  }
}

// 掃引の行は結果の件から作る（入力は軸・範囲・点数だけ）
function fillSweepRows() {
  const tbody = document.getElementById("sweep-rows");
  if (!tbody || tbody.children.length === sheetItems.length) return;
  tbody.replaceChildren(...sheetItems.map((it, i) => itemRow(i, [td(it.label, "num")])));
}

// 中継＝経路 ID・備考・点の表（名前・座標・高さ）と区間の表（空欄は共通設定）
function renderMultihop(tools, body) {
  const mh = sheets.multihop;
  const pts = mh.waypoints;
  const syncHops = () => {
    while (mh.hop_rf.length < Math.max(0, pts.length - 1)) mh.hop_rf.push({ freq: "", gain_tx: "", gain_rx: "" });
    mh.hop_rf.length = Math.max(0, pts.length - 1);
  };
  let focusPoint = null;
  tools.append(
    el("label", {}, [labels.route_id || "", cellInput(mh, "path_id", "text")]),
    el("label", {}, [labels.note || "", cellInput(mh, "note", "text")]),
    el("span", { className: "sep" }),
    button(labels.add_point, () => { pts.push({ name: "", coord: "", h: "" }); syncHops(); renderSheet(); markDirty(0); },
      pts.length >= spec.limits.waypoints),
    button(labels.del_point, () => {
      const at = focusPoint !== null ? focusPoint : pts.length - 1;
      pts.splice(at, 1);
      mh.hop_rf.splice(Math.max(0, Math.min(at - 1, mh.hop_rf.length - 1)), 1);
      syncHops();
      selected = null;
      renderSheet();
      markDirty(0);
    }, !pts.length),
  );
  const [ptable, pbody] = gridTable(spec.waypoints.map((c) => c.label));
  pts.forEach((p, i) => {
    const tr = el("tr", {}, spec.waypoints.map((c) => td(cellInput(p, c.key, c.key === "coord" ? "coord" : c.key === "h" ? "num" : "text"))));
    tr.addEventListener("focusin", () => { focusPoint = i; });
    pbody.append(tr);
  });
  const name = (i) => (pts[i] && pts[i].name.trim()) || `P${i + 1}`;
  const [htable, hbody] = gridTable(withResults([labels.section].concat(spec.hops.map((c) => c.label))));
  mh.hop_rf.forEach((hop, i) => {
    hbody.append(itemRow(i, [td(`${name(i)} → ${name(i + 1)}`, "name")].concat(
      spec.hops.map((c) => td(cellInput(hop, c.key, "num", current[c.key] || ""))))));
  });
  body.append(el("h3", { textContent: labels.waypoints || "" }), ptable,
    el("h3", { textContent: labels.hops || "" }), el("div", { id: "hint", className: "hint", textContent: labels.hint_inherit || "" }), htable);
}

// 見出しの結果を表の結果の列へ（件の番号で差す）。今の面の今の版でなければ薄くする
function showSheetResults(h) {
  if (workspace === "single") return;
  const fresh = h.kind === "sheet" && h.workspace === workspace;
  if (fresh) {
    sheetItems = h.items || [];
    sheetItemsWs = workspace;
  }
  document.getElementById("sheet").dataset.stale = fresh ? "" : "1";
  fillSweepRows();
  document.querySelectorAll("#sheet-body tr[data-item]").forEach((tr) => {
    const i = Number(tr.dataset.item);
    const it = sheetItems[i];
    const [w, m, p] = tr.querySelectorAll("td.result");
    w.textContent = it ? it.word : "";
    w.dataset.word = it ? it.word : "";
    m.textContent = it && "margin" in it ? dB(it.margin) : "";
    p.textContent = it && "p_rx" in it ? dB(it.p_rx) : "";
    tr.title = (it && it.message) || "";
    tr.setAttribute("aria-selected", String(i === selected));
  });
  drawSheetLines(fresh && h.done === h.total);
}

function verdictColor(word) {
  const name = { OK: "--ok", NG: "--ng", REVIEW: "--review", ERROR: "--err" }[word] || "--wait";
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

// 地図＝各件の送信→受信を語の色で・選んだ件を太く。全件そろって座標の組が変われば全体へ合わせる
function drawSheetLines(complete) {
  if (!sheetLayer) return;
  sheetLayer.clearLayers();
  if (workspace === "single") return;
  const stale = document.getElementById("sheet").dataset.stale === "1";
  const bounds = [];
  // 条件探索は全件が同じ経路＝1 本だけ（選んだ件の語・選んでいなければ全体の語）
  const one = workspace === "scenario";
  const whole = headline.kind === "sheet" && headline.overall ? headline.overall : "";
  sheetItems.forEach((it, i) => {
    if (!it.tx || !it.rx) return;
    if (one && (selected !== null ? i !== selected : i !== 0)) return;
    const word = one && selected === null ? whole : it.word;
    const line = L.polyline([it.tx, it.rx], {
      color: verdictColor(word), weight: i === selected || one ? 6 : 3, opacity: stale ? 0.45 : 0.9,
    }).addTo(sheetLayer);
    line.on("click", () => selectRow(i));
    if (i === selected) {
      line.on("mousemove", onPathHover);
      line.on("mouseout", () => setHover(null));
    }
    bounds.push(it.tx, it.rx);
  });
  const sig = JSON.stringify(bounds);
  if (complete && bounds.length && sig !== fittedSig) {
    fittedSig = sig;
    map.fitBounds(L.latLngBounds(bounds), { padding: [40, 40], maxZoom: 15 });
  }
}

function selectRow(i) {
  selected = i;
  profile = null;
  profileFor = null;
  hoverFrac = null;
  document.querySelectorAll("#sheet-body tr[data-item]").forEach((tr) => {
    tr.setAttribute("aria-selected", String(Number(tr.dataset.item) === i));
  });
  drawSheetLines();
  showProfile(headline);
}

// 選んだ行の断面図＝その件が今の版で済んでいれば 1 回だけ取る（版が変われば取り直す）
async function fetchRowProfile(h) {
  if (selected === null || h.kind !== "sheet" || h.workspace !== workspace) return;
  if (selected >= (h.items || []).length) return;        // まだその件が済んでいない
  const key = `${h.version}:${selected}`;
  if (profileFor === key) return;
  profileFor = key;
  const r = await fetch(`/api/profile?v=${h.version}&item=${selected}`, { headers: HEAD });
  if (profileFor !== key) return;                         // 待つ間に選び直した
  profile = r.ok ? (await r.json()).profile : null;
  document.getElementById("profile").dataset.stale = headline.version === h.version ? "" : "1";
  drawProfile();
}

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
        if (s.sheet_spec) {
          spec = s.sheet_spec;
          buildTabs();
        }
      }
      revision = s.revision;
      receiveSheets(s.sheets);
      if (!wsPending) setWorkspace(s.workspace || "single");
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
