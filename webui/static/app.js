// 新しい画面（Link 1.0）。字と欄の並びはすべてサーバーの表から＝ここに日本語を書かない。
"use strict";

const TOKEN = location.hash.slice(1);
const HEAD = { "X-Token": TOKEN, "Content-Type": "application/json" };
const DEBOUNCE_MS = 300;

let labels = {};
// 結果の表（リンクバジェットと距離の項目）＝表の id → サーバーの行の表
const readoutRows = { breakdown: [], distances: [] };
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

function buildReadout(id, rows) {
  readoutRows[id] = rows;
  const table = document.getElementById(id);
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

// 結果の表の値を `src`（見出しか作業面の 1 件）から埋める。無い名前は空
function fillReadout(id, src) {
  readoutRows[id].forEach((r) => {
    const td = document.querySelector(`#${id} tr[data-name="${r.name}"] td.num`);
    if (!td) return;
    if (!(r.name in src)) td.textContent = "";
    else td.textContent = r.text ? String(src[r.name]) : Number(src[r.name]).toFixed(r.digits);
  });
}

function showBreakdown(h) {
  if (workspace !== "single") { showRowDistances(); return; }
  const fresh = h.kind === "verdict";
  for (const id of ["budget", "distance"]) document.getElementById(id).dataset.stale = fresh ? "" : "1";
  if (!fresh) return;                      // 古い値は残して薄くする（今の版の値ではない）
  fillReadout("breakdown", h);
  fillReadout("distances", h);
}

// 作業面の「距離」の項目＝選んだ行の値（断面図と同じく選んだ行に連動・選ぶ前は空）
function showRowDistances() {
  if (workspace === "single") return;
  const fresh = !!headline && headline.kind === "sheet" && headline.workspace === workspace;
  document.getElementById("distance").dataset.stale = fresh ? "" : "1";
  const it = selected === null ? null : sheetItems[selected];
  fillReadout("distances", it || {});
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

// 座標の欄＝十進度でも度分秒でも読む（`core/coords.py` の `_parse_one` と同じ規則・I-257）。
// サーバーは計算の写しで同じ規則で十進度へ直す＝ここは線とピンを引くためだけの読み。
const DMS_RE = /^\s*(\d+(?:\.\d+)?)\s*[°\s]\s*(?:(\d+(?:\.\d+)?)\s*['′\s]\s*)?(?:(\d+(?:\.\d+)?)\s*["″\s]?\s*)?([NSEWnsew])?\s*$/;
const DD_RE = /^\s*[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?\s*$/;

function parseCoordPart(text) {
  if (DD_RE.test(text)) return parseFloat(text);
  const m = DMS_RE.exec(text);
  if (!m) return NaN;
  const value = parseFloat(m[1]) + parseFloat(m[2] || "0") / 60 + parseFloat(m[3] || "0") / 3600;
  return m[4] && "SWsw".includes(m[4]) ? -value : value;
}

function parseLatLon(text) {
  const parts = String(text).split(",").map(parseCoordPart);
  return parts.length === 2 && parts.every(Number.isFinite) ? parts : null;
}

// 度分秒＝秒は小数 1 桁・60.0 に丸まったら桁上げして秒は 0 に置く（`coords._format_dms_one`・B-034）
function dmsPart(value, positive, negative) {
  const hemi = value >= 0 ? positive : negative;
  const v = Math.abs(value);
  let deg = Math.trunc(v);
  const remMin = (v - deg) * 60;
  let minutes = Math.trunc(remMin);
  let seconds = (remMin - minutes) * 60;
  if (Math.round(seconds * 10) / 10 >= 60) {
    seconds = 0;
    minutes += 1;
  }
  if (minutes >= 60) {
    minutes -= 60;
    deg += 1;
  }
  const sec = seconds.toFixed(1).padStart(4, "0");
  return `${deg}°${String(minutes).padStart(2, "0")}'${sec}"${hemi}`;
}

// 欄へ書き戻す表記＝設定の「座標の表記」（`coordFormat`）に従う
let coordFormat = "dd";
function coordText(lat, lng) {
  return coordFormat === "dms"
    ? `${dmsPart(lat, "N", "S")}, ${dmsPart(lng, "E", "W")}`
    : `${lat.toFixed(5)}, ${lng.toFixed(5)}`;
}

// 左の列の入力欄（`input`／`select`）。⚠️ 包む `label` にも同じ `data-key` がある（`scopeForm` が使う）＝
// `[data-key=…]` だけで探すと先に `label` が当たり、見えない所へ書いてしまう
const FIELD = "#form :is(input, select)[data-key]";
const fieldOf = (key) => document.querySelector(`#form :is(input, select)[data-key="${key}"]`);

function showInputs(inputs) {
  current = inputs;
  document.querySelectorAll(FIELD).forEach((el) => {
    const key = el.dataset.key;
    if (document.activeElement !== el && !(key in pending)) el.value = inputs[key] || "";
  });
  if (!map || dragging || workspace !== "single") return;   // ドラッグ中のピンを古い値へ戻さない
  // ピンは読めた端点ごとに立てる（片方だけでも出す＝地図から置いた 1 点目が見える）。線は 2 点そろってから
  const at = { start: parseLatLon(inputs.start), end: parseLatLon(inputs.end) };
  [["start", "T"], ["end", "R"]].forEach(([key, text]) => {
    if (!at[key]) return;
    if (pins[key]) pins[key].setLatLng(at[key]);
    else pins[key] = makePin(key, text, at[key]);
  });
  if (!at.start || !at.end) return;
  const first = !path;
  if (first) {
    path = L.polyline([at.start, at.end], { color: "#1f5fbf", weight: 3 }).addTo(map);
    path.on("mousemove", onPathHover);
    path.on("mouseout", () => setHover(null));
  }
  path.setLatLngs([at.start, at.end]);
  // 寄せるのは初めて 2 点がそろったときだけ＝地図から置いたときは、見えているかぎり縮尺を動かさない
  if (first && shouldFit(path.getBounds())) map.fitBounds(path.getBounds(), { padding: [60, 60], maxZoom: 15 });
}

// ---- 地図 -----------------------------------------------------------------
const pins = {};
let cursor = null;
let dragging = false;

function escapeHtml(text) {
  const box = document.createElement("span");
  box.textContent = text;
  return box.innerHTML;
}

// 背景地図のタイル＝`{id, attribution, max_zoom}`（サーバーが渡す）。替えたら外して張り直す（I-257 の C）。
// 出典（B-133）＝地理院タイルの表示義務。字は帳票へ焼く地図と同じ 1 本（サーバが渡す）。
// 宣言した外部ソースの字は利用者のファイルの生の字＝HTML として読ませない。
// ソースの最大ズームより先はタイルを拡大して見せる（`maxNativeZoom`＝無いタイルを取りに行かない）。
let baseLayer = null;
function setBasemap(b) {
  if (!map || !b) return;
  if (baseLayer) map.removeLayer(baseLayer);
  baseLayer = L.tileLayer(`/tile/${b.id}/{z}/{x}/{y}.png`, {
    maxZoom: 18, maxNativeZoom: Math.min(b.max_zoom || 18, 18),
    attribution: escapeHtml(b.attribution || ""),
  }).addTo(map);
  baseLayer.bringToBack();
}

function initMap(basemap) {
  map = L.map("map", { zoomControl: true });
  // 前置き（Leaflet の名前と外のリンク）は出さない＝手元の道具から外へ誘わない。
  map.attributionControl.setPrefix(false);
  setBasemap(basemap);
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
  // 地図から端点を置く（1 回線）。小窓が開いていた左クリックは閉じるだけ（同じ所に開き直さない）
  map.on("preclick", () => { menuWasOpen = !!placeMenu && map.hasLayer(placeMenu); });
  map.on("click contextmenu", onMapClick);
}

// ---- 地図から地点を置く（4 つの面とも） ---------------------------------------
// 置き先は面ごと:
// - 1 回線・条件探索＝左の列の送信・受信座標（条件探索は左の列が基準）
// - 複数経路＝選んだ行の送信・受信座標（選んでいなければ、そこを TX にした行を足して選ぶ）
// - 中継経路＝座標が空の地点を上から埋め、空が無ければ末尾に地点を足す
// 左クリックは空の端点をその順に埋める。埋める所が無ければ（右クリックはいつも）置き先を選ぶメニューを出す。
// 書く字は選んだ表記。左の列は待たずに 1 回だけ送り、表は面の入力を丸ごと送る（打ったときと同じ道）。
let placeMenu = null;
let menuWasOpen = false;
let placedOnMap = false;            // 地図から置いた後は、全部が見えているかぎり寄せ直さない

function fieldReadable(key) {
  const field = fieldOf(key);
  return !!(field && parseLatLon(field.value));
}

// 地図から置いた後に寄せるか＝見えている範囲から出たときだけ寄せる
const shouldFit = (bounds) => !(placedOnMap && map.getBounds().contains(bounds));

function placeEnd(key, ll) {
  placedOnMap = true;
  const value = latLonText(ll);
  const field = fieldOf(key);
  if (field) field.value = value;
  pending[key] = value;
  showInputs(Object.assign({}, current, { [key]: value }));   // 返事を待たずにピンを立てる
  if (timer) clearTimeout(timer);
  flush();
  drawSheetLines();                                            // 条件探索の仮のピン
}

function openPlaceMenu(ll, items) {
  const box = el("div", { className: "place-menu" },
    items.map(([text, act]) => button(text, () => { map.closePopup(); act(); })));
  placeMenu = L.popup({ closeButton: false, className: "place-popup", offset: [0, -4] })
    .setLatLng(ll).setContent(box).openOn(map);
}

function placeOnForm(ll, left) {
  if (left && !fieldReadable("start")) return placeEnd("start", ll);
  if (left && !fieldReadable("end")) return placeEnd("end", ll);
  openPlaceMenu(ll, [[labels.place_tx, () => placeEnd("start", ll)], [labels.place_rx, () => placeEnd("end", ll)]]);
}

function nextRowId(rows) {
  const used = rows.map((r) => parseInt(r.id, 10)).filter(Number.isFinite);
  return String(used.length ? Math.max(...used) + 1 : rows.length + 1);
}

function placeOnRow(ll, left) {
  const rows = sheets.batch;
  const row = selected !== null && selected < rows.length ? rows[selected] : null;
  const setCell = (key) => {
    placedOnMap = true;
    row[key] = latLonText(ll);
    renderSheet();
    markDirty(0);
  };
  const newRow = () => {
    placedOnMap = true;
    // 高さは 1 回線の値を入れておく（空だとその行はすぐ ERROR＝地図に線が出ない）。ボタンで足す行は空のまま
    rows.push({ id: nextRowId(rows), start: latLonText(ll), h_tx: current.h_tx || "", h_rx: current.h_rx || "" });
    renderSheet();
    selectRow(rows.length - 1);
    markDirty(0);
  };
  if (!row) return newRow();
  if (left && !parseLatLon(row.start || "")) return setCell("start");
  if (left && !parseLatLon(row.end || "")) return setCell("end");
  openPlaceMenu(ll, [[labels.place_tx, () => setCell("start")], [labels.place_rx, () => setCell("end")],
    [labels.map_new_row, newRow]]);
}

function placeWaypoint(ll) {
  const mh = sheets.multihop;
  const pts = mh.waypoints;
  const note = document.getElementById("sheet-note");
  const empty = pts.find((p) => !parseLatLon(p.coord || ""));
  let point = empty;
  if (!point && pts.length >= spec.limits.waypoints) {
    note.textContent = fmt(labels.map_point_limit || "", { n: spec.limits.waypoints });
    return;
  }
  if (!point) {
    point = { name: "", coord: "", h: "" };
    pts.push(point);
    syncHops(mh);
  }
  point.coord = latLonText(ll);
  // 高さが空なら 1 回線の値を入れておく（先頭＝送信アンテナ高・ほか＝受信アンテナ高）。空のままだと計算できない
  if (!String(point.h || "").trim()) point.h = (pts.indexOf(point) === 0 ? current.h_tx : current.h_rx) || "";
  note.textContent = "";
  placedOnMap = true;
  renderSheet();
  markDirty(0);
}

function onMapClick(e) {
  const left = e.type === "click";
  if (left && menuWasOpen) return;
  const ll = e.latlng.wrap();
  if (workspace === "single" || workspace === "scenario") placeOnForm(ll, left);
  else if (!sheets || !spec) return;
  else if (workspace === "batch") placeOnRow(ll, left);
  else if (workspace === "multihop") placeWaypoint(ll);
}

const latLonText = (ll) => coordText(ll.lat, ll.lng);

// 端点のピン＝ドラッグの間は欄と線だけ動かし、離したときに 1 回だけ送る（地形の取得は端点が決まったときだけ）
function makePin(key, text, at) {
  const marker = L.marker(at, {
    draggable: true,
    icon: L.divIcon({ className: "", iconSize: [22, 22], iconAnchor: [11, 11], html: `<div class="pin">${text}</div>` }),
  }).addTo(map);
  const field = () => fieldOf(key);
  marker.on("dragstart", () => { dragging = true; });
  marker.on("drag", (e) => {
    const el = field();
    if (el) el.value = latLonText(e.latlng);
    if (path) path.setLatLngs([pins.start.getLatLng(), pins.end.getLatLng()]);   // 線は 2 点そろってから
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
    return pins.start && pins.end ? [pins.start.getLatLng(), pins.end.getLatLng()] : null;
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
    // 選んだ行が ERROR なら、断面の代わりにその 1 文（表では行の `title` にしか出ない）
    const it = workspace !== "single" && selected !== null ? sheetItems[selected] : null;
    const why = it && it.word === "ERROR" && it.message;
    g.fillText((why || (pick ? labels.pick_row : labels.profile_later)) || "", w / 2, h / 2);
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
  i.cellOf = [obj, key];                   // 地図のピンが動かしている間に、この欄を探して字を替える
  if (placeholder) i.placeholder = placeholder;
  i.addEventListener("input", () => { obj[key] = i.value; markDirty(DEBOUNCE_MS); });
  return i;
}

const cellInputOf = (obj, key) => [...document.querySelectorAll("#sheet-body input")]
  .find((i) => i.cellOf && i.cellOf[0] === obj && i.cellOf[1] === key);

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
  // ピンを動かしている間もポーリングの面で差し替えない（つかんでいる行・点を古い写しにしない）
  return sheetTimer !== null || inflight > 0 || typing || dragging;
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
  if (map) map.closePopup();                       // 地点を置くメニューの置き先は面ごとに違う
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
  if (key === "single") { if (headline) showBreakdown(headline); } else showRowDistances();
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
  tools.append(button(labels.save_csv, saveSheetCsv));   // 成果物の保存は道具の右端（10d）
  // KML は複数経路だけ（旧画面も `summary.kml` だけ＝中継・条件探索には無い）
  if (workspace === "batch") tools.append(button(labels.save_kml, saveSheetKml));
  showSheetResults(headline);
}

// 複数経路＝行の追加・削除（選んだ行・無ければ末尾）・CSV の読み込み
function renderBatch(tools, body) {
  const rows = sheets.batch;
  tools.append(
    button(labels.add_row, () => {
      rows.push({ id: nextRowId(rows) });
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

// 区間の表の行数を点の数 − 1 にそろえる（足りなければ空欄＝共通設定の行を足す）
function syncHops(mh) {
  const want = Math.max(0, mh.waypoints.length - 1);
  while (mh.hop_rf.length < want) mh.hop_rf.push({ freq: "", gain_tx: "", gain_rx: "" });
  mh.hop_rf.length = want;
}

// 中継＝経路 ID・備考・点の表（名前・座標・高さ）と区間の表（空欄は共通設定）
function renderMultihop(tools, body) {
  const mh = sheets.multihop;
  const pts = mh.waypoints;
  let focusPoint = null;
  tools.append(
    el("label", {}, [labels.route_id || "", cellInput(mh, "path_id", "text")]),
    el("label", {}, [labels.note || "", cellInput(mh, "note", "text")]),
    el("span", { className: "sep" }),
    button(labels.add_point, () => { pts.push({ name: "", coord: "", h: "" }); syncHops(mh); renderSheet(); markDirty(0); },
      pts.length >= spec.limits.waypoints),
    button(labels.del_point, () => {
      const at = focusPoint !== null ? focusPoint : pts.length - 1;
      pts.splice(at, 1);
      mh.hop_rf.splice(Math.max(0, Math.min(at - 1, mh.hop_rf.length - 1)), 1);
      syncHops(mh);
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

// 面の入力から立てるピン＝計算の結果を待たずに置いた所が見え、ドラッグで動かせる
// 複数経路＝選んだ行の T・R／中継＝各地点の番号／条件探索＝左の列の T・R
// 各ピンは [位置, 字, 書き手]。書き手 `write(LatLng, 離したか)` は動かしている間は欄だけを替え、離したら送る
function draftPins() {
  const out = [];
  const add = (text, label, write) => {
    const at = parseLatLon(text || "");
    if (at) out.push([at, label, write]);
  };
  // 表の行・点＝欄の字を替え、離したら面の入力を丸ごと送る（打ったときと同じ道）
  const cell = (obj, key) => (ll, done) => {
    obj[key] = latLonText(ll);
    const input = cellInputOf(obj, key);
    if (input) input.value = obj[key];
    if (!done) return;
    placedOnMap = true;
    renderSheet();
    markDirty(0);
  };
  // 左の列＝欄の字を替え、離したら 1 回だけ送る（1 回線のピンと同じ）
  const field = (key) => (ll, done) => {
    if (done) return placeEnd(key, ll);
    const input = fieldOf(key);
    if (input) input.value = latLonText(ll);
  };
  if (workspace === "batch" && sheets && selected !== null && sheets.batch[selected]) {
    const row = sheets.batch[selected];
    add(row.start, "T", cell(row, "start"));
    add(row.end, "R", cell(row, "end"));
  } else if (workspace === "multihop" && sheets) {
    sheets.multihop.waypoints.forEach((p, i) => add(p.coord, String(i + 1), cell(p, "coord")));
  } else if (workspace === "scenario") {
    add(fieldOf("start") && fieldOf("start").value, "T", field("start"));
    add(fieldOf("end") && fieldOf("end").value, "R", field("end"));
  }
  return out;
}

function makeDraftPin([at, text, write]) {
  const marker = L.marker(at, {
    draggable: true, keyboard: false,
    icon: L.divIcon({ className: "", iconSize: [22, 22], iconAnchor: [11, 11], html: `<div class="pin draft">${text}</div>` }),
  }).addTo(sheetLayer);
  // 1 回線のピンと同じ＝動かしている間は欄と線が一緒に動き、離したときに 1 回だけ送る。
  // 線は結果の線のうち、つかんだ所を端に持つもの（複数経路は選んだ行・条件探索は 1 本・中継は前後の区間）
  let ends = [];
  marker.on("dragstart", () => {
    dragging = true;
    const from = marker.getLatLng();
    ends = [];
    sheetLayer.eachLayer((layer) => {
      if (!(layer instanceof L.Polyline)) return;
      if (workspace === "batch" && layer.item !== selected) return;   // 同じ地点を持つほかの行は動かさない
      layer.getLatLngs().forEach((p, i) => { if (p.equals(from, 1e-6)) ends.push([layer, i]); });
    });
  });
  marker.on("drag", (e) => {
    write(e.latlng.wrap(), false);
    ends.forEach(([line, i]) => {
      const pts = line.getLatLngs();
      pts[i] = e.latlng;
      line.setLatLngs(pts);
    });
  });
  marker.on("dragend", () => {
    dragging = false;
    ends = [];
    write(marker.getLatLng().wrap(), true);
  });
}

// 地図＝各件の送信→受信を語の色で・選んだ件を太く。全件そろって座標の組が変われば全体へ合わせる
// ピンを動かしている間は描き直さない（つかんでいるピンを消さない）
function drawSheetLines(complete) {
  if (!sheetLayer || dragging) return;
  sheetLayer.clearLayers();
  if (workspace === "single") return;
  draftPins().forEach(makeDraftPin);
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
      bubblingMouseEvents: false,          // 線を押したら行を選ぶだけ（地点を置くメニューを開かない）
    }).addTo(sheetLayer);
    line.item = i;                         // ピンを動かすとき、どの件の線かを見分ける
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
    if (shouldFit(L.latLngBounds(bounds))) map.fitBounds(L.latLngBounds(bounds), { padding: [40, 40], maxZoom: 15 });
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
  showRowDistances();
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

// ============================================================
// `.rsproj` の開く・保存（ステージ10d）＝中身はサーバーが作る。JS はファイルの API で読み書きするだけ
// （Edge の `showSaveFilePicker`／`showOpenFilePicker`・無ければダウンロードと `<input type=file>`）
// ============================================================
function projNote(text, isErr) {
  const note = document.getElementById("proj-note");
  note.className = isErr ? "err" : "";
  note.textContent = text || "";
  note.title = text || "";
}

const PROJ_TYPES = () => [{ description: labels.proj_filetype, accept: { "application/json": [".rsproj"] } }];

// 打ちかけ（300ms 待ち）を先に送る＝保存する中身を画面に見えている値にそろえる
async function flushAll() {
  if (timer) { clearTimeout(timer); await flush(); }
  if (sheetTimer) { clearTimeout(sheetTimer); await flushSheet(); }
}

// 選んだ場所へ 1 つ書く（Edge の `showSaveFilePicker`・無ければダウンロード）。書いた名前を返す。
// 中身は文字列か Blob（画像）。選ぶ画面を閉じたら null（何もしない）・書けなければ知らせの欄に赤で出して null
// （出し先を替えるときは `onError(文)`＝「設定」の枠は枠の `err` へ出す）
async function saveTextFile(data, name, types, mime, onError) {
  try {
    if (window.showSaveFilePicker) {
      const h = await window.showSaveFilePicker({ suggestedName: name, types });
      const w = await h.createWritable();
      await w.write(data);
      await w.close();
      return h.name;
    }
    const a = el("a", { href: URL.createObjectURL(new Blob([data], { type: mime })), download: name });
    a.click();
    URL.revokeObjectURL(a.href);
    return name;
  } catch (e) {
    if (e && e.name === "AbortError") return null;     // 選ぶ画面を閉じた＝何もしない
    const text = e && e.message ? e.message : String(e);
    if (onError) onError(text); else projNote(text, true);
    return null;
  }
}

async function saveProject() {
  projNote("");
  await flushAll();
  const r = await fetch("/api/project/save", { method: "POST", headers: HEAD, body: "{}" });
  if (!r.ok) { projNote(String(r.status), true); return; }
  const d = await r.json();
  const name = await saveTextFile(d.text, d.name, PROJ_TYPES(), "application/json");
  if (name === null) return;
  // 保存した旨（ファイル名）と、保存しなかった節の警告を 1 行に
  const warn = (d.warnings || []).join(" ");
  projNote([labels.proj_saved.replace("{path}", name).replace(/\s*\n\s*/g, " "), warn].filter(Boolean).join(" "), !!warn);
}

// 作業面の結果の CSV（出力契約の `summary.csv`／`scenario.csv`／`hops.csv`）＝中身はサーバーが作る。
// 全件そろう前は 409＋1 文（知らせの欄に赤）
const CSV_TYPES = [{ description: "CSV", accept: { "text/csv": [".csv"] } }];

const KML_TYPES = [{ description: "KML", accept: { "application/vnd.google-earth.kml+xml": [".kml"] } }];

// 作業面の成果物を 1 つ保存する（CSV・KML 共通）＝中身はサーバーが作る
async function saveSheetFile(url, types, mime) {
  projNote("");
  await flushAll();
  const r = await fetch(url, { method: "POST", headers: HEAD, body: "{}" });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) { projNote(d.message || d.error || String(r.status), true); return; }
  const name = await saveTextFile(d.text, d.name, types, mime);
  if (name !== null) projNote(labels.file_saved.replace("{path}", name));
}

function saveSheetCsv() { return saveSheetFile("/api/sheet/csv", CSV_TYPES, "text/csv"); }
function saveSheetKml() {
  return saveSheetFile("/api/sheet/kml", KML_TYPES, "application/vnd.google-earth.kml+xml");
}

// ---- 画面の画像（10d）＝地図と断面図を別々に（2026-11-08 経営者） --------------------
const PNG_TYPES = [{ description: "PNG", accept: { "image/png": [".png"] } }];
const ZIP_TYPES = [{ description: "ZIP", accept: { "application/zip": [".zip"] } }];

// 地図＝帳票と同じ絵をサーバーが焼く（Leaflet の絵はタイルの canvas が無く PNG にできない）
async function saveMapPng() {
  projNote("");
  await flushAll();
  const r = await fetch("/api/image/map", { method: "POST", headers: HEAD, body: "{}" });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) { projNote(d.message || d.error || String(r.status), true); return; }
  const bytes = Uint8Array.from(atob(d.png), (c) => c.charCodeAt(0));
  const name = await saveTextFile(new Blob([bytes], { type: "image/png" }), d.name, PNG_TYPES, "image/png");
  if (name !== null) projNote(labels.file_saved.replace("{path}", name));
}

// 断面図の上の 1 行＝(左の字＝[名前]・語・余裕度, 語の色)。1 回線は判定の帯・作業面は選んだ行
function profileCaption() {
  if (workspace === "single") {
    if (headline.kind !== "verdict") return null;
    return [`${headline.word}  ${labels.margin || ""} ${dB(headline.margin)} dB`, headline.word];
  }
  const it = selected === null ? null : sheetItems[selected];
  if (!it) return null;
  const m = "margin" in it ? `  ${labels.margin || ""} ${dB(it.margin)} dB` : "";
  return [`${it.label}  ${it.word}${m}`, it.word];
}

// 断面図＝画面の canvas に、上の 1 行（語と余裕度＋凡例）を足して PNG に（ホバーの線は描かない）
async function saveProfilePng() {
  projNote("");
  const cap = profileCaption();
  if (!profile || !cap) { projNote(labels.no_profile || "", true); return; }
  const src = document.getElementById("profile-canvas");
  const keep = hoverFrac;
  hoverFrac = null;
  drawProfile();
  const dpr = window.devicePixelRatio || 1;
  const head = Math.round(26 * dpr);
  const out = el("canvas", { width: src.width, height: src.height + head });
  const g = out.getContext("2d");
  g.fillStyle = getComputedStyle(document.body).backgroundColor;
  g.fillRect(0, 0, out.width, out.height);
  g.drawImage(src, 0, head);
  hoverFrac = keep;
  drawProfile();
  g.scale(dpr, dpr);
  g.textBaseline = "middle";
  g.font = '600 14px "Segoe UI", "Yu Gothic UI", sans-serif';
  g.fillStyle = verdictColor(cap[1]);
  g.textAlign = "left";
  g.fillText(cap[0], PAD.l, 13);
  // 凡例（画面では canvas の外の字＝画像だけ見ても線が読めるように右に並べる）
  g.font = '12px "Segoe UI", "Yu Gothic UI", sans-serif';
  let x = out.width / dpr - PAD.r;
  [["legend_fresnel", "--pf-fresnel"], ["legend_los", "--pf-los"],
    ["legend_vegetation", "--pf-vegetation"], ["legend_terrain", "--pf-terrain"]].forEach(([k, c]) => {
    const t = labels[k] || "";
    x -= g.measureText(t).width;
    g.fillStyle = getComputedStyle(document.body).color;
    g.fillText(t, x, 13);
    x -= 16;
    g.fillStyle = cssColor(c);
    g.fillRect(x, 8, 11, 11);
    x -= 14;
  });
  const blob = await new Promise((done) => out.toBlob(done, "image/png"));
  if (!blob) { projNote(labels.no_profile || "", true); return; }
  const name = await saveTextFile(blob, "profile.png", PNG_TYPES, "image/png");
  if (name !== null) projNote(labels.file_saved.replace("{path}", name));
}

async function openProjectText(text) {
  projNote("");
  // 打ちかけは捨てる（開いたファイルを古い打ちかけで上書きしない）
  if (timer) { clearTimeout(timer); timer = null; }
  Object.keys(pending).forEach((k) => delete pending[k]);
  if (sheetTimer) { clearTimeout(sheetTimer); sheetTimer = null; }
  dirty.clear();
  const r = await fetch("/api/project/open", { method: "POST", headers: HEAD, body: JSON.stringify({ text }) });
  if (!r.ok) {
    const e = await r.json().catch(() => ({}));
    projNote(e.message || e.error || String(r.status), true);
  }
  // 開けたら画面はポーリング（`receiveSheets`／`showInputs`）で描き直る
}

async function openProject() {
  if (!window.showOpenFilePicker) { document.getElementById("proj-file").click(); return; }
  let file;
  try {
    const [h] = await window.showOpenFilePicker({ types: PROJ_TYPES() });
    file = await h.getFile();
  } catch (e) {
    if (e && e.name === "AbortError") return;
    projNote(e && e.message ? e.message : String(e), true);
    return;
  }
  await openProjectText(await file.text());
}

document.getElementById("proj-file").addEventListener("change", async (ev) => {
  const f = ev.target.files[0];
  ev.target.value = "";
  if (f) await openProjectText(await f.text());
});

function buildProjTools() {
  // 1 回線の結果の CSV・KML（10f＝旧画面の保存の `path.kml` を落とさない）＝作業面では表の道具が持つ
  const single = (b) => { b.classList.add("single-only"); return b; };
  document.getElementById("proj-tools").replaceChildren(
    button(labels.open_project, openProject),
    button(labels.save_project, saveProject),
    single(button(labels.save_csv, saveSheetCsv)),
    single(button(labels.save_kml, saveSheetKml)),
    button(labels.save_map_png, saveMapPng),
    button(labels.save_profile_png, saveProfilePng),
  );
}

// ---- テーマ・「設定」の枠・起動時の知らせ（I-257・I-256） ---------------------
// テーマ＝`<html data-theme>` を明暗のどちらかに決める（暗色の値は CSS の 1 か所だけ）。
// システムなら OS の明暗に追従する。
let themeChoice = "system";
const darkQuery = window.matchMedia("(prefers-color-scheme: dark)");

function applyTheme(choice) {
  if (choice) themeChoice = choice;
  const dark = themeChoice === "dark" || (themeChoice === "system" && darkQuery.matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  if (map) {                                     // canvas と地図の線は CSS の色を描いた時に読む
    drawProfile();
    drawSheetLines();
  }
}
darkQuery.addEventListener("change", () => applyTheme());

// 1 項目を替えるたびに保存する＝枠のボタンは閉じるだけ。言語は字の表を読み直す。
// 座標の表記を替えたら端点の欄を新しい表記で書き直して送る（旧画面の `coords.reformat` と同じ＝
// 読めない字はそのまま）。作業面の表の座標は書き換えない（旧画面も 1 回線の欄だけ）。
function reformatCoords() {
  const changes = {};
  ["start", "end"].forEach((key) => {
    const field = fieldOf(key);
    if (!field) return;
    const ll = parseLatLon(field.value);
    if (!ll) return;
    const text = coordText(ll[0], ll[1]);
    if (text !== field.value) {
      field.value = text;
      changes[key] = text;
    }
  });
  if (!Object.keys(changes).length) return;
  Object.assign(pending, changes);
  if (timer) clearTimeout(timer);
  flush();
}

// 背景地図の切り替え＝地図の右上（経営者の目視 2026-11-09＝設定の枠でなく、地図の上ですぐ替えられる所へ）。
// 値は設定の `basemap_layer` と同じ 1 つ＝替えたら保存してタイルを張り直す。失敗したら選び直す前へ戻し、字は欄の下。
let baseSwitch = null;
function buildBaseSwitch(st, name, send) {
  if (!map) return;
  if (baseSwitch) map.removeControl(baseSwitch);
  const pick = el("select", { title: name });
  pick.setAttribute("aria-label", name);
  st.choices.basemap_layer.forEach(([v, t]) => pick.append(el("option", { value: v, textContent: t })));
  pick.value = st.values.basemap_layer;
  const fail = el("div", { className: "err", role: "alert" });
  let current = pick.value;
  pick.addEventListener("change", async () => {
    fail.textContent = "";
    const ok = await send("basemap_layer", pick.value, (text) => { fail.textContent = text; });
    if (ok) current = pick.value;
    else pick.value = current;
  });
  baseSwitch = L.control({ position: "topright" });
  baseSwitch.onAdd = () => {
    const box = el("div", { className: "leaflet-bar basemap-switch" }, [pick, fail]);
    L.DomEvent.disableClickPropagation(box);
    L.DomEvent.disableScrollPropagation(box);
    return box;
  };
  baseSwitch.addTo(map);
}

function buildSettings(st) {
  applyTheme(st.values.theme);
  coordFormat = st.values.coord_format || "dd";
  const L = st.labels;
  const open = document.getElementById("settings-btn");
  open.title = L.title;
  open.setAttribute("aria-label", L.title);
  const dlg = document.getElementById("settings");
  const err = el("div", { className: "err", role: "alert" });
  // 1 つ替えるたびに保存＝通れば true。失敗の字は `fail` へ（無ければ枠の下）
  const send = async (key, value, fail) => {
    err.textContent = "";
    try {
      const r = await fetch("/api/settings", { method: "POST", headers: HEAD, body: JSON.stringify({ key, value }) });
      const body = await r.json();
      if (!r.ok) throw new Error(body.error || String(r.status));
      if (body.reload) {
        location.reload();
        return true;
      }
      applyTheme(body.settings.values.theme);
      if (body.basemap) setBasemap(body.basemap);
      if (key === "coord_format") {
        coordFormat = body.settings.values.coord_format;
        reformatCoords();
      }
      return true;
    } catch (e) {
      const text = String((e && e.message) || e);
      if (fail) fail(text);
      else err.textContent = text;
      return false;
    }
  };
  buildBaseSwitch(st, L.basemap_layer, send);
  const choice = (key) => {
    const s = el("select");
    st.choices[key].forEach(([v, t]) => s.append(el("option", { value: v, textContent: t })));
    s.value = st.values[key];
    s.addEventListener("change", () => send(key, s.value));
    return el("label", {}, [el("span", { textContent: L[key] }), s]);
  };
  const check = (key) => {
    const c = el("input", { type: "checkbox", checked: !!st.values[key] });
    c.addEventListener("change", () => send(key, c.checked));
    return el("label", { className: "check" }, [c, el("span", { textContent: L[key] })]);
  };
  const proxy = el("input", { type: "text", value: st.values.proxy_url || "", spellcheck: false, autocomplete: "off" });
  proxy.addEventListener("change", () => send("proxy_url", proxy.value));
  // 「ファイル」の口（I-257 の C）＝結果の 1 文は `note`・失敗は `err`（どちらも枠の下）
  const note = el("div", { className: "note", role: "status" });
  const call = async (url, body) => {
    err.textContent = "";
    note.textContent = "";
    try {
      const r = await fetch(url, { method: "POST", headers: HEAD, body: JSON.stringify(body || {}) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.message || d.error || String(r.status));
      return d;
    } catch (e) {
      err.textContent = String((e && e.message) || e);
      return null;
    }
  };
  const say = (d) => { if (d && d.message) note.textContent = d.message; };
  const loadFile = async (url) => {
    const text = await pickJsonText();
    if (text === null) return;
    const d = await call(url, { text });
    if (!d) return;
    if (d.reload) { location.reload(); return; }
    if (d.settings) {                              // アプリ設定＝枠を今の値で組み直す
      buildSettings(d.settings);
      setBasemap(d.basemap);
      reformatCoords();
      document.getElementById("settings").querySelector(".note").textContent = d.message;
      return;
    }
    say(d);
  };
  const confirmBox = el("div", { className: "confirm", hidden: true });
  const askDelete = async () => {
    const d = await call("/api/cache/breakdown");
    if (!d) return;
    const picks = d.sources.map((s) => {
      const c = el("input", { type: "checkbox", checked: true });
      c.dataset.id = s.id;
      return el("label", { className: "check" }, [c, el("span", { textContent: s.label })]);
    });
    const base = d.basemap === null ? null : el("input", { type: "checkbox", checked: true });
    const done = async () => {
      confirmBox.hidden = true;
      const ids = d.sources.length ? picks.map((p) => p.querySelector("input"))
        .filter((c) => c.checked).map((c) => c.dataset.id) : null;
      say(await call("/api/cache/delete", { sources: ids, basemap: base ? base.checked : true }));
    };
    confirmBox.replaceChildren(
      el("div", { textContent: d.confirm }),
      ...picks,
      ...(base ? [el("label", { className: "check" }, [base, el("span", { textContent: d.basemap })])] : []),
      el("div", { className: "actions" }, [
        button(d.cancel, () => { confirmBox.hidden = true; }), button(d.ok, done)]),
    );
    confirmBox.hidden = false;
  };
  // 「ヘルプ」の口（I-257 の D）＝診断は保存の前に何が入るかを見せて選ばせる
  // （環境事実は既定でチェック・結果は既定でチェックなし＝案件の座標が入る）
  const diagBox = el("div", { className: "confirm diag", hidden: true });
  const askDiagnostics = async () => {
    confirmBox.hidden = true;
    const d = await call("/api/help/diagnostics");
    if (!d) return;
    const pick = (value, text, on) => {
      const c = el("input", { type: "checkbox", checked: on });
      c.dataset.id = value;
      return el("label", { className: "check" }, [c, el("span", { textContent: text })]);
    };
    const facts = d.facts.map((f) => pick(f.id, f.label, true));
    const results = d.results.map((r) => pick(r, r, false));
    const chosen = (picks) => picks.map((p) => p.querySelector("input"))
      .filter((c) => c.checked).map((c) => c.dataset.id);
    const save = async () => {
      const z = await call("/api/help/diagnostics/zip", { facts: chosen(facts), results: chosen(results) });
      if (!z) return;
      const bytes = Uint8Array.from(atob(z.zip), (c) => c.charCodeAt(0));
      const name = await saveTextFile(new Blob([bytes], { type: "application/zip" }), d.name, ZIP_TYPES,
        "application/zip", (text) => { err.textContent = text; });
      if (name === null) return;
      diagBox.hidden = true;
      note.textContent = d.saved.replace("{path}", name);
    };
    diagBox.replaceChildren(
      el("div", { textContent: d.intro }),
      el("h4", { textContent: d.facts_group }),
      ...facts,
      el("h4", { textContent: d.results_group }),
      el("div", { className: "hint", textContent: d.results_hint }),
      results.length ? el("div", { className: "list" }, results) : el("div", { className: "hint", textContent: d.no_results }),
      el("div", { className: "actions" }, [
        button(d.cancel, () => { diagBox.hidden = true; }), button(d.save, save)]),
    );
    diagBox.hidden = false;
  };
  const checkUpdates = async (b) => {
    b.disabled = true;                              // 待つ間は二度押しさせない
    try { say(await call("/api/help/check_updates")); } finally { b.disabled = false; }
  };
  const updatesBtn = button(L.check_updates, () => checkUpdates(updatesBtn));
  dlg.replaceChildren(
    el("h2", { textContent: L.title }),
    choice("lang"),
    el("div", { className: "row" }, [
      button(L.lang_template, async () => say(await call("/api/lang_template")))]),
    choice("theme"),
    choice("coord_format"),
    el("label", {}, [el("span", { textContent: L.proxy_url }), proxy]),
    el("div", { className: "hint", textContent: L.proxy_hint }),
    check("update_check_auto"),
    check("update_check_prerelease"),
    el("h3", { textContent: L.file }),
    el("div", { className: "row" }, [
      button(L.load_sim, () => loadFile("/api/settings/load_sim")),
      button(L.load_app, () => loadFile("/api/settings/load_app")),
      button(L.delete_cache, () => { diagBox.hidden = true; askDelete(); }),
    ]),
    confirmBox,
    el("h3", { textContent: L.help }),
    el("div", { className: "row" }, [
      button(L.open_docs, () => call("/api/help/docs")),
      button(L.diagnostics, askDiagnostics),
      updatesBtn,
      button(L.about, () => { err.textContent = ""; note.textContent = L.about_text; }),
    ]),
    diagBox,
    err,
    note,
    el("div", { className: "actions" }, [button(L.close, () => dlg.close())]),
  );
}

// 設定の JSON を 1 つ選んで本文を返す（選ぶ画面を閉じたら null）。無い環境は `<input type=file>`
const JSON_TYPES = [{ description: "JSON", accept: { "application/json": [".json"] } }];
async function pickJsonText() {
  try {
    if (window.showOpenFilePicker) {
      const [h] = await window.showOpenFilePicker({ types: JSON_TYPES });
      return await (await h.getFile()).text();
    }
    const input = el("input", { type: "file", accept: ".json,application/json" });
    const file = await new Promise((done) => {
      input.addEventListener("change", () => done(input.files[0] || null));
      input.click();
    });
    return file ? await file.text() : null;
  } catch (e) {
    if (e && e.name === "AbortError") return null;
    throw e;
  }
}
document.getElementById("settings-btn").addEventListener("click", () => {
  const dlg = document.getElementById("settings");
  if (dlg.childElementCount) dlg.showModal();
});

// 知らせ＝サーバーは起動 1 回ぶんを全部返す。閉じた印はこのページだけが持つ。
const dismissed = new Set();
let noticeSig = "";

function showNotices(list) {
  const shown = (list || []).filter((n) => !dismissed.has(n.id));
  const sig = shown.map((n) => n.id).join(",");
  if (sig === noticeSig) return;                 // 長いポーリングのたびに組み直さない
  noticeSig = sig;
  document.getElementById("notices").replaceChildren(...shown.map((n) => {
    const row = el("div", {}, [el("span", { textContent: n.text })]);
    row.dataset.kind = n.kind;
    row.append(button("×", () => {
      dismissed.add(n.id);
      showNotices(list);
    }));
    return row;
  }));
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
        buildProjTools();
        buildForm(s.form || []);
        buildReadout("breakdown", s.breakdown || []);
        buildReadout("distances", s.distances || []);
        if (Number.isInteger(s.db_digits)) dbDigits = s.db_digits;
        if (!map) initMap(s.basemap || { id: "pale" });
        if (s.sheet_spec) {
          spec = s.sheet_spec;
          buildTabs();
        }
        if (s.settings) buildSettings(s.settings);
      }
      revision = s.revision;
      showNotices(s.notices);
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
applyTheme();                                    // 設定が届くまでは OS の明暗
poll();
