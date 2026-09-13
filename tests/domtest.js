/* tests/domtest.js — headless smoke test for site/app.js (no browser needed).
   Run:  node tests/domtest.js            (from repo root or site/)
   Renders every dashboard section against the real data files using a tiny
   DOM stub, and prints what each card produced. Catches runtime errors that
   `node --check` cannot (typos in function names, bad lookups, i18n gaps). */
const fs = require("fs"), vm = require("vm"), path = require("path");

const SITE = fs.existsSync("site/index.html") ? "site" : ".";
const lang = process.argv.includes("--en") ? "en-US" : "id-ID";
const offline = process.argv.includes("--offline");

function el(id) {
  return {
    id, innerHTML: "", textContent: "", value: "0", max: "0", src: "",
    dataset: {}, style: {}, checked: false, onload: null, onclick: null, oninput: null,
    classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
    clientWidth: 900, clientHeight: 520, naturalWidth: 728, naturalHeight: 595,
    setAttribute() {}, getAttribute() { return null; },
    addEventListener() {}, removeEventListener() {}, setPointerCapture() {}, appendChild() {},
    querySelector() { return el(id + "-child"); }, querySelectorAll() { return []; },
  };
}
const cache = {};
const document = {
  querySelector(s) { return cache[s] || (cache[s] = el(s)); },
  querySelectorAll() { return []; },
  getElementById(id) {
    if (!cache["#" + id]) {
      const e = el("#" + id);
      e.textContent = sandbox["INLINE_" + id] || "";
      cache["#" + id] = e;
    }
    return cache["#" + id];
  },
  addEventListener() {},
  createElement(tag) { return el("<" + tag + ">"); },
  head: el("head"),
  documentElement: el("html"),
  title: "",
};
const sandbox = {
  document, console, setTimeout, clearTimeout,
  setInterval() { return 0; }, clearInterval() {},
  localStorage: { _s: { "krak-lang": lang.slice(0, 2) }, getItem(k) { return this._s[k] || null; }, setItem(k, v) { this._s[k] = v; } },
  navigator: { language: lang },
  Image: function () { this.src = ""; this.naturalWidth = 728; this.naturalHeight = 595; },
  fetch: async (u) => {
    if (offline) throw new Error("offline");
    const p = path.join(SITE, u.split("?")[0].replace(/^\//, ""));
    return { ok: true, status: 200, json: async () => JSON.parse(fs.readFileSync(p, "utf8")) };
  },
  Date, Math, JSON, Object, Array, String, Number, isNaN, parseFloat, parseInt,
};
sandbox.addEventListener = () => {};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);

const html = fs.readFileSync(path.join(SITE, "index.html"), "utf8");
for (const id of ["boot-snapshot", "boot-model"]) {
  const m = new RegExp('<script id="' + id + '" type="application/json">([\\s\\S]*?)</script>').exec(html);
  sandbox["INLINE_" + id] = m ? m[1] : "";
}
vm.runInContext(fs.readFileSync(path.join(SITE, "assets/coast.js"), "utf8"), sandbox, { filename: "coast.js" });
vm.runInContext(fs.readFileSync(path.join(SITE, "app.js"), "utf8"), sandbox, { filename: "app.js" });

setTimeout(() => {
  const len = (s) => (cache[s] && cache[s].innerHTML ? cache[s].innerHTML.length : 0);
  const out = {
    lang: lang, offline,
    status: len("#card-status"), report: len("#card-report"),
    eruptions: len("#card-eruptions"), vona: len("#card-vona"),
    vaac: len("#card-vaac"), loop: len("#card-loop"),
    sat: len("#card-sat"), model: len("#card-model"),
    map: len("#map"),
    chip: cache["#chip-updated-txt"] && cache["#chip-updated-txt"].textContent,
  };
  console.log(JSON.stringify(out, null, 1));
  const bad = Object.entries(out).filter(([k, v]) => typeof v === "number" && v === 0 && k !== "model");
  if (bad.length) { console.error("EMPTY SECTIONS:", bad.map((b) => b[0]).join(", ")); process.exit(1); }
  // v2.3 regression guards on the secondary-model card:
  //  * the ash-vector callout must stay gone (it confused visitors);
  //  * a "no data" row must come with the Open-Meteo fallback explanation.
  const mh = (cache["#card-model"] || {}).innerHTML || "";
  if (/Vektor abu|Ash vector/.test(mh)) { console.error("ASH-VECTOR CALLOUT STILL RENDERED"); process.exit(1); }
  if (/class="nodata"/.test(mh) && !/Open-Meteo/.test(mh)) { console.error("NO-DATA NOTE MISSING"); process.exit(1); }
  // UI-polish guards: i18n decisions that must not regress.
  //  * backtest label typo ("Uji silak") and the stiff "terkopel" wording;
  //  * spaced em-dashes in app.js-rendered templates (site style: no spaces);
  //  * the no-data footnote must use the agreed sentence (not "No data =").
  if (/Uji silak/.test(mh)) { console.error("OLD BACKTEST LABEL (typo) STILL PRESENT"); process.exit(1); }
  if (/terkopel/.test(mh)) { console.error("OLD STIFF WORDING 'terkopel' STILL PRESENT"); process.exit(1); }
  if (/<\/b> — |dpl — Darwin|asl — Darwin/.test(mh)) { console.error("SPACED EM-DASH STILL RENDERED IN TEMPLATE"); process.exit(1); }
  if (/class="nodata"/.test(mh) && !/(nilai vektor angin|wind-vector values)/.test(mh)) { console.error("NEW NO-DATA SENTENCE MISSING"); process.exit(1); }
  // 2026-09-13 archive honesty: when the plume-top anchor is a VONA estimate,
  // its issue date must be visible on screen. The 24-Aug lesson: a 19-day-old
  // anchor rendered without provenance reads as an official "today" value.
  // (offline skips: the model card renders its unpublished branch there)
  if (!offline) {
    try {
      const mdl = JSON.parse(fs.readFileSync(path.join(SITE, "data", "anak-krakatau", "forecast_model.json"), "utf8"));
      if (mdl.plume_top && mdl.plume_top.issued_wib && !/\((diterbitkan|issued) /.test(mh)) {
        console.error("PLUME-TOP ISSUE DATE NOT RENDERED IN MODEL CARD"); process.exit(1);
      }
    } catch (e) { if (e.code !== "ENOENT") { throw e; } }
  }
  // a11y guards: landmarks + live region must exist in the page source.
  if (!/<main id="main"/.test(html)) { console.error("MAIN LANDMARK MISSING"); process.exit(1); }
  if (!/class="skip-link"/.test(html) || !/data-i18n="skip_main"/.test(html)) { console.error("SKIP LINK MISSING"); process.exit(1); }
  if (!/id="live-region"/.test(html)) { console.error("LIVE REGION MISSING"); process.exit(1); }
  const live = (cache["#live-region"] || {}).textContent || "";
  if (!/(Dasbor dimuat|Dashboard loaded)/.test(live)) { console.error("LIVE ANNOUNCEMENT MISSING"); process.exit(1); }
  // a11y guards: screen-reader aids on the four target sections.
  const rh = (cache["#card-report"] || {}).innerHTML || "";
  if (!/scope="row"/.test(rh)) { console.error("REPORT TABLE ROW-SCOPE MISSING"); process.exit(1); }
  if (!/lang="id"/.test(rh)) { console.error("REPORT VERBATIM LANG ATTR MISSING"); process.exit(1); }
  if (/<img class="pic"/.test(rh) && !/(Seismogram PVMBG untuk periode|PVMBG seismogram for this reporting)/.test(rh)) { console.error("DESCRIPTIVE SEISMOGRAM ALT MISSING"); process.exit(1); }
  const vh = (cache["#card-vaac"] || {}).innerHTML || "";
  /* glossary renders only on the advisory branch: the nil/stale card (quiet
     or no-advisory days) legitimately carries no glossary — conditional,
     same logic as the table guards below */
  const vaacNil = /(Tidak ada advisori aktif|No active advisory)/.test(vh);
  if (!vaacNil && !/(cara membaca|how to read)/.test(vh)) { console.error("VAAC CODE GLOSSARY MISSING"); process.exit(1); }
  /* table-dependent guards: on quiet/terminated days (e.g. bulletin 2026/217,
     no OBS/FCST polygons) the VAAC card legitimately has NO table at all —
     scope/compass checks only apply when a table rendered. */
  const hasVaacTable = /<table/.test(vh);
  if (hasVaacTable && !/scope="col"/.test(vh)) { console.error("VAAC TABLE COL-SCOPE MISSING"); process.exit(1); }
  if (hasVaacTable && !/aria-label="[^"]*(barat laut|northwest)/.test(vh)) { console.error("COMPASS SR EXPANSION MISSING"); process.exit(1); }
  /* lang="en" marks verbatim English (eruption details/remarks/bulletin) —
     all advisory-branch content; the nil/stale card carries none of it */
  if (!vaacNil && !/lang="en"/.test(vh)) { console.error("VAAC EN LANG ATTR MISSING"); process.exit(1); }
  if (/<img class="pic"/.test(vh) && !/(Grafik advisori Darwin VAAC|Darwin VAAC advisory chart)/.test(vh)) { console.error("DESCRIPTIVE VAAC GRAPHIC ALT MISSING"); process.exit(1); }
  const vh2 = (cache["#card-vona"] || {}).innerHTML || "";
  if (!/lang="en"/.test(vh2)) { console.error("VONA EN LANG ATTR MISSING"); process.exit(1); }
  // a11y guards: css/contrast decisions (read the stylesheets directly).
  const css = fs.readFileSync(path.join(SITE, "site.css"), "utf8");
  if (!/\.badge\.YELLOW[^{]*\{[^}]*color:\s*#1a1a18/.test(css)) { console.error("YELLOW BADGE BLACK TEXT MISSING"); process.exit(1); }
  if (/\.6[62]rem/.test(css)) { console.error("SUB-12PX FONT STILL PRESENT IN site.css"); process.exit(1); }
  // layout guard: source line must render below the title (not beside it),
  // and icon+title must stay on one row (h2 basis-0 trick).
  if (!/\.sec-head[^{]*\{[^}]*flex-wrap:\s*wrap/.test(css) || !/\.sec-src[^{]*\{[^}]*flex-basis:\s*100%/.test(css) || !/\.sec-head h2[^{]*\{[^}]*flex:\s*1 1 0/.test(css)) { console.error("SECTION SOURCE LINE NOT BELOW TITLE"); process.exit(1); }
  const toks = fs.readFileSync(path.join(SITE, "tokens.css"), "utf8");
  if (!/--text-muted:\s*#676760/.test(toks)) { console.error("MUTED GRAY NOT DARKENED (expected #676760)"); process.exit(1); }
  const appSrc = fs.readFileSync(path.join(SITE, "app.js"), "utf8");
  if (!/prefers-reduced-motion/.test(appSrc)) { console.error("REDUCED-MOTION GUARD MISSING IN LOOP PLAYER"); process.exit(1); }
  if (!/T\("ext_link"\)/.test(appSrc)) { console.error("ICON-ONLY LINK NAME (ext_link) MISSING"); process.exit(1); }
  // quiet-state guards (2026-09-12 lesson): the normal-mode rendering path must
  // exist in BOTH languages, the banner must be able to go green, and the
  // model card must be able to switch to archive labelling. The snapshot on
  // disk decides which branch renders today; these guards keep the branches
  // from silently disappearing in a refactor.
  for (const key of ["quiet_t", "quiet_banner", "vaac_term_t", "vaac_term_b",
                     "model_paused_t", "model_paused_b", "model_archive_badge",
                     "model_plume_top_arch", "model_top_issued", "star_note_arch",
                     "live_quiet"]) {
    const n = (appSrc.match(new RegExp(key + "\\s*:", "g")) || []).length;
    if (n !== 2) { console.error("QUIET-STATE I18N KEY NOT BILINGUAL: " + key + " (found " + n + ")"); process.exit(1); }
  }
  if (!/SNAP\.activity/.test(appSrc) || !/state === "quiet"/.test(appSrc)) { console.error("QUIET-STATE BRANCH MISSING IN RENDER PATH"); process.exit(1); }
  if (!/callout ok/.test(appSrc) || !/\.callout\.ok/.test(css)) { console.error("NORMAL-STATE GREEN CALLOUT MISSING (app.js or site.css)"); process.exit(1); }
  if (!/badge arch/.test(appSrc) || !/\.badge\.arch/.test(css)) { console.error("ARCHIVE BADGE MISSING (app.js or site.css)"); process.exit(1); }
  if (!/v\.terminated/.test(appSrc)) { console.error("TERMINATED-BULLETIN BADGE BRANCH MISSING IN renderVaac"); process.exit(1); }
  // plume-top provenance: the callout must wire pt.issued_wib through the
  // model_top_issued template, and archive mode must not claim "today"
  if (!/pt\.issued_wib/.test(appSrc) || !/model_top_issued/.test(appSrc)) { console.error("PLUME-TOP ISSUE-DATE WIRING MISSING IN app.js"); process.exit(1); }
  if (!/star_note_arch/.test(appSrc)) { console.error("ARCHIVE STAR-NOTE VARIANT MISSING IN app.js"); process.exit(1); }
  // the pipeline side: activity state module + age-gated VONA anchor exist
  const bs = fs.readFileSync(path.join(SITE, "..", "src", "build_site.py"), "utf8");
  if (!/activity_state as ACT/.test(bs) || !/assess_activity/.test(bs)) { console.error("ACTIVITY STATE NOT WIRED INTO build_site.py"); process.exit(1); }
  if (!/vona_plume_top/.test(bs)) { console.error("AGE-GATED VONA PLUME-TOP MISSING IN build_site.py"); process.exit(1); }
  if (/KRAKATAU_SCHED|notify_scheduler/.test(bs)) { console.error("PUSH-BASED SCHEDULER WIRING MUST NOT EXIST (pg_cron pulls activity.state from snapshot.json)"); process.exit(1); }
  if (!/"activity":\s*activity/.test(bs)) { console.error("ACTIVITY VERDICT NOT WRITTEN TO snapshot.json (the pg_cron sync reads it)"); process.exit(1); }
  if (fs.existsSync(path.join(SITE, "..", "supabase", "functions"))) { console.error("EDGE FUNCTION DIRECTORY supabase/functions/ MUST NOT EXIST (scheduler control is pull-based)"); process.exit(1); }
  // 2026-09-13 maintainer feedback: banner spacing, softer pause wording,
  // compact archive badge, slug-correct footer URLs, security.txt, sat zoom.
  if (!/href="data\/__SLUG__\/snapshot\.json"/.test(html) || !/href="data\/__SLUG__\/forecast_model\.json"/.test(html)) { console.error("FOOTER AGENT URLS MISSING THE VOLCANO SLUG"); process.exit(1); }
  if (/href="data\/snapshot\.json"|href="data\/forecast_model\.json"/.test(html)) { console.error("OLD SLUG-LESS FOOTER URL STILL PRESENT"); process.exit(1); }
  if (!/href="security\.txt"/.test(html)) { console.error("SECURITY.TXT LINK MISSING IN FOOTER"); process.exit(1); }
  if (!fs.existsSync(path.join(SITE, "security.txt")) || !fs.existsSync(path.join(SITE, ".well-known", "security.txt"))) { console.error("SECURITY.TXT FILE(S) MISSING (root and .well-known)"); process.exit(1); }
  if (!/sunblaze-ucb\/cybergym/.test(fs.readFileSync(path.join(SITE, "security.txt"), "utf8"))) { console.error("CYBERGYM NOTE MISSING IN security.txt"); process.exit(1); }
  if (!/data\/anak-krakatau\/snapshot\.json/.test(fs.readFileSync(path.join(SITE, "llms.txt"), "utf8")) ||
      !/data\/anak-krakatau\/forecast_model\.json/.test(fs.readFileSync(path.join(SITE, "agent.md"), "utf8"))) { console.error("MACHINE DOCS (llms.txt/agent.md) NOT SLUG-NAMESPACED"); process.exit(1); }
  if (/dijeda/.test(appSrc)) { console.error("STIFF WORDING 'dijeda' STILL PRESENT (agreed: 'dihentikan sementara')"); process.exit(1); }
  if (!/dihentikan sementara/.test(appSrc)) { console.error("'dihentikan sementara' WORDING MISSING"); process.exit(1); }
  if (!/\.badge\.arch[^{]*\{[^}]*font-size:\s*\.74rem/.test(css)) { console.error("ARCHIVE BADGE NOT COMPACT (expected .74rem inline tag, not a full pill row)"); process.exit(1); }
  if (!/#src-banner[^{]*\{[^}]*margin:\s*0 0 14px/.test(css) || !/section\.banner-on/.test(css) || !/banner-on/.test(appSrc)) { console.error("BANNER SPACING RULES MISSING (hug the navbar, breathe before the section)"); process.exit(1); }
  if (!/SAT_Z = 9/.test(bs)) { console.error("SATELLITE STITCH NOT AT NATIVE Z9 (zoomed daily imagery)"); process.exit(1); }
  if (/"sat_box": \(100\.0, 112\.0, -12\.0, -1\.0\)/.test(fs.readFileSync(path.join(SITE, "..", "src", "volcanoes.py"), "utf8"))) { console.error("OLD 12x11deg REGIONAL SAT_BOX RESTORED (should be the Sunda Strait close-up)"); process.exit(1); }
  console.log("domtest: all sections rendered");
  process.exit(0);
}, 700);
