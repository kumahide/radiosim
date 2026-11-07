// 新しい画面の殻（4.0 ステージ8）。字はすべてサーバーの字の表から＝ここに日本語を書かない。
"use strict";

const TOKEN = location.hash.slice(1);
const HEAD = { "X-Token": TOKEN, "Content-Type": "application/json" };
const DEBOUNCE_MS = 300;

let labels = {};
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
}

function parseLatLon(text) {
  const parts = String(text).split(",").map((s) => parseFloat(s));
  return parts.length === 2 && parts.every(Number.isFinite) ? parts : null;
}

function showInputs(inputs) {
  document.querySelectorAll("input[data-key]").forEach((el) => {
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
  timer = setTimeout(flush, DEBOUNCE_MS);
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

document.querySelectorAll("input[data-key]").forEach((el) => el.addEventListener("input", onEdit));
window.addEventListener("pagehide", () => {
  navigator.sendBeacon(`/api/bye?t=${encodeURIComponent(TOKEN)}`);
});
poll();
