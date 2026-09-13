/* ============================================================================
   app.js — volcano community dashboard (default: Anak Krakatau).
   No build step, no framework: plain JS so a non-developer can edit safely.
   Reads only local JSON produced by build_site.py (the site never scrapes).
   Multi-volcano: boots from data/volcanoes.json, reads data/<slug>/…, and
   ?volcano=<slug> in the URL picks another registry entry.
   ==========================================================================*/
"use strict";

/* ---------------------------------------------------------------- i18n ---- */
const I18N = {
  id: {
    title: "Pantau {volcano}",
    subtitle: "Dasbor komunitas—data resmi + model sekunder",
    sec_status: "Status Aktivitas",
    sec_report: "Laporan Pengamatan Terakhir",
    sec_eruptions: "Kejadian Erupsi",
    sec_vona: "VONA (Penerbangan)",
    sec_vaac: "Advisori Abu Vulkanik—Darwin VAAC",
    sec_sat: "Citra Satelit Harian",
    sec_model: "Model Arah Abu (Sekunder)",
    src_model: "Himawari-9 + open-meteo—bukan data resmi",
    src_magma: 'Sumber: <a href="https://magma.esdm.go.id" target="_blank" rel="noopener">MAGMA Indonesia/PVMBG</a>',
    src_magma2: 'Sumber: <a href="https://magma.esdm.go.id/v1/gunung-api/laporan" target="_blank" rel="noopener">MAGMA Indonesia/PVMBG</a>',
    src_vaac: 'Sumber: <a href="https://www.bom.gov.au/aviation/volcanic-ash/darwin-va-advisory.shtml" target="_blank" rel="noopener">Bureau of Meteorology (Australia), ICAO VAAC</a>',
    src_firms: 'Sumber: <a href="https://firms.modaps.eosdis.nasa.gov" target="_blank" rel="noopener">NASA FIRMS/GIBS</a> (Suomi NPP VIIRS &amp; Aqua MODIS)',
    foot_official: "Sumber resmi",
    foot_disclaim_t: "Penyangkalan",
    foot_disclaim: 'Situs komunitas <b>tidak resmi</b>. Bukan sistem peringatan dini. Selalu ikuti arahan PVMBG, BNPB/BPBD, dan otoritas penerbangan. Data © lembaga masing-masing.',
    foot_agents: "Untuk mesin/AI agents",
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
    vaac_nil_body: "Darwin VAAC tidak menerbitkan advisori saat ini. <b>Ini BUKAN berarti tidak ada bahaya</b>—advisori hanya terbit bila abu teridentifikasi dan relevan bagi penerbangan. Untuk status erupsi, lihat MAGMA/PVMBG di atas.",
    vaac_stale: "Advisori untuk gunung lain aktif, tetapi tidak ada untuk {volcano}. Ketidakadaan advisori bukan berarti aman.",
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
    map_tracks: "Jalur model sekunder",
    map_envs: "Selubung per lapisan",
    map_union: "Selubung gabungan (di bawah puncak resmi)",
    map_union_all: "Selubung semua lapisan (worst-case)",
    map_tracks_upper: "Jalur di atas puncak resmi (worst-case)",
    map_env: "selubung abu terdeteksi (mengikuti sisa massa abu di udara)",
    map_env_w: "lebar maks {a} km → {b} km di +12 jam",
    map_env_none: "< 0,5 km—di bawah ambang deteksi, menyatu dengan garis jalur",
    map_union_hull: "batas luar worst-case (hull semua pita): {a} km²",
    emission_line: "Umur awan {a} jam—diturunkan dari lebar polygon OBS Darwin ({w} km); sebaran awal & massa airborne mengikuti riwayat emisi.",
    emission_capped: "Catatan: umur awan dicap pada batas model 48 jam—lebar polygon OBS ({w} km) melebihi yang bisa dijelaskan model; baca lebar selubung dengan hati-hati.",
    map_note: "Peta skematik—garis pantai Natural Earth. BUKAN untuk navigasi.",
    sat_none: "Citra harian belum tersedia.",
    sec_loop: "Animasi Himawari-9 (Inframerah)",
    src_loop: 'Sumber: <a href="https://worldview.earthdata.nasa.gov" target="_blank" rel="noopener">NASA GIBS</a>/JMA Himawari-9 AHI Band 13',
    loop_none: "Animasi belum tersedia (butuh ≥4 frame). Akan muncul pada build berikutnya.",
    loop_cap: "Putar untuk melihat pergerakan awan/abu. Putih = puncak awan dingin/tinggi; gelap = permukaan hangat. Garis pantai tipis + titik merah = {volcano}.",
    loop_eruptions: "Tanda merah pada garis waktu = waktu erupsi menurut MAGMA/PVMBG.",
    model_plume_top: "Puncak awan abu resmi hari ini",
    model_no_top: "Tidak ada puncak awan abu resmi hari ini—semua lapisan ditampilkan setara.",
    model_relevant: "paling relevan hari ini",
    model_traj_kind: "Garis pergerakan memakai angin prakiraan yang berubah per jam ({kind}); varian angin-tetap tersedia di forecast_model.json.",
    verbatim_note: "Seluruh teks dari lembaga resmi (PVMBG/MAGMA, VONA, Darwin VAAC) ditampilkan apa adanya, tanpa suntingan—termasuk bila sumber mengandung pengulangan kalimat.",
    abbr_note: "dpl = di atas permukaan laut · ft = kaki · km = kilometer",
    star_note: "★ = lapisan paling relevan hari ini (berdasar puncak awan abu resmi)",
    backtest_line: "Pemeriksaan ulang historis vs Darwin VAAC: rerata selisih sudut {m}° (n={n}).",
    caveats_title: "Catatan kejujuran:",
    no_data: "tidak ada data",
    no_data_note: 'Baris "tidak ada data": Himawari-9 tidak memiliki nilai vektor angin di lapisan tersebut saat data diambil; garis jalur di peta murni angin dari data Open-Meteo.',
    auto_note: "Model ini dipublikasikan OTOMATIS oleh jadwal 6-jam (jendela aktivitas menurun); tetap bawa catatan kejujuran di atas.",
    src_err_banner: "Sebagian sumber resmi tidak terjangkau saat pembaruan terakhir ({list}). Bagian terkait menampilkan data terakhir yang berhasil diambil—KESENJANGAN INI BUKAN berarti aktivitas menurun.",
    magma_unreachable: "MAGMA/PVMBG tidak terjangkau saat pembaruan terakhir; kartu ini menampilkan data terakhir yang berhasil diambil.",
    vaac_unreachable: "Darwin VAAC tidak terjangkau saat pembaruan terakhir; periksa langsung bom.gov.au untuk advisori terkini.",
    loop_latency: "Frame tertinggal ±20–60 menit dari waktu nyata karena pemrosesan NASA—wajar, bukan kesalahan data.",
    loop_verified: "Waktu frame terverifikasi: grid citra 10-menit Himawari {grid} · slot citra ada di NOAA S3 {noaa} ({slot}).",
    loop_verify_hint: "Klik untuk membuka tile sumber NASA frame ini (verifikasi mandiri)",
    map_need_network: "Peta interaktif butuh koneksi internet (tile © OpenStreetMap). Data poligon tetap dapat dibaca mesin dari berkas JSON di folder data/.",
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
    disclosure: '⚠️ <b>Model ini dihitung OTOMATIS</b> dari angin satelit Himawari-9 dan prakiraan open-meteo—<b>bisa tidak akurat</b>. Ini BUKAN keluaran PVMBG, BNPB, maupun Darwin VAAC. ketinggian dan arah abu dapat berubah cepat. Untuk keputusan apa pun, gunakan rilis resmi: <a href="https://magma.esdm.go.id" target="_blank" rel="noopener">MAGMA/PVMBG</a>, <a href="https://www.bnpb.go.id" target="_blank" rel="noopener">BNPB/BPBD</a>, <a href="https://www.bom.gov.au/aviation/volcanic-ash/darwin-va-advisory.shtml" target="_blank" rel="noopener">Darwin VAAC</a>.',
    hours_ago: (n) => `${n} jam lalu`,
    min_ago: (n) => `${n} menit lalu`,
    days_ago: (n) => `${n} hari lalu`,
    just_now: "baru saja",
    /* ---- a11y additions (skip link, live region, SR reading aids) ---- */
    skip_main: "Lewati ke konten utama",
    live_loaded: "Dasbor dimuat: status {level}; {erup} kejadian erupsi; {vona} VONA; advisori VAAC {vaac}; data {wib}.",
    live_no_adv: "tidak ada advisori aktif",
    live_partial: "Pembaruan sebagian—{list} tidak terjangkau saat pembaruan terakhir; bagian terkait menampilkan data terakhir yang berhasil diambil.",
    alt_seismo: "Seismogram PVMBG untuk periode laporan ini—nilai numeriknya tersedia di tabel kegempaan di sebelahnya.",
    alt_vaac_graphic: "Grafik advisori Darwin VAAC—peta poligon awan abu teramati dan prakiraan.",
    ext_link: "buka laporan asli di MAGMA",
    vaac_codes_t: "Kode advisori (cara membaca)",
    vaac_codes: '<dl class="codes"><dt>VA</dt><dd>volcanic ash—abu vulkanik</dd><dt>OBS</dt><dd>observed—teramati (OBS VA DTG = saat awan abu teramati)</dd><dt>DTG</dt><dd>date/time group—waktu advisori diterbitkan (UTC)</dd><dt>FCST</dt><dd>forecast—prakiraan</dd><dt>SFC</dt><dd>surface—permukaan tanah</dd><dt>FL050</dt><dd>flight level 050—ketinggian penerbangan 5.000 kaki ≈ 1,5 km</dd><dt>MOV NW 10KT</dt><dd>bergerak ke barat laut dengan kecepatan 10 knot</dd><dt>RMK</dt><dd>remarks—catatan</dd><dt>NXT ADV</dt><dd>advisori berikutnya</dd><dt>11/0820Z</dt><dd>tanggal 11, pukul 08:20 UTC (Z = UTC)</dd></dl>',
    vaac_codes_ex: 'Contoh: “VA TO FL050 OBS AT 11/0820Z MOV SW” = abu vulkanik hingga FL050 (5.000 kaki ≈ 1,5 km), teramati 11 September pukul 08:20 UTC, bergerak ke barat daya.',
    /* ---- quiet / normal state (activity_state, pelajaran 2026-09-12) ---- */
    quiet_t: "Aktivitas normal",
    quiet_banner: "Tidak ada episode abu vulkanik yang berlangsung—{why} Tingkat aktivitas resmi di bawah tetap ditampilkan apa adanya.",
    quiet_since: "kondisi normal sejak {when}",
    vaac_term_t: "Episode dihentikan",
    vaac_term_b: "Buletin ini menutup episode: VAAC menyatakan <b>ADVISORY TERMINATED</b>—abu tidak lagi teridentifikasi dan tidak ada laporan erupsi berlangsung. Situs kembali ke mode normal; advisori atau VONA baru akan memulai pemantauan ulang.",
    model_paused_t: "Model dihentikan sementara—kondisi normal",
    model_paused_b: "Tidak ada episode abu yang berlangsung, sehingga model sekunder tidak dihitung dan jadwal 6-jamnya dihentikan sementara. Tabel di bawah adalah ARSIP perhitungan terakhir saat episode masih berlangsung ({when}).",
    model_archive_badge: "ARSIP",
    model_plume_top_arch: "Puncak awan abu resmi saat episode terakhir",
    live_quiet: "Kondisi normal—tidak ada episode abu vulkanik yang berlangsung.",
  },
  en: {
    title: "{volcano} Watch",
    subtitle: "Community dashboard—official data + secondary model",
    sec_status: "Activity Status",
    sec_report: "Latest Observation Report",
    sec_eruptions: "Eruption Events",
    sec_vona: "VONA (Aviation)",
    sec_vaac: "Volcanic Ash Advisory—Darwin VAAC",
    sec_sat: "Daily Satellite Imagery",
    sec_model: "Ash Direction Model (Secondary)",
    src_model: "Himawari-9 + open-meteo—not official data",
    src_magma: 'Source: <a href="https://magma.esdm.go.id" target="_blank" rel="noopener">MAGMA Indonesia/PVMBG</a>',
    src_magma2: 'Source: <a href="https://magma.esdm.go.id/v1/gunung-api/laporan" target="_blank" rel="noopener">MAGMA Indonesia/PVMBG</a>',
    src_vaac: 'Source: <a href="https://www.bom.gov.au/aviation/volcanic-ash/darwin-va-advisory.shtml" target="_blank" rel="noopener">Bureau of Meteorology (Australia), ICAO VAAC</a>',
    src_firms: 'Source: <a href="https://firms.modaps.eosdis.nasa.gov" target="_blank" rel="noopener">NASA FIRMS/GIBS</a> (Suomi NPP VIIRS &amp; Aqua MODIS)',
    foot_official: "Official sources",
    foot_disclaim_t: "Disclaimer",
    foot_disclaim: 'An <b>unofficial</b> community site. Not an early-warning system. Always follow PVMBG, BNPB/BPBD and aviation authority guidance. Data © respective agencies.',
    foot_agents: "For machines/AI agents",
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
    vaac_nil_body: "Darwin VAAC has no current advisory. <b>This does NOT mean no hazard</b>—advisories are issued only when ash is identifiable and relevant to aviation. For eruption status see MAGMA/PVMBG above.",
    vaac_stale: "Advisories are active for other volcanoes but none for {volcano}. Absence of an advisory is not an all-clear.",
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
    map_tracks: "Secondary model tracks",
    map_envs: "Per-layer envelopes",
    map_union: "Combined envelope (below the official top)",
    map_union_all: "All-layers envelope (worst case)",
    map_tracks_upper: "Tracks above the official top (worst case)",
    map_env: "detectable-ash envelope (mass-coupled)",
    map_env_w: "max width {a} km → {b} km at +12 h",
    map_env_none: "< 0.5 km—below the detection threshold, merged with the track line",
    map_union_hull: "worst-case outer bound (hull of all bands): {a} km²",
    emission_line: "Cloud age {a} h—derived from the Darwin OBS polygon width ({w} km); the initial spread and airborne mass follow the emission history.",
    emission_capped: "Note: cloud age capped at the model's 48 h limit—the OBS polygon width ({w} km) exceeds what the model can explain; read envelope widths with care.",
    map_note: "Schematic map—Natural Earth coastlines. NOT for navigation.",
    sat_none: "Daily imagery not available yet.",
    sec_loop: "Himawari-9 Animation (Infrared)",
    src_loop: 'Source: <a href="https://worldview.earthdata.nasa.gov" target="_blank" rel="noopener">NASA GIBS</a>/JMA Himawari-9 AHI Band 13',
    loop_none: "Animation not available yet (needs ≥4 frames). It will appear on the next build.",
    loop_cap: "Press play to watch cloud/ash motion. White = cold/high cloud tops; dark = warm surface. Thin coastline + red dot = {volcano}.",
    loop_eruptions: "Red marks on the timeline = eruption times per MAGMA/PVMBG.",
    model_plume_top: "Official ash-cloud top today",
    model_no_top: "No official ash-cloud top today—all layers shown equally.",
    model_relevant: "most relevant today",
    model_traj_kind: "Trajectories use hourly-evolving forecast wind ({kind}); a steady-wind variant ships in forecast_model.json.",
    verbatim_note: "All text from official agencies (PVMBG/MAGMA, VONA, Darwin VAAC) is shown verbatim, unedited—including where the source itself repeats a sentence.",
    abbr_note: "asl = above sea level · ft = feet · km = kilometres",
    star_note: "★ = most relevant layer today (based on the official ash-cloud top)",
    backtest_line: "Historical cross-check vs Darwin VAAC: mean angular difference {m}° (n={n}).",
    caveats_title: "Honesty notes:",
    no_data: "no data",
    no_data_note: '"no data" rows: Himawari-9 had no wind-vector values for that layer at acquisition time; the track line on the map is Open-Meteo winds only.',
    auto_note: "This model was published AUTOMATICALLY by the 6-hourly schedule (decreasing-activity window); it still carries the honesty notes above.",
    src_err_banner: "Some official sources were unreachable at the last rebuild ({list}). Affected sections show the last successfully fetched data—THIS GAP DOES NOT mean activity has decreased.",
    magma_unreachable: "MAGMA/PVMBG was unreachable at the last rebuild; this card shows the last successfully fetched data.",
    vaac_unreachable: "Darwin VAAC was unreachable at the last rebuild; check bom.gov.au directly for the current advisory.",
    loop_latency: "Frames lag real time by ±20–60 min due to NASA processing—expected, not a data error.",
    loop_verified: "Frame times verified: Himawari 10-min imaging grid {grid} · imaging slot present on NOAA S3 {noaa} ({slot}).",
    loop_verify_hint: "Click to open NASA's source tile for this frame (self-verification)",
    map_need_network: "Map library failed to load: site/vendor/leaflet.js is missing and backup CDNs are unreachable. Check the site/vendor/ upload - the rest of the dashboard works normally.",
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
    disclosure: '⚠️ <b>This model is computed AUTOMATICALLY</b> from Himawari-9 satellite winds and open-meteo forecasts—<b>it can be inaccurate</b>. It is NOT output from PVMBG, BNPB or Darwin VAAC. Ash height and direction can change quickly. For any decision use official releases: <a href="https://magma.esdm.go.id" target="_blank" rel="noopener">MAGMA/PVMBG</a>, <a href="https://www.bnpb.go.id" target="_blank" rel="noopener">BNPB/BPBD</a>, <a href="https://www.bom.gov.au/aviation/volcanic-ash/darwin-va-advisory.shtml" target="_blank" rel="noopener">Darwin VAAC</a>.',
    hours_ago: (n) => `${n} h ago`,
    min_ago: (n) => `${n} min ago`,
    days_ago: (n) => `${n} d ago`,
    just_now: "just now",
    /* ---- a11y additions (skip link, live region, SR reading aids) ---- */
    skip_main: "Skip to main content",
    live_loaded: "Dashboard loaded: status {level}; {erup} eruption events; {vona} VONA; VAAC advisory {vaac}; data {wib}.",
    live_no_adv: "no active advisory",
    live_partial: "Partial update—{list} unreachable at the last rebuild; affected sections show the last successfully fetched data.",
    alt_seismo: "PVMBG seismogram for this reporting period—its numeric values are in the seismicity table alongside.",
    alt_vaac_graphic: "Darwin VAAC advisory chart—map of observed and forecast ash-cloud polygons.",
    ext_link: "open the original report on MAGMA",
    vaac_codes_t: "Advisory codes (how to read)",
    vaac_codes: '<dl class="codes"><dt>VA</dt><dd>volcanic ash</dd><dt>OBS</dt><dd>observed (OBS VA DTG = when the ash cloud was observed)</dd><dt>DTG</dt><dd>date/time group—issue time (UTC)</dd><dt>FCST</dt><dd>forecast</dd><dt>SFC</dt><dd>surface (ground level)</dd><dt>FL050</dt><dd>flight level 050—5,000 ft ≈ 1.5 km</dd><dt>MOV NW 10KT</dt><dd>moving northwest at 10 knots</dd><dt>RMK</dt><dd>remarks</dd><dt>NXT ADV</dt><dd>next advisory</dd><dt>11/0820Z</dt><dd>day 11, 08:20 UTC (Z = UTC)</dd></dl>',
    vaac_codes_ex: 'Example: “VA TO FL050 OBS AT 11/0820Z MOV SW” = volcanic ash up to FL050 (5,000 ft ≈ 1.5 km), last observed 11 September at 08:20 UTC, moving southwest.',
    /* ---- quiet / normal state (activity_state, 2026-09-12 lesson) ---- */
    quiet_t: "Normal activity",
    quiet_banner: "No volcanic ash episode in progress—{why} The official alert level below is still shown exactly as issued.",
    quiet_since: "normal conditions since {when}",
    vaac_term_t: "Episode terminated",
    vaac_term_b: "This bulletin closes the episode: the VAAC declares <b>ADVISORY TERMINATED</b>—ash is no longer identifiable and no ongoing eruption is reported. The site is back in normal mode; a new advisory or VONA will restart monitoring.",
    model_paused_t: "Model paused—normal conditions",
    model_paused_b: "No ash episode in progress, so the secondary model is not computed and its 6-hourly schedule is paused. The table below is the ARCHIVE of the last computation during the episode ({when}).",
    model_archive_badge: "ARCHIVE",
    model_plume_top_arch: "Official ash-cloud top during the last episode",
    live_quiet: "Normal conditions—no volcanic ash episode in progress.",
  },
};

let LANG = localStorage.getItem("krak-lang") ||
           ((navigator.language || "id").toLowerCase().startsWith("id") ? "id" : "en");

/* Active volcano: bootstrapped from data/volcanoes.json (written by
   build_site.py from src/volcanoes.py) before the first render; the default
   matches the registry's primary entry so a page cached before that file
   existed degrades to the previous behaviour. */
let VOLC = { slug: "anak-krakatau", name: "Anak Krakatau", lat: -6.102, lon: 105.423 };
let DATA = "data/anak-krakatau";

const T = (k) => {
  const v = I18N[LANG][k] !== undefined ? I18N[LANG][k] : I18N.id[k] !== undefined ? I18N.id[k] : k;
  return typeof v === "string" ? v.replace(/\{volcano\}/g, VOLC.name) : v;
};

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
  const lt = $("#btn-lang-txt");
  if (lt) lt.textContent = LANG === "id" ? "EN" : "ID";
  document.title = LANG === "id"
    ? `Pantau Gunung ${VOLC.name}—Dasbor Komunitas`
    : `${VOLC.name} Watch—Community Dashboard`;
}

function renderStatus() {
  const s = SNAP.status, v = SNAP.volcano;
  if (SNAP.error || s.level == null) {
    $("#card-status").innerHTML = `<div class="callout warn">${T("magma_unreachable")}</div>
      <p class="stamp">${esc(SNAP.error || s.error || "")}</p>`;
    return;
  }
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
    .map(([k, n]) => `<tr><th scope="row">${esc(k)}</th><td class="num"><b>${n}</b></td></tr>`).join("");
  /* lang="id": MAGMA report values are verbatim Indonesian even when the
     UI is English — marking them lets screen readers switch voice. */
  $("#card-report").innerHTML = `
    <p class="stamp" style="margin:0 0 8px">${T("verbatim_note")}</p>
    <div class="kv"><span class="k">${T("period")}</span><span class="v" lang="id"><b>${esc(r.period)}</b></span></div>
    <div class="kv"><span class="k">${T("observer")}</span><span class="v" lang="id">${esc(r.author || "—")}</span></div>
    <div class="kv"><span class="k">${T("visual")}</span><span class="v" lang="id">${esc(r.visual || "—")}</span></div>
    <div class="kv"><span class="k">${T("weather")}</span><span class="v" lang="id">${esc(r.climate || "—")}</span></div>
    <div class="grid2" style="margin-top:10px">
      <div><div class="stamp" style="margin-bottom:4px">${T("seismic")}</div>
        <table><thead><tr><th></th><th scope="col" style="text-align:right">${T("seismic_n")}</th></tr></thead>
        <tbody>${rows || "<tr><td colspan=2>—</td></tr>"}</tbody></table></div>
      <div>${r.seismogram_asset ? `<div class="stamp" style="margin-bottom:4px">${T("seismogram")}</div>
        <img class="pic" src="${esc(r.seismogram_asset)}" alt="${esc(T("alt_seismo"))}" loading="lazy">` : ""}</div>
    </div>
    ${r.recommendation ? `<div class="callout warn"><b>${T("recommendation")}:</b> <span lang="id">${esc(r.recommendation)}</span></div>` : ""}`;
}

function renderEruptions() {
  const list = SNAP.eruptions || [];
  /* Icon-only links carry their name in aria-label (the SVG glyph alone
     reads as an unnamed "link"); eruption text is verbatim Indonesian. */
  $("#card-eruptions").innerHTML = list.length ? `<ul class="feed">${list.map((e) => `
    <li><span class="t">${esc(e.wib || fmtWib(e.utc))} ${e.utc ? `<span class="stamp">(${relWib(e.utc)})</span>` : ""}</span>
    ${e.text ? `<span lang="id">${esc(e.text)}</span>` : ""}${e.url ? ` <a href="${esc(e.url)}" target="_blank" rel="noopener" aria-label="${esc(T("ext_link"))}"><svg class="ic" aria-hidden="true" style="width:12px;height:12px"><use href="#i-ext"/></svg></a>` : ""}</li>`).join("")}</ul>`
    : `<p class="stamp">${T("no_eruption")}</p>`;
}

function renderVona() {
  const list = SNAP.vona || [];
  $("#card-vona").innerHTML = list.length ? `<ul class="feed">${list.map((v) => `
    <li><span class="badge sm ${esc(v.code)}">${esc(v.code)}</span>
        <span class="t" style="display:inline;margin-left:8px">${esc(v.wib || fmtWib(v.issued_utc))}</span>
      <div lang="en" style="margin-top:4px">${esc(v.text || "")}</div></li>`).join("")}</ul>`
    : `<p class="stamp">${T("no_vona")}</p>`;
}

/* Screen-reader aid for the layer tables: compass points stay visually
   ENGLISH (maintainer decision) but each cell carries a spoken expansion
   in the UI language, so "NW" is announced as "barat laut" / "northwest",
   and "SFC–FL050" as the full human-readable base–top pair. */
const COMPASS_SR = {
  id: { N: "utara", NNE: "utara–timur laut", NE: "timur laut", ENE: "timur–timur laut", E: "timur",
        ESE: "timur–tenggara", SE: "tenggara", SSE: "selatan–tenggara", S: "selatan",
        SSW: "selatan–barat daya", SW: "barat daya", WSW: "barat–barat daya", W: "barat",
        WNW: "barat–barat laut", NW: "barat laut", NNW: "utara–barat laut" },
  en: { N: "north", NNE: "north-northeast", NE: "northeast", ENE: "east-northeast", E: "east",
        ESE: "east-southeast", SE: "southeast", SSE: "south-southeast", S: "south",
        SSW: "south-southwest", SW: "southwest", WSW: "west-southwest", W: "west",
        WNW: "west-northwest", NW: "northwest", NNW: "north-northwest" },
};

function layerTable(layers) {
  if (!layers || !layers.length) return `<p class="stamp">—</p>`;
  return `<table><thead><tr><th scope="col">${T("layer")}</th><th scope="col">${T("height")}</th><th scope="col">${T("motion")}</th></tr></thead><tbody>
    ${layers.map((l) => {
      const baseH = (LANG === "id" ? l.base_human_id : l.base_human_en) || l.base || "";
      const topH = (LANG === "id" ? l.top_human_id : l.top_human_en) || l.top || "";
      const layerSr = `${baseH}–${topH}`;
      const comp = COMPASS_SR[LANG][(l.move_toward || "").trim().toUpperCase()];
      const motionTxt = `<b>${esc(l.move_toward)}</b> · ${l.speed_kt} kt ≈ ${l.speed_ms} m/s`;
      const motionSr = comp
        ? `${comp}, ${l.speed_kt} ${LANG === "id" ? "knot" : "knots"} ≈ ${l.speed_ms} ${LANG === "id" ? "meter per detik" : "metres per second"}`
        : null;
      return `<tr>
      <td><span aria-label="${esc(layerSr)}">${esc(l.base)}–${esc(l.top)}</span></td>
      <td>${esc(LANG === "id" ? l.top_human_id : l.top_human_en)}</td>
      <td>${motionSr ? `<span aria-label="${esc(motionSr)}">${motionTxt}</span>` : motionTxt}</td>
    </tr>`; }).join("")}</tbody></table>`;
}

function renderVaac() {
  const v = SNAP.vaac;
  if (v.state === "error") {
    $("#card-vaac").innerHTML = `<div class="callout warn">${T("vaac_unreachable")}</div>
      <p class="stamp">${esc(v.error || "")}</p>`;
    return;
  }
  if (v.state !== "advisory" || !v.advisory_nr) {
    $("#card-vaac").innerHTML = `<div class="callout warn"><b>${T("vaac_nil")}.</b>
      ${v.state === "stale" ? T("vaac_stale") : T("vaac_nil_body")}</div>`;
    return;
  }
  const fc = Object.entries(v.forecasts || {}).map(([k, f]) => `
    <div class="kv"><span class="k">${T("fcst_cloud")} ${esc(k)}</span><span class="v stamp">${esc(f.valid_wib || fmtWib(f.valid_utc))}</span></div>
    ${layerTable(f.layers)}`).join("");
  /* terminated bulletin (e.g. 2026/217): the episode's official end — show
     it as such, not as business-as-usual advisory traffic */
  const termNote = v.terminated
    ? `<div class="callout ok" style="margin-top:10px"><b>${T("vaac_term_t")}.</b> ${T("vaac_term_b")}</div>`
    : "";
  $("#card-vaac").innerHTML = `
    <div style="display:flex;gap:12px;align-items:baseline;flex-wrap:wrap">
      <b style="font-size:1.02rem">${T("vaac_advisory")} ${esc(v.advisory_nr)}</b>
      <span class="stamp">${T("issued")}: <b>${esc(v.dtg_wib || fmtWib(v.dtg_utc))}</b> (${relWib(v.dtg_utc)})</span>
    </div>
    ${termNote}
    <div class="kv" style="margin-top:8px"><span class="k">${T("eruption_detail")}</span><span class="v" lang="en">${esc(v.eruption_details || "—")}</span></div>
    <div class="stamp" style="margin:8px 0 2px">${T("obs_cloud")}</div>
    ${layerTable(v.observed_layers)}
    <p class="stamp">${T("abbr_note")}</p>
    <details class="vaac-codes"><summary>${T("vaac_codes_t")}</summary>
      ${T("vaac_codes")}<p class="stamp">${T("vaac_codes_ex")}</p></details>
    <div style="margin-top:12px">${fc}</div>
    ${v.remarks ? `<div class="callout" lang="en"><b>${T("remarks")}:</b> ${esc(v.remarks)}</div>` : ""}
    ${v.next_advisory_by_wib ? `<div class="kv"><span class="k">${T("next_adv")}</span><span class="v">${esc(v.next_advisory_by_wib)}</span></div>` : ""}
    <div class="grid2" style="margin-top:12px">
      <div>${v.graphic_asset ? `<div class="stamp" style="margin-bottom:4px">${T("graphic")}</div>
        <img class="pic" src="${esc(v.graphic_asset)}" alt="${esc(T("alt_vaac_graphic"))}" loading="lazy">` : ""}</div>
      <div><details><summary>${T("bulletin")}</summary><pre class="bulletin" lang="en">${esc(v.bulletin_text || "")}</pre></details>
        <p class="stamp" style="margin-top:8px"><a href="${esc(v.source_url)}" target="_blank" rel="noopener">${esc(v.source_url)}</a></p></div>
    </div>`;
}

function renderSat() {
  const s = SNAP.satellite || {};
  const cards = ["snpp", "modis"].map((k) => {
    const d = s[k];
    if (!d) return `<div class="card"><p class="stamp">${T("sat_none")}</p></div>`;
    const sensor = d.sensor_label || (k === "snpp" ? "Suomi NPP (VIIRS)" : "Aqua (MODIS)");
    const firmsUrl = `https://firms.modaps.eosdis.nasa.gov/map/#d/${d.date},${d.date}/@${VOLC.lon},${VOLC.lat},8z`;
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
  /* quiet state (activity_state): no live episode -> the model is paused and
     whatever is on screen is the ARCHIVE of the ended episode, never a
     fresh-looking "today" computation */
  const quiet = !!(SNAP && SNAP.activity && SNAP.activity.state === "quiet");
  const pausedNote = quiet
    ? `<div class="callout ok"><b>${T("model_paused_t")}.</b> ${T("model_paused_b")
        .replace("{when}", MODEL && MODEL.computed_utc ? fmtWib(MODEL.computed_utc) : "—")}</div>`
    : "";
  if (!MODEL || MODEL.status !== "approved") {
    box.innerHTML = `${pausedNote}<div class="model-unpub">
      <svg class="ic"><use href="#i-shield"/></svg>
      <h3 style="margin:8px 0 4px">${T("model_unpub_t")}</h3>
      <p class="stamp" style="max-width:520px;margin:0 auto">${T("model_unpub_b")}</p></div>`;
    return;
  }
  // Canonical band list: even if an older builder omitted no-data rows entirely,
  // every layer keeps its place on the table (missing => explicit "no data").
  const CANON = [
    ["~0-1 km surface", 0.5], ["~1-2 km ASH-CRITICAL", 1.5], ["~2-4 km ASH-CRITICAL", 3.0],
    ["~4-6 km", 5.0], ["~6-9 km", 7.5], ["~9-16 km upper", 12.0]];
  const srcRows = CANON.map(([name, nom]) =>
    MODEL.layers.find((l) => l.layer === name) ||
    { layer: name, data: false, nom_alt_km: nom, legacy_missing: true });
  const rows = srcRows.map((l, i) => {
    // legacy builders omitted the data flag: infer from presence of a solution
    l = Object.assign({}, l, { data: l.data !== undefined ? !!l.data : (l.toward_deg != null) });
    const c = BAND_COLORS[i % 6];
    const alt = l.data
      ? esc(LANG === "id" ? l.alt_human_id : l.alt_human_en)
      : (l.nom_alt_km != null ? `≈ ${l.nom_alt_km} km ${LANG === "id" ? "dpl" : "asl"}` : "—");
    const motion = l.data
      ? `<b>${esc(l.toward_compass)}</b> (${l.toward_deg.toFixed(0)}°${l.uncertainty_deg ? " ±" + l.uncertainty_deg + "°" : ""}) · ${l.speed_ms.toFixed(1)} m/s`
      : `<span class="stamp">${T("no_data")}</span>`;
    const stats = l.data
      ? `R=${l.consistency_R.toFixed(2)}, n=${l.n_vectors}, ${T("model_conf")}: ${esc(l.confidence || "?")}`
      : "—";
    const gapTitle = l.data ? "" :
      ` title="${esc((LANG === "id" ? l.note_id : l.note_en) || "")}"`;
    return `<tr${l.data ? "" : ` class="nodata"${gapTitle}`}>
      <td><span class="sw" style="display:inline-block;width:11px;height:11px;border-radius:3px;background:${c};margin-right:7px"></span>${esc(l.layer)}${l.relevant_today ? `<span class="star" title="${esc(T("star_note"))}">★</span>` : ""}</td>
      <td>${alt}</td>
      <td>${motion}</td>
      <td class="stamp">${stats}</td></tr>`;
  }).join("");
  // v2.3: one clear explanation of what a "no data" row means, right under the table
  const hasGap = srcRows.some((l) => !(l.data !== undefined ? !!l.data : l.toward_deg != null));
  const pt = MODEL.plume_top;
  const kind = (MODEL.layers.find((l) => l.trajectory_kind) || {}).trajectory_kind || "steady-wind";
  box.innerHTML = `
    ${pausedNote}
    <div class="kv" style="margin:0"><span class="k">${T("model_approved")}</span>
      <span class="v"><b>${esc(MODEL.approved_by)}</b>—${fmtWib(MODEL.approved_utc)} (${relWib(MODEL.approved_utc)})${quiet ? ` <span class="badge arch">${T("model_archive_badge")}</span>` : ""}</span></div>
    ${MODEL.auto_published && !quiet ? `<p class="stamp">${T("auto_note")}</p>` : ""}
    <div class="kv"><span class="k">${T("model_computed")}</span><span class="v">${fmtWib(MODEL.computed_utc)}</span></div>
    <div class="kv"><span class="k">${T("model_valid")}</span><span class="v stamp">
      hard_failures=${MODEL.validation.hard_failures} · agreement=${esc(MODEL.validation.direction_agreement)} ·
      occurrence=[${(MODEL.validation.occurrence_sources || []).map(esc).join(", ")}]</span></div>
    ${MODEL.backtest ? `<p class="stamp">${T("backtest_line")
      .replace("{m}", MODEL.backtest.mean_abs_deg).replace("{n}", MODEL.backtest.n)}</p>` : ""}
    ${MODEL.envelope_emission && MODEL.envelope_emission.emission_age_h != null ? `<p class="stamp">${T("emission_line")
      .replace("{a}", MODEL.envelope_emission.emission_age_h.toFixed(1))
      .replace("{w}", Math.round(MODEL.envelope_emission.obs_width_km || 0))}</p>` : ""}
    ${(MODEL.envelope_emission && /capped/i.test(MODEL.envelope_emission.note || "")) ? `<p class="stamp">${T("emission_capped")
      .replace("{w}", Math.round(MODEL.envelope_emission.obs_width_km || 0))}</p>` : ""}
    ${pt ? `<div class="callout"><b>${T(quiet ? "model_plume_top_arch" : "model_plume_top")}:</b>
      ${esc(LANG === "id" ? pt.human_id : pt.human_en)}—${esc(pt.source)}</div>`
      : `<p class="stamp">${T("model_no_top")}</p>`}
    <div class="stamp" style="margin:10px 0 2px">${T("model_layers")}</div>
    <table><thead><tr><th>${T("layer")}</th><th>${T("height")}</th><th>${T("motion")}</th><th></th></tr></thead>
    <tbody>${rows}</tbody></table>
    ${hasGap ? `<p class="stamp" style="margin-top:6px">${T("no_data_note")}</p>` : ""}
    <p class="stamp" style="margin-top:10px">${T("model_traj_kind").replace("{kind}", esc(kind))} · ${T("abbr_note")} · ${T("star_note")}</p>
`;
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
  const vx = X(VOLC.lon), vy = Y(VOLC.lat);
  P.push(`<circle cx="${vx}" cy="${vy}" r="7" fill="none" stroke="#ff5544" stroke-width="1.4" opacity=".9"/>`);
  P.push(`<circle cx="${vx}" cy="${vy}" r="2.6" fill="#ff5544"/>`);
  P.push(`<text x="${vx + 10}" y="${vy + 4}" font-size="11" font-weight="700" fill="#fff" opacity=".92">${esc(VOLC.name)}</text>`);
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
    wire(); loopTicks(); loopShow();
    // prefers-reduced-motion: start paused instead of autoplaying; the play
    // button still works — user-initiated motion is always allowed.
    const RM = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    loopSetPlaying(!RM);
  } else { loopTicks(); }
}

/* ---------------------------------------------------------------- map ----- */
/* Leaflet + OpenStreetMap. Replaced the hand-drawn SVG map because:
   real coastlines for free, proper mobile gestures, no text-selection-while-
   dragging bug, and a native collapsible layer control. Vectors still render
   if tiles cannot load (offline preview), just without basemap. */
const MAP = { el: null, control: null, groups: {},
  on: { obs: true, f6: true, f12: false, f18: false, tracks: true, tracks_upper: false,
        envs: false, union: true, unionall: false } };
let VENT = [-6.102, 105.423];   // vent marker; refreshed from the registry in loadAll()
const PCOL = { obs: "#9B2B1A", f6: "#d97706", f12: "#b45309", f18: "#78716c" };

let LEAFLET = 0;   // 0 idle, 1 loading, 2 ready, 3 failed
function loadLeaflet(cb) {
  if (typeof L !== "undefined" || LEAFLET === 2) return cb(true);
  if (LEAFLET === 3) return cb(false);
  if (LEAFLET === 1) return;              // already fetching
  LEAFLET = 1;
  // Leaflet is vendored in site/vendor/ and loaded by index.html before this
  // file. If that copy is missing (skipped upload), fall back to CDNs before
  // giving up - the message must never blame the user's internet wrongly.
  const sources = [
    "https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js",
    "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js",
  ];
  const css = document.createElement("link");
  css.rel = "stylesheet";
  css.href = "https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css";
  document.head.appendChild(css);
  let i = 0;
  const tryNext = () => {
    if (i >= sources.length) { LEAFLET = 3; return cb(false); }
    const sc = document.createElement("script");
    sc.src = sources[i++];
    sc.onload = () => { LEAFLET = 2; cb(true); };
    sc.onerror = tryNext;
    document.head.appendChild(sc);
  };
  tryNext();
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
  const map = L.map("map", { zoomControl: true }).setView([VOLC.lat - 0.25, VOLC.lon], 8);
  MAP.el = map;
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 12,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(map);
  L.circleMarker(VENT, { radius: 6, color: "#9B2B1A", weight: 2, fillColor: "#9B2B1A", fillOpacity: 0.9 })
    .addTo(map)
    .bindTooltip(VOLC.name, { permanent: true, direction: "right", offset: [9, 0], className: "vent-tip" });
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
      // The model renders as separate toggle families: tracks / per-layer
      // envelopes / combined envelope / worst-case views, so visitors can
      // compare shapes without one family covering the rest of the map.
      // UI polish: the DEFAULT "tracks" family now carries only bands at or
      // below the official cloud top (same rule as the combined envelope);
      // tracks above the top describe ash the bulletin says is not there
      // today, so they moved to their own worst-case toggle, off by default.
      const topKm = MODEL.plume_top && MODEL.plume_top.km != null ? MODEL.plume_top.km : null;
      const bandAlt = (l) => (l.alt_km != null ? l.alt_km
        : (l.nom_alt_km != null ? l.nom_alt_km : null));
      const belowTop = (l) => {
        if (topKm == null) return true;          // no official top: all equal
        const a = bandAlt(l);
        return a == null || a <= topKm + 0.5;
      };
      // Honest per-band ring geometry (max width, end width, area) from the
      // [lon, lat] envelope ring: powers the combined hover tooltip and the
      // below-top combined envelope. Ring = left[] + right[] reversed, so
      // ring[k] pairs with ring[len-1-k].
      const ringStats = (env) => {
        if (!Array.isArray(env) || env.length < 4) return null;
        const n = Math.floor(env.length / 2);
        const cf = Math.cos(-6.1 * Math.PI / 180);
        let wMax = 0, wEnd = 0, s = 0;
        for (let k = 0; k < n; k++) {
          const a = env[k], b = env[2 * n - 1 - k];
          const w = 0.5 * Math.hypot((a[0] - b[0]) * 111.32 * cf, (a[1] - b[1]) * 110.57);
          if (w > wMax) wMax = w;
          if (k === n - 1) wEnd = w;
        }
        for (let i = 0; i < env.length; i++) {
          const p = env[i], q = env[(i + 1) % env.length];
          s += p[0] * q[1] - q[0] * p[1];
        }
        return { wMax, wEnd, area: Math.abs(s) / 2 * 110.57 * 111.32 * cf };
      };
      // One COMBINED tooltip per band (track + envelope state). Hovering a
      // thin/degenerate ring used to flip between two different tooltips
      // (track info vs envelope info); identical content on both the line
      // and the ring makes that flip invisible, whatever layer wins the hit.
      const envLine = (l) => {
        const st = ringStats(l.envelope);
        if (!st || st.wMax < 0.5) return `${T("map_env")}: ${T("map_env_none")}`;
        return `${T("map_env")}: ${T("map_env_w")
          .replace("{a}", st.wMax.toFixed(1)).replace("{b}", st.wEnd.toFixed(1))}`;
      };
      const trackParts = [];
      const upperParts = [];
      const envParts = [];
      ls.forEach((l, i) => {
        const c = BAND_COLORS[i % 6];
        const e = l.trajectory[l.trajectory.length - 1];
        const nwpOnly = l.trajectory_kind === "nwp-only";   // no Himawari vectors this slot
        const lab = (l.toward_deg != null
          ? `${l.layer} → ${esc(l.toward_compass)} ${l.toward_deg.toFixed(0)}°${l.uncertainty_deg ? " ±" + l.uncertainty_deg + "°" : ""}`
          : `${l.layer} → ${LANG === "id" ? "model cuaca saja" : "weather model only"}`)
          + `<br>${envLine(l)}`;
        const dest = belowTop(l) ? trackParts : upperParts;
        dest.push(L.polyline(l.trajectory.map((pt) => [pt[0], pt[1]]),
          { color: c, weight: 2.6, opacity: nwpOnly ? 0.7 : 0.9,
            dashArray: nwpOnly ? "5 7" : null }).bindTooltip(lab));
        dest.push(L.circleMarker([e[0], e[1]], { radius: 4, color: c, fillColor: c, fillOpacity: 1 })
          .bindTooltip(`+${e[2]}h · ${l.layer}`));
        if (l.envelope && l.envelope.length > 2) {
          // envelope rings are [lon, lat] (GeoJSON order); Leaflet wants [lat, lon]
          envParts.push(L.polygon(l.envelope.map((pt) => [pt[1], pt[0]]),
            { color: c, weight: 0.8, fillColor: c,
              fillOpacity: 0.07, dashArray: "2 4" })
            .bindTooltip(lab));               // same combined content as the track
        }
        Object.entries(l.settling_classes || {}).forEach(([cn, obj]) => {
          // classes whose mass has fully settled get no marker: a dot for ash
          // that has already landed is visual noise, not information
          if (obj.mass_remaining != null && obj.mass_remaining < 0.01) return;
          const pts = obj.pts || obj;
          const ce = pts[pts.length - 1];
          if (!ce || ce[0] == null) return;
          dest.push(L.circleMarker([ce[0], ce[1]], { radius: 3, color: c, weight: 1.4,
            fillColor: "#fff", fillOpacity: 0.9 })
            .bindTooltip(`${cn} ash: +${ce[2]}h, alt ${ce[3]} km` +
              (obj.mass_remaining != null ? `, mass left ${(obj.mass_remaining * 100).toFixed(0)}%` : "")));
          (obj.wet_points || []).forEach((w) => {
            dest.push(L.circleMarker([w.lat, w.lon], { radius: 3.4, color: "#2563eb",
              weight: 1.2, fillColor: "#2563eb", fillOpacity: 0.55 })
              .bindTooltip(`rain cell ${w.rate_mm_h} mm/h at +${w.hours}h (wet deposition)`));
          });
        });
      });
      // v2.3: dual combined hull. envelope_union = bands at/below the official
      // cloud top (the VAAC-comparable shape); envelope_union_all = every band
      // 0-16 km, the worst-case view — the builder only emits it when the two
      // actually differ, so quiet/high-top days never show duplicate hulls.
      const mkGroup = (key, polys, label) => {
        if (!polys.length) return;
        const g = L.layerGroup(polys);
        g._krkKey = key;
        MAP.groups[key] = g;
        named[label] = g;
      };
      mkGroup("tracks", trackParts, T("map_tracks"));
      if (upperParts.length) mkGroup("tracks_upper", upperParts, T("map_tracks_upper"));
      mkGroup("envs", envParts, T("map_envs"));
      // Combined envelope, drawn HONESTLY: the actual per-band rings of the
      // bands at/below the official top, not their convex hull. On quiet days
      // the hull bridged empty space between diverging bands (about 47x the
      // real ring area), which read like a fabricated corridor. The hull
      // number survives as the worst-case figure in the tooltip.
      const un = MODEL.envelope_union;
      const unBands = ls.filter((l) => belowTop(l) && l.envelope && l.envelope.length > 2);
      if (unBands.length) {
        const hullLine = un && un.area_km2
          ? `<br>${T("map_union_hull").replace("{a}", Math.round(un.area_km2).toLocaleString())}` : "";
        mkGroup("union", unBands.map((l) => {
          const st = ringStats(l.envelope);
          return L.polygon(l.envelope.map((pt) => [pt[1], pt[0]]),
            { color: "#111827", weight: 1.8, fillColor: "#6b7280", fillOpacity: 0.10,
              dashArray: "6 3" })
            .bindTooltip(`${T("map_union")} · ${l.layer}` +
              (st && st.area >= 1 ? ` · ${Math.round(st.area).toLocaleString()} km²` : "") + hullLine);
        }), T("map_union"));
      } else if (un && un.polygon && un.polygon.length > 2) {
        // legacy data path: no per-band rings on this run, fall back to the
        // stored hull polygon so the toggle never silently disappears
        mkGroup("union", [L.polygon(un.polygon.map((pt) => [pt[1], pt[0]]),
          { color: "#111827", weight: 2.2, fillColor: "#6b7280", fillOpacity: 0.08,
            dashArray: "6 3" })
          .bindTooltip(`${T("map_union")} · ${un.n_bands} × ${T("layer")} · ` +
            `${Math.round(un.area_km2).toLocaleString()} km²`)], T("map_union"));
      }
      const una = MODEL.envelope_union_all;
      if (una && una.polygon && una.polygon.length > 2) {
        mkGroup("unionall", [L.polygon(una.polygon.map((pt) => [pt[1], pt[0]]),
          { color: "#9ca3af", weight: 1.4, fillColor: "#d1d5db", fillOpacity: 0.05,
            dashArray: "2 6" })
          .bindTooltip(`${T("map_union_all")} · ${una.n_bands} × ${T("layer")} · ` +
            `${Math.round(una.area_km2).toLocaleString()} km²`)], T("map_union_all"));
      }
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
  $("#foot-stamp").textContent = `${T("updated")}: ${fmtWib(SNAP.generated_utc)} · schema v${SNAP.schema_version} · WIB = UTC+7`;
}

/* a11y: one polite live-region sentence per data refresh so screen readers
   hear that the dashboard loaded (and how fresh it is). Identical content
   is skipped — the 5-min auto-refresh never chatters when nothing changed. */
let LIVE_LAST = "";
function liveAnnounce() {
  const el = $("#live-region");
  if (!el || !SNAP) return;
  const errs = SNAP.source_errors || {};
  const names = Object.keys(errs).map((k) => (k === "magma" ? "MAGMA/PVMBG" : "Darwin VAAC")).join(", ");
  const v = SNAP.vaac || {};
  const vaac = v.state === "advisory" && v.advisory_nr ? v.advisory_nr : T("live_no_adv");
  let msg = T("live_loaded")
    .replace("{level}", (SNAP.status || {}).level_name || "—")
    .replace("{erup}", String((SNAP.eruptions || []).length))
    .replace("{vona}", String((SNAP.vona || []).length))
    .replace("{vaac}", vaac)
    .replace("{wib}", SNAP.generated_wib || "");
  if ((SNAP.activity || {}).state === "quiet") msg = `${T("live_quiet")} ${msg}`;
  if (names) msg = `${T("live_partial").replace("{list}", names)} ${msg}`;
  if (msg === LIVE_LAST) return;
  LIVE_LAST = msg;
  el.textContent = msg;
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

function renderBanner() {
  const box = $("#src-banner");
  if (!box) return;
  /* banner-on pulls #sec-status up to the navbar (site.css): the banner
     itself carries the bottom margin, so the section header keeps its gap. */
  const show = (on) => {
    box.hidden = !on;
    const sec = $("#sec-status");
    if (sec) sec.classList.toggle("banner-on", on);
  };
  const errs = (SNAP && SNAP.source_errors) || {};
  const keys = Object.keys(errs);
  const act = (SNAP && SNAP.activity) || {};
  if (keys.length) {
    /* a degraded fetch outranks everything: silence must never look like
       calm ("no news is not good news") — keep the orange banner on top. */
    const names = keys.map((k) => (k === "magma" ? "MAGMA/PVMBG" : "Darwin VAAC")).join(", ");
    show(true);
    box.className = "callout warn";
    box.innerHTML = T("src_err_banner").replace("{list}", esc(names));
    return;
  }
  if (act.state === "quiet") {
    /* normal mode: green callout with the reason and since-when. The alert
       level card below is untouched — Siaga III stays Siaga III. */
    const why = esc((act.reason || {})[LANG] || "");
    show(true);
    box.className = "callout ok";
    box.innerHTML = `<b>${T("quiet_t")}.</b> ${T("quiet_banner").replace("{why}", why)}` +
      (act.since_utc ? ` <span class="stamp">${esc(T("quiet_since").replace("{when}", fmtWib(act.since_utc)))}</span>` : "");
    return;
  }
  show(false);
  box.className = "callout warn";
  box.innerHTML = "";
}

function renderAll() {
  applyI18n();
  safeRender("banner", renderBanner);
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
  safeRender("live-region", liveAnnounce);
}

function inlineJSON(id) {
  const el = document.getElementById(id);
  if (!el || !el.textContent.trim()) return null;
  try { return JSON.parse(el.textContent); } catch (e) { return null; }
}

async function loadAll() {
  const bootSnap = inlineJSON("boot-snapshot");
  const bootModel = inlineJSON("boot-model");
  // volcano registry first: it decides which data folder everything below
  // reads (?volcano=<slug> in the URL picks a non-primary entry)
  try {
    const reg = await loadJSON("data/volcanoes.json");
    const wanted = new URLSearchParams(location.search).get("volcano");
    const list = reg.volcanoes || [];
    const v = list.find((x) => x.slug === wanted) || list[0];
    if (v && v.slug) {
      VOLC = v;
      DATA = "data/" + v.slug;
      VENT = [v.lat, v.lon];
    }
  } catch (e) { /* older build without the registry: keep the primary default */ }
  SNAP = await loadJSON(DATA + "/snapshot.json").catch(() => bootSnap);
  if (!SNAP) throw new Error("no snapshot available");
  MODEL = await loadJSON(DATA + "/forecast_model.json")
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
    c.innerHTML = `<p class="stamp">${DATA}/snapshot.json: ${esc(String(e))}</p>`;
  });
});
setInterval(() => { loadAll().catch(() => {}); }, 300000);   // auto-refresh 5 min
document.addEventListener("visibilitychange", () => { if (!document.hidden) loadAll().catch(() => {}); });
