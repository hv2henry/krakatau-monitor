/* ============================================================================
   app.js — Krakatau community dashboard.
   No build step, no framework: plain JS so a non-developer can edit safely.
   Reads only local JSON produced by build_site.py (the site never scrapes).
   ==========================================================================*/
"use strict";

/* ---------------------------------------------------------------- i18n ---- */
const I18N = {
  id: {
    title: "Pantau Anak Krakatau",
    subtitle: "Dasbor komunitas — data resmi + model sekunder",
    sec_status: "Status Aktivitas",
    sec_report: "Laporan Pengamatan Terakhir",
    sec_eruptions: "Kejadian Erupsi",
    sec_vona: "VONA (Penerbangan)",
    sec_vaac: "Advisori Abu Vulkanik — Darwin VAAC",
    sec_sat: "Citra Satelit Harian",
    sec_model: "Model Arah Abu (Sekunder)",
    src_model: "Himawari-9 + open-meteo — bukan data resmi",
    src_magma: 'Sumber: <a href="https://magma.esdm.go.id" target="_blank" rel="noopener">MAGMA Indonesia / PVMBG</a>',
    src_magma2: 'Sumber: <a href="https://magma.esdm.go.id/v1/gunung-api/laporan" target="_blank" rel="noopener">MAGMA Indonesia / PVMBG</a>',
    src_vaac: 'Sumber: <a href="https://www.bom.gov.au/aviation/volcanic-ash/darwin-va-advisory.shtml" target="_blank" rel="noopener">Bureau of Meteorology (Australia), ICAO VAAC</a>',
    src_firms: 'Sumber: <a href="https://firms.modaps.eosdis.nasa.gov" target="_blank" rel="noopener">NASA FIRMS / GIBS</a> (Suomi NPP VIIRS &amp; Aqua MODIS)',
    foot_official: "Sumber resmi",
    foot_disclaim_t: "Penyangkalan",
    foot_disclaim: 'Situs komunitas <b>tidak resmi</b>. Bukan sistem peringatan dini. Selalu ikuti arahan PVMBG, BNPB/BPBD, dan otoritas penerbangan. Data © lembaga masing-masing.',
    foot_agents: "Untuk mesin / AI agents",
    foot_agents1: "data terstruktur, skema v1",
    foot_agents2: "model (bila dipublikasikan)",
    level: "Tingkat aktivitas",
    province: "Wilayah",
    position: "Posisi",
    updated: "diperbarui",
    fetched: "Diambil",
    stale: "KEDALUWARSA",
    nationwide: "Seluruh Indonesia",
    period: "Periode",
    observer: "Petugas",
    visual: "Pengamatan visual",
    weather: "Cuaca",
    seismic: "Kegempaan",
    seismic_n: "kejadian",
    recommendation: "Rekomendasi resmi",
    seismogram: "Seismogram (PVMBG)",
    no_eruption: "Tidak ada kejadian erupsi tercatat pada umpan saat ini.",
    no_vona: "Tidak ada VONA pada umpan saat ini.",
    vaac_advisory: "Advisori",
    vaac_nil: "Tidak ada advisori aktif",
    vaac_nil_body: "Darwin VAAC tidak menerbitkan advisori saat ini. <b>Ini BUKAN berarti tidak ada bahaya</b> — advisori hanya terbit bila abu teridentifikasi dan relevan bagi penerbangan. Untuk status erupsi, lihat MAGMA/PVMBG di atas.",
    vaac_stale: "Advisori untuk gunung lain aktif, tetapi tidak ada untuk Krakatau. Ketidakadaan advisori bukan berarti aman.",
    issued: "Terbit",
    next_adv: "Advisori berikutnya paling lambat",
    eruption_detail: "Detail erupsi",
    obs_cloud: "Awan abu TERAMATI",
    fcst_cloud: "Prakiraan awan abu",
    moves: "bergerak ke",
    at_speed: "dengan kecepatan",
    remarks: "Catatan VAAC",
    bulletin: "Buletin asli (teks WMO)",
    graphic: "Grafik advisori resmi (BoM)",
    layer: "Lapisan",
    height: "Tinggi",
    motion: "Gerakan",
    valid: "Berlaku",
    map_obs: "VAAC teramati",
    map_model: "Model sekunder (Himawari-9 + open-meteo)",
    map_note: "Peta skematik — garis pantai Natural Earth. BUKAN untuk navigasi.",
    sat_none: "Citra harian belum tersedia.",
    sec_loop: "Animasi Himawari-9 (Inframerah)",
    src_loop: 'Sumber: <a href="https://worldview.earthdata.nasa.gov" target="_blank" rel="noopener">NASA GIBS</a>/JMA Himawari-9 AHI Band 13',
    loop_none: "Animasi belum tersedia (butuh ≥4 frame). Akan muncul pada build berikutnya.",
    loop_cap: "Putar untuk melihat pergerakan awan/abu. Putih = puncak awan dingin/tinggi; gelap = permukaan hangat. Garis pantai tipis + titik merah = Anak Krakatau.",
    loop_eruptions: "Tanda merah pada garis waktu = waktu erupsi menurut MAGMA/PVMBG.",
    model_plume_top: "Puncak awan abu resmi hari ini",
    model_no_top: "Tidak ada puncak awan abu resmi hari ini — semua lapisan ditampilkan setara.",
    model_relevant: "paling relevan hari ini",
    model_traj_kind: "Garis pergerakan memakai angin prakiraan yang berubah per jam ({kind}); varian angin-tetap tersedia di forecast_model.json.",
    verbatim_note: "Seluruh teks dari lembaga resmi (PVMBG/MAGMA, VONA, Darwin VAAC) ditampilkan apa adanya, tanpa suntingan — termasuk bila sumber mengandung pengulangan kalimat.",
    abbr_note: "dpl = di atas permukaan laut · ft = kaki · km = kilometer",
    star_note: "★ = lapisan paling relevan hari ini (berdasar puncak awan abu resmi)",
    loop_latency: "Frame tertinggal ±20–60 menit dari waktu nyata karena pemrosesan NASA — wajar, bukan kesalahan data.",
    loop_verified: "Waktu frame terverifikasi: grid citra 10-menit Himawari {grid} · slot citra ada di NOAA S3 {noaa} ({slot}).",
    loop_verify_hint: "Klik untuk membuka tile sumber NASA frame ini (verifikasi mandiri)",
    map_need_network: "Peta interaktif butuh koneksi internet (tile © OpenStreetMap). Data poligon tetap dapat dibaca mesin di data/snapshot.json.",
    gal_of: "dari",
    sat_open: "Buka peta interaktif FIRMS untuk tanggal ini",
    model_unpub_t: "Model belum dipublikasikan",
    model_unpub_b: "Model arah abu dihitung otomatis dari angin satelit Himawari-9 dan prakiraan open-meteo, tetapi hanya tayang setelah <b>disetujui oleh manusia</b> dan lolos validasi. Saat ini belum ada versi yang dipublikasikan.",
    model_approved: "Disetujui oleh",
    model_computed: "Dihitung",
    model_valid: "Validasi",
    model_layers: "Lapisan model & arah pergerakan",
    model_conf: "keyakinan",
    model_traj: "Perkiraan posisi abu",
    model_show_map: "Tampilkan sebagai lapisan peta",
    disclosure: '⚠️ <b>Model ini dihitung OTOMATIS</b> dari angin satelit Himawari-9 dan prakiraan open-meteo — <b>bisa tidak akurat</b>. Ini BUKAN keluaran PVMBG, BNPB, maupun Darwin VAAC. ketinggian dan arah abu dapat berubah cepat. Untuk keputusan apa pun, gunakan rilis resmi: <a href="https://magma.esdm.go.id" target="_blank" rel="noopener">MAGMA/PVMBG</a>, <a href="https://www.bnpb.go.id" target="_blank" rel="noopener">BNPB/BPBD</a>, <a href="https://www.bom.gov.au/aviation/volcanic-ash/darwin-va-advisory.shtml" target="_blank" rel="noopener">Darwin VAAC</a>.',
    hours_ago: (n) => `${n} jam lalu`,
    min_ago: (n) => `${n} menit lalu`,
    days_ago: (n) => `${n} hari lalu`,
    just_now: "baru saja",
  },
  en: {
    title: "Anak Krakatau Watch",
    subtitle: "Community dashboard — official data + secondary model",
    sec_status: "Activity Status",
    sec_report: "Latest Observation Report",
    sec_eruptions: "Eruption Events",
    sec_vona: "VONA (Aviation)",
    sec_vaac: "Volcanic Ash Advisory — Darwin VAAC",
    sec_sat: "Daily Satellite Imagery",
    sec_model: "Ash Direction Model (Secondary)",
    src_model: "Himawari-9 + open-meteo — not official data",
    src_magma: 'Source: <a href="https://magma.esdm.go.id" target="_blank" rel="noopener">MAGMA Indonesia / PVMBG</a>',
    src_magma2: 'Source: <a href="https://magma.esdm.go.id/v1/gunung-api/laporan" target="_blank" rel="noopener">MAGMA Indonesia / PVMBG</a>',
    src_vaac: 'Source: <a href="https://www.bom.gov.au/aviation/volcanic-ash/darwin-va-advisory.shtml" target="_blank" rel="noopener">Bureau of Meteorology (Australia), ICAO VAAC</a>',
    src_firms: 'Source: <a href="https://firms.modaps.eosdis.nasa.gov" target="_blank" rel="noopener">NASA FIRMS / GIBS</a> (Suomi NPP VIIRS &amp; Aqua MODIS)',
    foot_official: "Official sources",
    foot_disclaim_t: "Disclaimer",
    foot_disclaim: 'An <b>unofficial</b> community site. Not an early-warning system. Always follow PVMBG, BNPB/BPBD and aviation authority guidance. Data © respective agencies.',
    foot_agents: "For machines / AI agents",
    foot_agents1: "structured data, schema v1",
    foot_agents2: "model (when published)",
    level: "Alert level",
    province: "Region",
    position: "Position",
    updated: "updated",
    fetched: "Fetched",
    stale: "STALE",
    nationwide: "Indonesia-wide",
    period: "Period",
    observer: "Observer",
    visual: "Visual observation",
    weather: "Weather",
    seismic: "Seismicity",
    seismic_n: "events",
    recommendation: "Official recommendation",
    seismogram: "Seismogram (PVMBG)",
    no_eruption: "No eruption events in the current feed.",
    no_vona: "No VONA in the current feed.",
    vaac_advisory: "Advisory",
    vaac_nil: "No active advisory",
    vaac_nil_body: "Darwin VAAC has no current advisory. <b>This does NOT mean no hazard</b> — advisories are issued only when ash is identifiable and relevant to aviation. For eruption status see MAGMA/PVMBG above.",
    vaac_stale: "Advisories are active for other volcanoes but none for Krakatau. Absence of an advisory is not an all-clear.",
    issued: "Issued",
    next_adv: "Next advisory no later than",
    eruption_detail: "Eruption details",
    obs_cloud: "OBSERVED ash cloud",
    fcst_cloud: "Forecast ash cloud",
    moves: "moving toward",
    at_speed: "at",
    remarks: "VAAC remarks",
    bulletin: "Original bulletin (WMO text)",
    graphic: "Official advisory chart (BoM)",
    layer: "Layer",
    height: "Height",
    motion: "Motion",
    valid: "Valid",
    map_obs: "VAAC observed",
    map_model: "Secondary model (Himawari-9 + open-meteo)",
    map_note: "Schematic map — Natural Earth coastlines. NOT for navigation.",
    sat_none: "Daily imagery not available yet.",
    sec_loop: "Himawari-9 Animation (Infrared)",
    src_loop: 'Source: <a href="https://worldview.earthdata.nasa.gov" target="_blank" rel="noopener">NASA GIBS</a>/JMA Himawari-9 AHI Band 13',
    loop_none: "Animation not available yet (needs ≥4 frames). It will appear on the next build.",
    loop_cap: "Press play to watch cloud/ash motion. White = cold/high cloud tops; dark = warm surface. Thin coastline + red dot = Anak Krakatau.",
    loop_eruptions: "Red marks on the timeline = eruption times per MAGMA/PVMBG.",
    model_plume_top: "Official ash-cloud top today",
    model_no_top: "No official ash-cloud top today — all layers shown equally.",
    model_relevant: "most relevant today",
    model_traj_kind: "Trajectories use hourly-evolving forecast wind ({kind}); a steady-wind variant ships in forecast_model.json.",
    verbatim_note: "All text from official agencies (PVMBG/MAGMA, VONA, Darwin VAAC) is shown verbatim, unedited — including where the source itself repeats a sentence.",
    abbr_note: "asl = above sea level · ft = feet · km = kilometres",
    star_note: "★ = most relevant layer today (based on the official ash-cloud top)",
    loop_latency: "Frames lag real time by ±20–60 min due to NASA processing — expected, not a data error.",
    loop_verified: "Frame times verified: Himawari 10-min imaging grid {grid} · imaging slot present on NOAA S3 {noaa} ({slot}).",
    loop_verify_hint: "Click to open NASA's source tile for this frame (self-verification)",
    map_need_network: "The interactive map needs an internet connection (tiles © OpenStreetMap). Polygon data remains machine-readable at data/snapshot.json.",
    gal_of: "of",
    sat_open: "Open the FIRMS interactive map for this date",
    model_unpub_t: "Model not published",
    model_unpub_b: "The ash-direction model is computed automatically from Himawari-9 and open-meteo, but only appears after a <b>human approves</b> it and it passes validation. No published version right now.",
    model_approved: "Approved by",
    model_computed: "Computed",
    model_valid: "Validation",
    model_layers: "Model layers & motion",
    model_conf: "confidence",
    model_traj: "Projected ash positions",
    model_show_map: "Show as map layer",
    disclosure: '⚠️ <b>This model is computed AUTOMATICALLY</b> from Himawari-9 satellite winds and open-meteo forecasts — <b>it can be inaccurate</b>. It is NOT output from PVMBG, BNPB or Darwin VAAC. Ash height and direction can change quickly. For any decision use official releases: <a href="https://magma.esdm.go.id" target="_blank" rel="noopener">MAGMA/PVMBG</a>, <a href="https://www.bnpb.go.id" target="_blank" rel="noopener">BNPB/BPBD</a>, <a href="https://www.bom.gov.au/aviation/volcanic-ash/darwin-va-advisory.shtml" target="_blank" rel="noopener">Darwin VAAC</a>.',
    hours_ago: (n) => `${n} h ago`,
    min_ago: (n) => `${n} min ago`,
    days_ago: (n) => `${n} d ago`,
    just_now: "just now",
  },
};

let LANG = localStorage.getItem("krak-lang") ||
           ((navigator.language || "id").toLowerCase().startsWith("id") ? "id" : "en");
const T = (k) => (I18N[LANG][k] !== undefined ? I18N[LANG][k] : I18N.id[k] !== undefined ? I18N.id[k] : k);

/* ------------------------------------------------------------- helpers ---- */
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function relWib(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return "";
  const mins = Math.max(0, Math.round((Date.now() - d.getTime()) / 60000));
  if (mins < 2) return T("just_now");
  if (mins < 60) return T("min_ago")(mins);
  if (mins < 48 * 60) return T("hours_ago")(Math.round(mins / 60));
  return T("days_ago")(Math.round(mins / 1440));
}
/* WIB formatter: data carries UTC ISO; WIB is a fixed +07:00 offset. */
function fmtWib(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return String(iso);
  const w = new Date(d.getTime() + 7 * 3600e3);
  const M = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"];
  const p = (n) => String(n).padStart(2, "0");
  return `${p(w.getUTCDate())} ${M[w.getUTCMonth()]} ${w.getUTCFullYear()}, ${p(w.getUTCHours())}:${p(w.getUTCMinutes())} WIB`;
}
function flHuman(fl) {
  if (!fl || fl === "SFC") return LANG === "id" ? "permukaan tanah" : "ground level";
  const m = /^FL(\d{3})$/.exec(fl);
  if (!m) return fl;
  const feet = +m[1] * 100, km = feet * 0.3048 / 1000;
  return `${fl} = ${feet.toLocaleString(LANG === "id" ? "id-ID" : "en-US")} ft ≈ ${km.toFixed(1)} km`;
}
/* Compass points stay ENGLISH in both languages (maintainer decision):
   16-point names like WNW have no tidy ID equivalent, and mixing half-translated
   compass roses confused readers. N/E/S/W/WNW/SSE everywhere. */

/* ------------------------------------------------------------ state ------- */
let SNAP = null, MODEL = null, MODEL_ERR = null;

async function loadJSON(url, opt) {
  // No cache-busting query: GitHub Pages' CDN answers repeat visitors fast.
  // Freshness still lands within ~10 min (CDN TTL) + our 5-min refetch, and the
  // "updated" chip always shows the true data age, so staleness is visible.
  const r = await fetch(url, opt);
  if (!r.ok) throw new Error(url + " -> " + r.status);
  return r.json();
}

/* ------------------------------------------------------------ render ------ */
function applyI18n() {
  document.documentElement.lang = LANG;
  document.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = T(el.dataset.i18n); });
  document.querySelectorAll("[data-i18n-html]").forEach((el) => { el.innerHTML = T(el.dataset.i18nHtml); });
  const fl = $("#flag-icon");
  if (fl) fl.innerHTML = `<use href="#${LANG === "id" ? "f-en" : "f-id"}"/>`;
  document.title = LANG === "id"
    ? "Pantau Gunung Anak Krakatau — Dasbor Komunitas"
    : "Anak Krakatau Watch — Community Dashboard";
}

function renderStatus() {
  const s = SNAP.status, v = SNAP.volcano;
  const lv = s.level || 0;
  $("#card-status").innerHTML = `
    <div style="display:flex;gap:16px;align-items:center;flex-wrap:wrap">
      <span class="badge l${lv}">${esc(s.level_name || "?")}</span>
      <div>
        <div style="font-size:1.05rem;font-weight:700">${esc(v.name)}</div>
        <div class="stamp">${esc(v.province || "")} · ${v.lat}°, ${v.lon}° · 157 m dpl/ASL</div>
      </div>
    </div>
    <div class="kv" style="margin-top:10px"><span class="k">${T("fetched")}</span>
      <span class="v">${fmtWib(s.fetched_utc)} <span class="stamp">(${relWib(s.fetched_utc)})</span></span></div>
    ${s.indonesia_counts ? `<div class="kv"><span class="k">${T("nationwide")}</span><span class="v stamp">
      ${Object.entries(s.indonesia_counts).map(([k, n]) => `${esc(k)}: <b>${n}</b>`).join(" · ")}</span></div>` : ""}`;
}

function renderReport() {
  const r = SNAP.report;
  if (!r || !r.period) { $("#card-report").innerHTML = `<p class="stamp">—</p>`; return; }
  const seis = r.seismic_counts || {};
  const rows = Object.entries(seis).sort((a, b) => b[1] - a[1])
    .map(([k, n]) => `<tr><td>${esc(k)}</td><td class="num"><b>${n}</b></td></tr>`).join("");
  $("#card-report").innerHTML = `
    <div class="kv"><span class="k">${T("period")}</span><span class="v"><b>${esc(r.period)}</b></span></div>
    <div class="kv"><span class="k">${T("observer")}</span><span class="v">${esc(r.author || "—")}</span></div>
    <p class="stamp">${T("verbatim_note")}</p>
    <div class="kv"><span class="k">${T("visual")}</span><span class="v">${esc(r.visual || "—")}</span></div>
    <div class="kv"><span class="k">${T("weather")}</span><span class="v">${esc(r.climate || "—")}</span></div>
    <div class="grid2" style="margin-top:10px">
      <div><div class="stamp" style="margin-bottom:4px">${T("seismic")}</div>
        <table><thead><tr><th></th><th style="text-align:right">${T("seismic_n")}</th></tr></thead>
        <tbody>${rows || "<tr><td colspan=2>—</td></tr>"}</tbody></table></div>
      <div>${r.seismogram_asset ? `<div class="stamp" style="margin-bottom:4px">${T("seismogram")}</div>
        <img class="pic" src="${esc(r.seismogram_asset)}" alt="seismogram PVMBG" loading="lazy">` : ""}</div>
    </div>
    ${r.recommendation ? `<div class="callout warn"><b>${T("recommendation")}:</b> ${esc(r.recommendation)}</div>` : ""}`;
}

function renderEruptions() {
  const list = SNAP.eruptions || [];
  $("#card-eruptions").innerHTML = list.length ? `<ul class="feed">${list.map((e) => `
    <li><span class="t">${esc(e.wib || fmtWib(e.utc))} ${e.utc ? `<span class="stamp">(${relWib(e.utc)})</span>` : ""}</span>
    ${esc(e.text || "")}${e.url ? ` <a href="${esc(e.url)}" target="_blank" rel="noopener"><svg class="ic" style="width:12px;height:12px"><use href="#i-ext"/></svg></a>` : ""}</li>`).join("")}</ul>`
    : `<p class="stamp">${T("no_eruption")}</p>`;
}

function renderVona() {
  const list = SNAP.vona || [];
  $("#card-vona").innerHTML = list.length ? `<ul class="feed">${list.map((v) => `
    <li><span class="badge sm ${esc(v.code)}">${esc(v.code)}</span>
        <span class="t" style="display:inline;margin-left:8px">${esc(v.wib || fmtWib(v.issued_utc))}</span>
      <div style="margin-top:4px">${esc(v.text || "")}</div></li>`).join("")}</ul>`
    : `<p class="stamp">${T("no_vona")}</p>`;
}

function layerTable(layers) {
  if (!layers || !layers.length) return `<p class="stamp">—</p>`;
  return `<table><thead><tr><th>${T("layer")}</th><th>${T("height")}</th><th>${T("motion")}</th></tr></thead><tbody>
    ${layers.map((l) => `<tr>
      <td>${esc(l.base)}–${esc(l.top)}</td>
      <td>${esc(LANG === "id" ? l.top_human_id : l.top_human_en)}</td>
      <td><b>${esc(l.move_toward)}</b> · ${l.speed_kt} kt ≈ ${l.speed_ms} m/s</td>
    </tr>`).join("")}</tbody></table>`;
}

function renderVaac() {
  const v = SNAP.vaac;
  if (v.state !== "advisory" || !v.advisory_nr) {
    $("#card-vaac").innerHTML = `<div class="callout warn"><b>${T("vaac_nil")}.</b>
      ${v.state === "stale" ? T("vaac_stale") : T("vaac_nil_body")}</div>`;
    return;
  }
  const fc = Object.entries(v.forecasts || {}).map(([k, f]) => `
    <div class="kv"><span class="k">${T("fcst_cloud")} ${esc(k)}</span><span class="v stamp">${esc(f.valid_wib || fmtWib(f.valid_utc))}</span></div>
    ${layerTable(f.layers)}`).join("");
  $("#card-vaac").innerHTML = `
    <div style="display:flex;gap:12px;align-items:baseline;flex-wrap:wrap">
      <b style="font-size:1.02rem">${T("vaac_advisory")} ${esc(v.advisory_nr)}</b>
      <span class="stamp">${T("issued")}: <b>${esc(v.dtg_wib || fmtWib(v.dtg_utc))}</b> (${relWib(v.dtg_utc)})</span>
    </div>
    <div class="kv" style="margin-top:8px"><span class="k">${T("eruption_detail")}</span><span class="v">${esc(v.eruption_details || "—")}</span></div>
    <div class="stamp" style="margin:8px 0 2px">${T("obs_cloud")}</div>
    ${layerTable(v.observed_layers)}
    <p class="stamp">${T("abbr_note")}</p>
    <div style="margin-top:12px">${fc}</div>
    ${v.remarks ? `<div class="callout"><b>${T("remarks")}:</b> ${esc(v.remarks)}</div>` : ""}
    ${v.next_advisory_by_wib ? `<div class="kv"><span class="k">${T("next_adv")}</span><span class="v">${esc(v.next_advisory_by_wib)}</span></div>` : ""}
    <div class="grid2" style="margin-top:12px">
      <div>${v.graphic_asset ? `<div class="stamp" style="margin-bottom:4px">${T("graphic")}</div>
        <img class="pic" src="${esc(v.graphic_asset)}" alt="Darwin VAAC graphical advisory" loading="lazy">` : ""}</div>
      <div><details><summary>${T("bulletin")}</summary><pre class="bulletin">${esc(v.bulletin_text || "")}</pre></details>
        <p class="stamp" style="margin-top:8px"><a href="${esc(v.source_url)}" target="_blank" rel="noopener">${esc(v.source_url)}</a></p></div>
    </div>`;
}

function renderSat() {
  const s = SNAP.satellite || {};
  const cards = ["snpp", "modis"].map((k) => {
    const d = s[k];
    if (!d) return `<div class="card"><p class="stamp">${T("sat_none")}</p></div>`;
    const sensor = d.sensor_label || (k === "snpp" ? "Suomi NPP (VIIRS)" : "Aqua (MODIS)");
    const firmsUrl = `https://firms.modaps.eosdis.nasa.gov/map/#d/${d.date},${d.date}/@105.423,-6.102,8z`;
    return `<div class="card"><div class="stamp"><b>${sensor}</b> · ${esc(d.date)}</div>
      <img class="pic" style="margin-top:6px" src="${esc(d.asset)}" alt="${sensor} true color ${esc(d.date)}" loading="lazy">
      <div class="figcap">${d.sensor_note ? esc(d.sensor_note) + " · " : ""}${esc(d.credit)} ·
        <a href="${firmsUrl}" target="_blank" rel="noopener">${T("sat_open")} <svg class="ic" style="width:11px;height:11px"><use href="#i-ext"/></svg></a></div></div>`;
  });
  $("#card-sat").innerHTML = cards.join("");
}

const BAND_COLORS = ["#6b7280", "#d97706", "#9b2b1a", "#7c3aed", "#2563eb", "#16a34a"];

function renderModel() {
  $("#model-disclosure").innerHTML = T("disclosure");
  const box = $("#card-model");
  if (!MODEL || MODEL.status !== "approved") {
    box.innerHTML = `<div class="model-unpub">
      <svg class="ic"><use href="#i-shield"/></svg>
      <h3 style="margin:8px 0 4px">${T("model_unpub_t")}</h3>
      <p class="stamp" style="max-width:520px;margin:0 auto">${T("model_unpub_b")}</p></div>`;
    return;
  }
  const rows = MODEL.layers.map((l, i) => `
    <tr><td><span class="sw" style="display:inline-block;width:11px;height:11px;border-radius:3px;background:${BAND_COLORS[i % 6]};margin-right:7px"></span>${esc(l.layer)}
      ${l.relevant_today ? `<span class="star" title="${esc(T("star_note"))}">★</span>` : ""}</td>
      <td>${esc(LANG === "id" ? l.alt_human_id : l.alt_human_en)}</td>
      <td><b>${esc(l.toward_compass)}</b> (${l.toward_deg.toFixed(0)}°) · ${l.speed_ms.toFixed(1)} m/s</td>
      <td class="stamp">R=${l.consistency_R.toFixed(2)}, n=${l.n_vectors}, ${T("model_conf")}: ${esc(l.confidence || "?")}</td></tr>`).join("");
  const pt = MODEL.plume_top;
  const kind = (MODEL.layers.find((l) => l.trajectory_kind) || {}).trajectory_kind || "steady-wind";
  box.innerHTML = `
    <div class="kv"><span class="k">${T("model_approved")}</span>
      <span class="v"><b>${esc(MODEL.approved_by)}</b> — ${fmtWib(MODEL.approved_utc)} (${relWib(MODEL.approved_utc)})</span></div>
    <div class="kv"><span class="k">${T("model_computed")}</span><span class="v">${fmtWib(MODEL.computed_utc)}</span></div>
    <div class="kv"><span class="k">${T("model_valid")}</span><span class="v stamp">
      hard_failures=${MODEL.validation.hard_failures} · agreement=${esc(MODEL.validation.direction_agreement)} ·
      occurrence=[${(MODEL.validation.occurrence_sources || []).map(esc).join(", ")}]</span></div>
    ${pt ? `<div class="callout"><b>${T("model_plume_top")}:</b>
      ${esc(LANG === "id" ? pt.human_id : pt.human_en)} — ${esc(pt.source)}</div>`
      : `<p class="stamp">${T("model_no_top")}</p>`}
    <div class="stamp" style="margin:10px 0 2px">${T("model_layers")}</div>
    <table><thead><tr><th>${T("layer")}</th><th>${T("height")}</th><th>${T("motion")}</th><th></th></tr></thead>
    <tbody>${rows}</tbody></table>
    <p class="stamp" style="margin-top:10px">${T("model_traj_kind").replace("{kind}", esc(kind))} · ${T("abbr_note")} · ${T("star_note")}</p>
`;
  $("#model-on-map").checked = !!MAP.on.model;
}

/* ------------------------------------------------------- himawari loop ---- */
const LOOP = { frames: [], imgs: [], idx: 0, timer: null, fps: 2, playing: false, key: "", wired: false };
const LOOP_HTML = `
  <div class="player">
    <img id="loop-img" alt="Himawari-9 infrared frame" draggable="false">
    <svg id="loop-overlay" aria-hidden="true"></svg>
    <div class="loop-ts" id="loop-ts">—</div>
  </div>
  <div class="loop-ctl">
    <button class="btn" id="loop-play" aria-label="play/pause"><svg class="ic"><use href="#i-play"/></svg></button>
    <button class="btn" id="loop-speed" aria-label="speed">2 fps</button>
    <input type="range" id="loop-scrub" min="0" max="0" value="0" step="1" aria-label="frame">
    <span class="stamp" id="loop-count">0/0</span>
  </div>
  <div class="loop-ticks" id="loop-ticks" aria-hidden="true"></div>
  <p class="figcap" id="loop-cap"></p>
  <p class="stamp" id="loop-eruptions-note"></p>`;

const mx = (lon) => (lon + 180) / 360;
const my = (lat) => { const r = (lat * Math.PI) / 180; return (1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2; };

function loopOverlay() {
  const svg = $("#loop-overlay"), img = $("#loop-img");
  const L = SNAP.loop; if (!svg || !img || !L || !img.naturalWidth) return;
  const W = img.naturalWidth, H = img.naturalHeight;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  const [lo0, lo1, la0, la1] = L.roi;
  const X = (lon) => ((mx(lon) - mx(lo0)) / (mx(lo1) - mx(lo0))) * W;
  const Y = (lat) => ((my(la1) - my(lat)) / (my(la1) - my(la0))) * H;
  const P = [];
  (window.COAST || []).forEach((ring) => {
    let d = "", pen = false;
    ring.forEach(([lon, lat]) => {
      const inside = lon >= lo0 - 1 && lon <= lo1 + 1 && lat >= la0 - 1 && lat <= la1 + 1;
      if (inside) { d += (pen ? "L" : "M") + X(lon).toFixed(1) + " " + Y(lat).toFixed(1); pen = true; }
      else pen = false;
    });
    if (d) P.push(`<path d="${d}" fill="none" stroke="rgba(255,255,255,.6)" stroke-width="1.1"/>`);
  });
  const vx = X(105.423), vy = Y(-6.102);
  P.push(`<circle cx="${vx}" cy="${vy}" r="7" fill="none" stroke="#ff5544" stroke-width="1.4" opacity=".9"/>`);
  P.push(`<circle cx="${vx}" cy="${vy}" r="2.6" fill="#ff5544"/>`);
  P.push(`<text x="${vx + 10}" y="${vy + 4}" font-size="11" font-weight="700" fill="#fff" opacity=".92">Anak Krakatau</text>`);
  svg.innerHTML = P.join("");
}

function loopVerifyUrl(f) {
  const Lp = SNAP && SNAP.loop;
  if (!Lp || !Lp.verified) return null;
  const z = Lp.zoom, [lo0, lo1, la0, la1] = Lp.roi;
  const clon = (lo0 + lo1) / 2, clat = (la0 + la1) / 2;
  const n = 2 ** z;
  const col = Math.floor(((clon + 180) / 360) * n);
  const r = (clat * Math.PI) / 180;
  const row = Math.floor(((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2) * n);
  return `https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/${Lp.layer}/default/${f.t_utc}/GoogleMapsCompatible_Level${Lp.tms}/${z}/${row}/${col}.png`;
}

function loopShow() {
  const f = LOOP.frames[LOOP.idx]; if (!f) return;
  $("#loop-img").src = f.asset;
  $("#loop-ts").textContent = f.t_wib || fmtWib(f.t_utc);
  $("#loop-scrub").value = LOOP.idx;
  $("#loop-count").textContent = `${LOOP.idx + 1}/${LOOP.frames.length}`;
  loopOverlay();
}
function loopSetPlaying(on) {
  LOOP.playing = on;
  clearInterval(LOOP.timer);
  if (on) LOOP.timer = setInterval(() => { LOOP.idx = (LOOP.idx + 1) % LOOP.frames.length; loopShow(); }, 1000 / LOOP.fps);
  $("#loop-play").innerHTML = `<svg class="ic"><use href="#${on ? "i-pause" : "i-play"}"/></svg>`;
}
function loopTicks() {
  const L = SNAP.loop, box = $("#loop-ticks"); if (!L || !box) return;
  const t0 = new Date(L.frames[0].t_utc).getTime(), t1 = new Date(L.frames[L.frames.length - 1].t_utc).getTime();
  const evs = (SNAP.eruptions || []).filter((e) => e.utc && +new Date(e.utc) >= t0 && +new Date(e.utc) <= t1);
  box.innerHTML = evs.map((e) => {
    const p = ((+new Date(e.utc) - t0) / (t1 - t0)) * 100;
    const d = new Date(+new Date(e.utc) + 7 * 3600e3);
    return `<i style="left:${p.toFixed(1)}%" data-l="${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")}"></i>`;
  }).join("");
  $("#loop-eruptions-note").textContent = evs.length ? T("loop_eruptions") : "";
}
function renderLoop() {
  const card = $("#card-loop"), L = SNAP && SNAP.loop;
  if (!L || !(L.frames || []).length || L.frames.length < 4) {
    card.innerHTML = `<p class="stamp">${T("loop_none")}</p>`; LOOP.key = ""; return;
  }
  const key = L.frames[0].t_utc + ":" + L.frames.length;
  if (!LOOP.wired || key !== LOOP.key) {
    card.innerHTML = LOOP_HTML;
    LOOP.key = key; LOOP.frames = L.frames; LOOP.idx = L.frames.length - 1;
    LOOP.imgs = L.frames.map((f) => { const im = new Image(); im.src = f.asset; return im; });
    $("#loop-scrub").max = L.frames.length - 1;
    const vf = L.verified || {};
    const mark = (v) => (v === true ? "✓" : v === false ? "✗" : "?");
    let vs = T("loop_verified").replace("{grid}", mark(vf.ten_minute_grid))
      .replace("{noaa}", mark(vf.noaa_s3_slot_exists)).replace("{slot}", vf.noaa_slot || "");
    $("#loop-cap").textContent = (L.band_label || "IR 10.4 µm") + " · 10 min/frame · " +
      T("loop_cap") + " · " + L.credit + " · " + T("loop_latency") + " " + vs;
    const wire = () => {
      $("#loop-play").onclick = () => loopSetPlaying(!LOOP.playing);
      $("#loop-speed").onclick = () => { LOOP.fps = LOOP.fps === 1 ? 2 : LOOP.fps === 2 ? 4 : 1; $("#loop-speed").textContent = LOOP.fps + " fps"; if (LOOP.playing) loopSetPlaying(true); };
      $("#loop-scrub").oninput = (e) => { LOOP.idx = +e.target.value; loopSetPlaying(false); loopShow(); };
      $("#loop-img").onload = loopOverlay;
      const ts = $("#loop-ts");
      if (!ts) return;
      ts.title = T("loop_verify_hint");
      ts.onclick = () => {
        const f = LOOP.frames[LOOP.idx];
        const u = loopVerifyUrl(f) || (SNAP.loop.verified || {}).verify_url;
        if (u) window.open(u, "_blank");
      };
      LOOP.wired = true;
    };
    wire(); loopTicks(); loopShow(); loopSetPlaying(true);
  } else { loopTicks(); }
}

/* ---------------------------------------------------------------- map ----- */
/* Leaflet + OpenStreetMap. Replaced the hand-drawn SVG map because:
   real coastlines for free, proper mobile gestures, no text-selection-while-
   dragging bug, and a native collapsible layer control. Vectors still render
   if tiles cannot load (offline preview), just without basemap. */
const MAP = { el: null, control: null, groups: {}, on: { obs: true, f6: true, f12: false, f18: false, model: false } };
const VENT = [-6.102, 105.423];
const PCOL = { obs: "#9B2B1A", f6: "#d97706", f12: "#b45309", f18: "#78716c" };

let LEAFLET = 0;   // 0 idle, 1 loading, 2 ready, 3 failed
function loadLeaflet(cb) {
  if (typeof L !== "undefined" || LEAFLET === 2) return cb(true);
  if (LEAFLET === 3) return cb(false);
  if (LEAFLET === 1) return;              // already fetching
  LEAFLET = 1;
  const c = document.createElement("link");
  c.rel = "stylesheet";
  c.href = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css";
  document.head.appendChild(c);
  const s = document.createElement("script");
  s.src = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
  s.crossOrigin = "anonymous";
  s.onload = () => { LEAFLET = 2; cb(true); };
  s.onerror = () => { LEAFLET = 3; cb(false); };
  document.head.appendChild(s);
}

function initMap() {
  const box = $("#map");
  if (typeof L === "undefined") {
    if (LEAFLET === 0) {
      box.innerHTML = `<div class="map-fallback"><span class="loading-dots"><span></span><span></span><span></span></span></div>`;
      loadLeaflet((ok) => { if (ok) initMap(); else mapFallback(); });
      return;
    }
    if (LEAFLET === 1) return;            // script in flight; onload will retry
    return mapFallback();
  }
  const map = L.map("map", { zoomControl: true }).setView([-6.35, 105.42], 8);
  MAP.el = map;
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 12,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(map);
  L.circleMarker(VENT, { radius: 6, color: "#9B2B1A", weight: 2, fillColor: "#9B2B1A", fillOpacity: 0.9 })
    .addTo(map)
    .bindTooltip("Anak Krakatau", { permanent: true, direction: "right", offset: [9, 0], className: "vent-tip" });
  map.on("overlayadd", (e) => { const k = e.layer._krkKey; if (k) MAP.on[k] = true; });
  map.on("overlayremove", (e) => { const k = e.layer._krkKey; if (k) MAP.on[k] = false; });
  rebuildMapLayers();
}

const XY = (pt) => (Array.isArray(pt) ? [pt[1], pt[0]] : [pt.lat, pt.lon]);

function _polyGroup(key, layers, labelFn) {
  const ls = (layers || []).filter((l) => l.polygon && l.polygon.length > 2);
  if (!ls.length) return null;
  const g = L.layerGroup(ls.map((l) =>
    L.polygon(l.polygon.map((pt) => XY(pt)),
      { color: PCOL[key], weight: 1.6, fillColor: PCOL[key], fillOpacity: 0.16 })
      .bindTooltip(labelFn(l))));
  g._krkKey = key;
  return g;
}

function rebuildMapLayers() {
  const map = MAP.el;
  if (!map) return;
  if (MAP.control) { map.removeControl(MAP.control); MAP.control = null; }
  Object.values(MAP.groups).forEach((g) => map.removeLayer(g));
  MAP.groups = {};
  const V = SNAP && SNAP.vaac;
  const named = {};
  if (V && V.state === "advisory") {
    const obs = _polyGroup("obs", V.observed_layers,
      (l) => `${T("map_obs")} · ${l.base}–${l.top} · ${T("moves")} ${l.move_toward} ${l.speed_kt} kt`);
    if (obs) { MAP.groups.obs = obs; named[T("map_obs")] = obs; }
    Object.entries(V.forecasts || {}).forEach(([h, f]) => {
      const key = h === "+6h" ? "f6" : h === "+12h" ? "f12" : h === "+18h" ? "f18" : null;
      if (!key) return;
      const g = _polyGroup(key, f.layers,
        (l) => `${T("fcst_cloud")} ${h} · ${l.base}–${l.top}`);
      if (g) { MAP.groups[key] = g; named[`${T("fcst_cloud")} ${h}`] = g; }
    });
  }
  if (MODEL && MODEL.status === "approved") {
    const ls = (MODEL.layers || []).filter((l) => l.trajectory && l.trajectory.length > 1);
    if (ls.length) {
      const g = L.layerGroup(ls.map((l, i) => {
        const c = BAND_COLORS[i % 6];
        const e = l.trajectory[l.trajectory.length - 1];
        return L.layerGroup([
          L.polyline(l.trajectory.map((pt) => [pt[0], pt[1]]), { color: c, weight: 2.6, opacity: 0.9 })
            .bindTooltip(`${l.layer} → ${l.toward_compass} ${l.toward_deg.toFixed(0)}°`),
          L.circleMarker([e[0], e[1]], { radius: 4, color: c, fillColor: c, fillOpacity: 1 })
            .bindTooltip(`+${e[2]}h · ${l.layer}`),
        ]);
      }));
      g._krkKey = "model";
      MAP.groups.model = g;
      named[T("map_model")] = g;
    }
  }
  MAP.control = L.control.layers(null, named, { collapsed: true }).addTo(map);
  Object.entries(MAP.groups).forEach(([k, g]) => { if (MAP.on[k]) g.addTo(map); });
}

function mapFallback() {
  const box = $("#map");
  if (box) box.innerHTML = `<div class="map-fallback">${T("map_need_network")}</div>`;
}

function ensureMap() {
  if (MAP.el) rebuildMapLayers(); else initMap();
  const note = $("#map-note");
  if (note) note.textContent = T("map_note");
}

/* ------------------------------------------------------------ lightbox ---- */
const GALLERY = { items: [], idx: 0 };
function galleryCollect() {
  GALLERY.items = [...document.querySelectorAll("img.pic")].map((im) => ({
    src: im.src,
    cap: ((im.closest(".card") || {}).querySelector ?
      (im.closest(".card").querySelector(".figcap") || im.closest(".card").querySelector(".stamp") || {}).textContent : "") || im.alt || "",
  }));
}
function galleryShow(i) {
  GALLERY.idx = (i + GALLERY.items.length) % GALLERY.items.length;
  const it = GALLERY.items[GALLERY.idx];
  let lb = $("#lightbox");
  if (!lb) {
    lb = document.createElement("div");
    lb.id = "lightbox";
    lb.innerHTML = `<button class="lb-x" aria-label="close">&times;</button>
      <button class="lb-p" aria-label="prev">&#8249;</button>
      <img alt="">
      <button class="lb-n" aria-label="next">&#8250;</button>
      <div class="lb-cap"></div>`;
    document.body.appendChild(lb);
    const closeLb = () => { lb.classList.remove("on"); document.body.style.overflow = ""; };
    lb.addEventListener("click", (e) => {
      if (e.target === lb || e.target.classList.contains("lb-x")) closeLb();
      if (e.target.classList.contains("lb-p")) galleryShow(GALLERY.idx - 1);
      if (e.target.classList.contains("lb-n")) galleryShow(GALLERY.idx + 1);
    });
    document.addEventListener("keydown", (e) => {
      if (!lb.classList.contains("on")) return;
      if (e.key === "Escape") { lb.classList.remove("on"); document.body.style.overflow = ""; }
      if (e.key === "ArrowLeft") galleryShow(GALLERY.idx - 1);
      if (e.key === "ArrowRight") galleryShow(GALLERY.idx + 1);
    });
  }
  lb.querySelector("img").src = it.src;
  lb.querySelector(".lb-cap").textContent =
    `${it.cap}  (${GALLERY.idx + 1} ${T("gal_of")} ${GALLERY.items.length})`;
  lb.classList.add("on");
  document.body.style.overflow = "hidden";   // scroll lock while viewing
}
document.addEventListener("click", (e) => {
  const im = e.target.closest ? e.target.closest("img.pic") : null;
  if (im) { e.preventDefault(); galleryCollect(); galleryShow(GALLERY.items.findIndex((x) => x.src === im.src)); }
});

/* ------------------------------------------------------------ boot -------- */
function stamp() {
  if (!SNAP) return;
  const ageH = (Date.now() - new Date(SNAP.generated_utc).getTime()) / 3600e3;
  $("#chip-updated-txt").textContent = `${T("fetched")} ${relWib(SNAP.generated_utc)}`;
  $("#chip-updated-txt").title = `${T("fetched")}: ${fmtWib(SNAP.generated_utc)}`;
  const dot = $("#upd-dot");
  if (dot) dot.classList.toggle("stale", ageH > 1.5);
  $("#foot-stamp").textContent = `${T("updated")}: ${fmtWib(SNAP.generated_utc)} · schema v${SNAP.schema_version} · WIB = UTC+7`;
}

function safeRender(name, fn) {
  try {
    fn();
  } catch (e) {
    const card = document.querySelector("#card-" + name) || document.querySelector("#" + name);
    if (card) card.innerHTML = `<p class="stamp">⚠ ${esc(name)}: ${esc(String((e && e.message) || e))}</p>`;
    if (typeof console !== "undefined") console.error("render failed:", name, e);
  }
}

function renderAll() {
  applyI18n();
  safeRender("status", renderStatus);
  safeRender("report", renderReport);
  safeRender("eruptions", renderEruptions);
  safeRender("vona", renderVona);
  safeRender("vaac", renderVaac);
  safeRender("loop", renderLoop);
  safeRender("sat", renderSat);
  safeRender("model", renderModel);
  safeRender("map", ensureMap);
  safeRender("stamp", stamp);
}

function inlineJSON(id) {
  const el = document.getElementById(id);
  if (!el || !el.textContent.trim()) return null;
  try { return JSON.parse(el.textContent); } catch (e) { return null; }
}

async function loadAll() {
  const bootSnap = inlineJSON("boot-snapshot");
  const bootModel = inlineJSON("boot-model");
  SNAP = await loadJSON("data/snapshot.json").catch(() => bootSnap);
  if (!SNAP) throw new Error("no snapshot available");
  MODEL = await loadJSON("data/forecast_model.json")
    .catch(() => (bootModel && bootModel.status === "approved" ? bootModel : null));
  renderAll();
}

$("#btn-lang").onclick = () => { LANG = LANG === "id" ? "en" : "id"; localStorage.setItem("krak-lang", LANG); renderAll(); };
$("#btn-theme").onclick = () => {
  const d = document.documentElement.dataset.theme === "dark";
  document.documentElement.dataset.theme = d ? "light" : "dark";
  localStorage.setItem("krak-theme", d ? "light" : "dark");
  $("#btn-theme").setAttribute("aria-pressed", String(!d));
};

document.documentElement.dataset.theme = localStorage.getItem("krak-theme") || "light";

loadAll().catch((e) => {
  document.querySelectorAll(".card").forEach((c) => {
    c.innerHTML = `<p class="stamp">data/snapshot.json: ${esc(String(e))}</p>`;
  });
});
setInterval(() => { loadAll().catch(() => {}); }, 300000);   // auto-refresh 5 min
document.addEventListener("visibilitychange", () => { if (!document.hidden) loadAll().catch(() => {}); });
