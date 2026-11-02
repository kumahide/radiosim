// 画面の道具の試作（4.0 ステージ2）。A と B が同じこの画面を読む。
// 🔑 不変条件＝画面の判定はいま画面に出ている入力から計算したもの。そうでない間は「計算中」。
//    状態は入力の集合 inputs 1 つ。seq が 1 つでも進めば、古い応答は画面に出さない。
"use strict";

const query = new URLSearchParams(location.search);
const TOKEN = query.get("t");
const MEASURE = query.get("measure") === "1";

const DEFAULTS = {
  start: "34.5429, 132.4118", end: "34.5389, 132.4050",
  h_tx: "30.0", h_rx: "10.0", freq: "2400.0", p_tx: "20.0",
  gain_tx: "3.0", gain_rx: "3.0", sens: "-85.0", veg_h: "10.0", rain_rate: "0.0",
};
const inputs = { ...DEFAULTS };

let seq = 0;
let inflight = null;
let endpointsDirty = false;
let profile = null;
let firstVerdictAt = null;
const timing = {};

const $ = (id) => document.getElementById(id);
const fields = [...document.querySelectorAll("input[data-field]")];

function api(path, body, signal) {
  return fetch(path, {
    method: "POST", signal,
    headers: { "Content-Type": "application/json", "X-Token": TOKEN },
    body: JSON.stringify(body ?? {}),
  });
}

function showVerdict(state, word, detail) {
  $("verdict").dataset.state = state;
  $("verdict-word").textContent = word;
  $("verdict-detail").textContent = detail;
}

function setBreakdown(rows) {
  const dl = $("breakdown");
  dl.replaceChildren();
  for (const [k, v] of rows) {
    const dt = document.createElement("dt"); dt.textContent = k;
    const dd = document.createElement("dd"); dd.textContent = v;
    dl.append(dt, dd);
  }
}

// ---- 計算の要求 ----
function request(waitingFor) {
  seq += 1;
  if (inflight) inflight.abort();
  if (endpointsDirty) {
    profile = null;
    drawProfile();
    showVerdict("pending", "計算中", "端点を確定してください（Enter か、欄の外をクリック）");
    return;
  }
  showVerdict("pending", "計算中", waitingFor);
  const mine = seq;
  const ctl = new AbortController();
  inflight = ctl;
  api("/api/calc", { seq: mine, inputs }, ctl.signal)
    .then((r) => r.json())
    .then((res) => { if (res.seq === seq) apply(res); })
    .catch((e) => {
      if (e.name !== "AbortError" && mine === seq) {
        showVerdict("pending", "計算中", "計算の口に届きません: " + e.message);
      }
    });
}

function apply(res) {
  if (!res.ok) {
    profile = null;
    drawProfile();
    showVerdict("pending", res.kind === "input" ? "入力待ち" : "地形なし", res.message);
    setBreakdown([]);
    return;
  }
  const failed = res.fail_pct > 0 ? `・地形の取得失敗 ${res.fail_pct}%` : "";
  showVerdict(res.status === "OK" ? "ok" : "ng", res.status,
    `余裕 ${res.margin_db.toFixed(1)} dB・受信電力 ${res.p_rx_dbm.toFixed(1)} dBm・距離 ${res.distance_km.toFixed(2)} km${failed}`);
  setBreakdown([
    ["自由空間損失", `${res.fspl_db.toFixed(1)} dB`],
    ["回折損", `${res.diff_loss_db.toFixed(1)} dB`],
    ["植生減衰", `${res.veg_loss_db.toFixed(1)} dB`],
    ["総損失", `${res.total_loss_db.toFixed(1)} dB`],
    ["Fresnel 遮蔽", res.blocked_text],
    ["F1 侵入深さ", res.f1_depth_text],
    ["標本数", `${res.samples} 点`],
  ]);
  profile = res.profile;
  timing.last_terrain_ms = res.terrain_ms;
  timing.last_calc_ms = res.calc_ms;
  drawProfile();
  if (firstVerdictAt === null) {
    firstVerdictAt = Date.now();
    onFirstVerdict();
  }
}

// ---- 入力 ----
function parsePoint(text) {
  const p = text.split(",").map(Number);
  return p.length === 2 && p.every(Number.isFinite) ? p : null;
}

for (const el of fields) {
  const key = el.dataset.field;
  el.value = inputs[key];
  if ("endpoint" in el.dataset) {
    el.addEventListener("input", () => {
      el.dataset.dirty = "1";
      endpointsDirty = true;
      request("");
    });
    el.addEventListener("change", () => commitEndpoint(el));
    el.addEventListener("keydown", (e) => { if (e.key === "Enter") commitEndpoint(el); });
  } else {
    el.addEventListener("input", () => {
      inputs[key] = el.value;
      request("再計算しています");
    });
  }
}

function commitEndpoint(el) {
  if (!("dirty" in el.dataset)) return;
  delete el.dataset.dirty;
  inputs[el.dataset.field] = el.value;
  endpointsDirty = fields.some((f) => "dirty" in f.dataset);
  syncMap();
  request("地形を取得しています");
}

// ---- 地図 ----
const map = L.map("map", { zoomControl: true, attributionControl: true });
L.tileLayer("/tile/pale/{z}/{x}/{y}.png", {
  maxZoom: 18, attribution: "地理院タイル（淡色地図）",
}).addTo(map);
map.attributionControl.setPrefix(false);

const pin = (cls, text) => L.divIcon({
  className: "", iconSize: [22, 22], iconAnchor: [11, 11],
  html: `<div class="pin ${cls}">${text}</div>`,
});
const txMarker = L.marker(parsePoint(inputs.start), { draggable: true, icon: pin("tx", "T") }).addTo(map);
const rxMarker = L.marker(parsePoint(inputs.end), { draggable: true, icon: pin("rx", "R") }).addTo(map);
const pathLine = L.polyline([], { color: "#1f5fbf", weight: 3 }).addTo(map);
const cursor = L.marker([0, 0], {
  interactive: false, opacity: 0,
  icon: L.divIcon({ className: "", iconSize: [10, 10], iconAnchor: [5, 5], html: '<div class="cursor"></div>' }),
}).addTo(map);

function syncMap() {
  const a = parsePoint(inputs.start);
  const b = parsePoint(inputs.end);
  if (!a || !b) return;
  txMarker.setLatLng(a);
  rxMarker.setLatLng(b);
  pathLine.setLatLngs([a, b]);
}

function hookMarker(marker, key) {
  const el = fields.find((f) => f.dataset.field === key);
  const text = (ll) => `${ll.lat.toFixed(5)}, ${ll.lng.toFixed(5)}`;
  marker.on("drag", (e) => {
    el.value = text(e.latlng);
    el.dataset.dirty = "1";
    endpointsDirty = true;
    pathLine.setLatLngs([txMarker.getLatLng(), rxMarker.getLatLng()]);
    request("");
  });
  marker.on("dragend", () => {
    el.value = text(marker.getLatLng());
    commitEndpoint(el);
  });
}
hookMarker(txMarker, "start");
hookMarker(rxMarker, "end");
syncMap();
map.fitBounds(pathLine.getBounds(), { padding: [60, 60], maxZoom: 15 });

// ---- 断面図 ----（地図と連動＝ホバーの位置を両方に出す）
let hoverFrac = null;
const canvas = $("profile");

function drawProfile() {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  const g = canvas.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, h);
  g.font = '12px "Yu Gothic UI", "Segoe UI", sans-serif';
  if (!profile) {
    g.fillStyle = "#5d6673";
    g.fillText("断面図は計算のあとに出ます", 16, 24);
    return;
  }
  const { d_km: xs, ground, los, fresnel_lower: fr } = profile;
  const pad = { l: 52, r: 16, t: 14, b: 26 };
  const values = [...ground, ...los, ...fr].filter((v) => v !== null);
  let lo = Math.min(...values);
  let hi = Math.max(...values);
  const margin = Math.max((hi - lo) * 0.08, 2);
  lo -= margin; hi += margin;
  const xMax = xs[xs.length - 1];
  const px = (d) => pad.l + (d / xMax) * (w - pad.l - pad.r);
  const py = (v) => h - pad.b - ((v - lo) / (hi - lo)) * (h - pad.t - pad.b);

  g.strokeStyle = "#e3e6ea"; g.fillStyle = "#5d6673"; g.lineWidth = 1;
  const ticks = 5;
  for (let i = 0; i <= ticks; i++) {
    const v = lo + ((hi - lo) * i) / ticks;
    const y = py(v);
    g.beginPath(); g.moveTo(pad.l, y); g.lineTo(w - pad.r, y); g.stroke();
    g.fillText(`${v.toFixed(0)} m`, 6, y + 4);
  }
  for (let i = 0; i <= ticks; i++) {
    const d = (xMax * i) / ticks;
    g.fillText(`${d.toFixed(2)} km`, px(d) - 22, h - 8);
  }

  g.beginPath();
  g.moveTo(px(xs[0]), py(lo));
  xs.forEach((d, i) => { if (ground[i] !== null) g.lineTo(px(d), py(ground[i])); });
  g.lineTo(px(xMax), py(lo));
  g.closePath();
  g.fillStyle = "#c8bfa9"; g.fill();

  const line = (ys, color, dash) => {
    g.beginPath(); g.setLineDash(dash); g.strokeStyle = color; g.lineWidth = 1.5;
    xs.forEach((d, i) => (i ? g.lineTo(px(d), py(ys[i])) : g.moveTo(px(d), py(ys[i]))));
    g.stroke(); g.setLineDash([]);
  };
  line(los, "#1f5fbf", []);
  line(fr, "#1f5fbf", [5, 4]);

  if (hoverFrac !== null) {
    const d = hoverFrac * xMax;
    let i = 0;
    while (i < xs.length - 1 && xs[i] < d) i++;
    g.strokeStyle = "#111"; g.lineWidth = 1;
    g.beginPath(); g.moveTo(px(d), pad.t); g.lineTo(px(d), h - pad.b); g.stroke();
    g.fillStyle = "#111";
    const label = `${d.toFixed(3)} km・地表 ${ground[i]?.toFixed(1) ?? "?"} m`;
    g.fillText(label, Math.min(px(d) + 6, w - 170), pad.t + 12);
  }
}

function setHover(frac) {
  hoverFrac = frac;
  if (frac === null) {
    cursor.setOpacity(0);
  } else {
    const a = txMarker.getLatLng();
    const b = rxMarker.getLatLng();
    cursor.setLatLng([a.lat + (b.lat - a.lat) * frac, a.lng + (b.lng - a.lng) * frac]).setOpacity(1);
  }
  drawProfile();
}

canvas.addEventListener("mousemove", (e) => {
  if (!profile) return;
  const left = 52;
  const right = canvas.clientWidth - 16;
  setHover(Math.max(0, Math.min(1, (e.offsetX - left) / (right - left))));
});
canvas.addEventListener("mouseleave", () => setHover(null));
pathLine.on("mousemove", (e) => {
  const a = map.latLngToLayerPoint(txMarker.getLatLng());
  const b = map.latLngToLayerPoint(rxMarker.getLatLng());
  const p = e.layerPoint;
  const dx = b.x - a.x, dy = b.y - a.y;
  setHover(Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / (dx * dx + dy * dy))));
});
pathLine.on("mouseout", () => setHover(null));
new ResizeObserver(() => { map.invalidateSize(); drawProfile(); }).observe($("views"));

// ---- 測定 ----（会社 PC ではこの欄のスクショ 1 枚を読む）
function renderer() {
  const c = document.createElement("canvas");
  const gl = c.getContext("webgl2") || c.getContext("webgl");
  if (!gl) return "WebGL なし";
  const ext = gl.getExtension("WEBGL_debug_renderer_info");
  return ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : "WebGL（名前は不明）";
}

const paintDone = () => new Promise((ok) => requestAnimationFrame(() => setTimeout(ok, 0)));

function overflow() {
  const over = (el) => Math.max(0, el.scrollHeight - el.clientHeight);
  return { inputs: over($("inputs")), diag: over($("diag")), page: over(document.documentElement) };
}

async function panFps() {
  // 地図を 120 px 往復させ、描画の間隔を測る＝AVD の画面転送の手前の値。
  let frames = 0;
  let running = true;
  const t0 = performance.now();
  const tick = () => { frames += 1; if (running) requestAnimationFrame(tick); };
  requestAnimationFrame(tick);
  for (const dx of [120, -120, 120, -120]) {
    map.panBy([dx, 0], { duration: 0.4 });
    await new Promise((ok) => setTimeout(ok, 420));
  }
  running = false;
  return Math.round(frames / ((performance.now() - t0) / 1000));
}

async function inputLatency(count) {
  const el = fields.find((f) => f.dataset.field === "p_tx");
  const original = el.value;
  const samples = [];
  const applied = [];
  for (let i = 0; i < count; i++) {
    const before = seq + 1;
    const t0 = performance.now();
    el.value = String(10 + (i % 20));
    el.dispatchEvent(new Event("input"));
    while ($("verdict").dataset.state === "pending" || seq !== before) {
      await new Promise((ok) => setTimeout(ok, 1));
    }
    // 判定と断面図を書き終えた時点＝C（Tk の update が返る時点）と同じ量。
    applied.push(performance.now() - t0);
    await paintDone();
    samples.push(performance.now() - t0);
  }
  el.value = original;
  el.dispatchEvent(new Event("input"));
  samples.sort((a, b) => a - b);
  applied.sort((a, b) => a - b);
  return { p50: samples[Math.floor(count / 2)], p95: samples[Math.floor(count * 0.95)], max: samples[count - 1],
           apply_p50: applied[Math.floor(count / 2)] };
}

function nav() {
  const n = performance.getEntriesByType("navigation")[0];
  return n ? { page_ms: Math.round(n.domContentLoadedEventEnd) } : {};
}

async function onFirstVerdict() {
  await paintDone();
  const painted = Date.now();
  const env = await (await fetch("/api/env")).json();
  const metrics = {
    first_verdict_ms: Math.round(painted - PAGE_T.startEpoch),
    page_origin_ms: Math.round(PAGE_T.startEpoch),
    ...nav(),
    renderer: renderer(),
    gpu: "gpu" in navigator ? "WebGPU あり" : "WebGPU なし",
    user_agent: navigator.userAgent.match(/(Edg\/[\d.]+)|(Chrome\/[\d.]+)/)?.[0] ?? "",
    screen: `${screen.width}×${screen.height}`,
    avail: `${screen.availWidth}×${screen.availHeight}`,
    client: `${innerWidth}×${innerHeight}`,
    dpr: window.devicePixelRatio,
    overflow: overflow(),
  };
  showDiag(env, metrics);
  await api("/api/metrics", metrics);
  metrics.pan_fps = await panFps();
  metrics.input_ms = await inputLatency(30);
  await new Promise((ok) => setTimeout(ok, 500));
  const env2 = await (await fetch("/api/env")).json();
  metrics.tiles = env2.tiles;
  showDiag(env2, metrics);
  setInterval(async () => {
    showDiag(await (await fetch("/api/env")).json(), { ...metrics, client: `${innerWidth}×${innerHeight}`, overflow: overflow() });
  }, 2000);
  if (MEASURE) await api("/api/metrics", { ...metrics, done: true });
}

const PAGE_T = { startEpoch: performance.timeOrigin };

function showDiag(env, m) {
  const t = env.tiles;
  const bad = (cond, text) => ({ text, bad: cond });
  const over = m.overflow;
  const startEpoch = env.process_start_epoch_ms;
  const startup = startEpoch ? `${((PAGE_T.startEpoch + (m.first_verdict_ms ?? 0) - startEpoch) / 1000).toFixed(2)} 秒` : "—";
  const rows = [
    ["候補", `${env.candidate}・${env.launch} 回目の起動`],
    ["起動→判定", startup],
    ["画面→判定", m.first_verdict_ms ? `${(m.first_verdict_ms / 1000).toFixed(2)} 秒` : "—"],
    ["入力→判定 中央", m.input_ms ? `${m.input_ms.p50.toFixed(0)} ms` : "測定中"],
    ["入力→判定 95%", m.input_ms ? `${m.input_ms.p95.toFixed(0)} ms` : "測定中"],
    ["入力→書き終え 中央", m.input_ms ? `${m.input_ms.apply_p50.toFixed(0)} ms` : "測定中"],
    ["地図の移動", m.pan_fps ? `${m.pan_fps} fps（画面 ${env.refresh_hz} Hz）` : "測定中"],
    ["描画", bad(/SwiftShader|WebGL なし|Basic Render/.test(m.renderer), m.renderer)],
    ["WebView2", bad(!env.webview2, env.webview2 ?? "無し")],
    ["Edge", bad(!env.edge_found, m.user_agent || (env.edge_found ? "あり" : "無し"))],
    ["画面", m.screen],
    ["使える領域", `${m.avail}（OS ${env.work_area}）`],
    ["窓の内側", m.client],
    ["DPI 倍率", `${m.dpr}`],
    ["はみ出し", bad(over.page + over.inputs + over.diag > 0, `全体 ${over.page}・入力 ${over.inputs}・測定 ${over.diag} px`)],
    ["タイル", bad(t.failed > 0, `手元 ${t.cache}・取得 ${t.fetched}・失敗 ${t.failed}`)],
    ["タイルの失敗", t.last_error || "なし"],
    ["プロキシ", env.proxy],
    ["遠隔の接続", `${env.remote_session ? "はい" : "いいえ"}（${env.session_name || "—"}）`],
    ["Windows", env.windows],
    ["Python", `${env.python}${env.frozen ? "（固めた exe）" : ""}`],
  ];
  const dl = $("diag-list");
  dl.replaceChildren();
  for (const [k, v] of rows) {
    const dt = document.createElement("dt"); dt.textContent = k;
    const dd = document.createElement("dd");
    dd.textContent = typeof v === "object" ? v.text : v;
    if (typeof v === "object" && v.bad) dd.className = "bad";
    dl.append(dt, dd);
  }
}

$("proxy-apply").addEventListener("click", async () => {
  await api("/api/proxy", { url: $("proxy").value.trim() });
  location.reload();
});
window.addEventListener("pagehide", () => {
  navigator.sendBeacon?.(`/api/bye?t=${TOKEN}`);
});

request("地形を取得しています");
