// 新しい画面（Link 1.0）。字と欄の並びはすべてサーバーの表から＝ここに日本語を書かない。
"use strict";

const TOKEN = location.hash.slice(1);
const HEAD = { "X-Token": TOKEN, "Content-Type": "application/json" };
const DEBOUNCE_MS = 300;

let labels = {};
let breakdownRows = [];
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
  const band = document.getElementById("band");
  const word = document.getElementById("band-word");
  const detail = document.getElementById("band-detail");
  band.dataset.kind = h.kind;
  band.dataset.word = h.word || "";
  if (h.kind === "verdict") {
    word.textContent = h.word;
    detail.textContent = `${h.margin} dB · ${h.p_rx} dBm`;
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
  const a = parseLatLon(inputs.start);
  const b = parseLatLon(inputs.end);
  if (map && a && b) {
    if (!path) {
      path = L.polyline([a, b], { weight: 3 }).addTo(map);
      map.fitBounds(path.getBounds(), { padding: [40, 40] });
    } else {
      path.setLatLngs([a, b]);
    }
  }
}

function initMap(basemap) {
  map = L.map("map", { zoomControl: true });
  L.tileLayer(`/tile/${basemap}/{z}/{x}/{y}.png`, { maxZoom: 18 }).addTo(map);
  map.setView([35.0, 135.0], 5);
}

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
