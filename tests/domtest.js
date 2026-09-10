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
  console.log("domtest: all sections rendered");
  process.exit(0);
}, 700);
