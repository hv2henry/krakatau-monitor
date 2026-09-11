#!/usr/bin/env python3
"""
volcano_monitor.py — Anak Krakatau (and any MAGMA-listed) activity monitor.

Pulls live data from MAGMA Indonesia (PVMBG / Badan Geologi), the official
Indonesian volcano observatory, and tells you what changed since the last run.

Sources (all server-rendered HTML, stdlib-only scraping):
  * /v1/gunung-api/tingkat-aktivitas  -> alert level of every Indonesian volcano
  * /v1/gunung-api/informasi-letusan  -> near-real-time eruption event log
  * /v1/vona?code=XXX                 -> Volcano Observatory Notice for Aviation
  * /v1/gunung-api/laporan/<id>       -> 6-hourly visual + seismic observation report

Usage
-----
    python3 volcano_monitor.py                 # one-shot status report
    python3 volcano_monitor.py --json          # machine-readable
    python3 volcano_monitor.py --watch         # built-in loop, no cron needed
    python3 volcano_monitor.py --interval 900  # with --watch: poll every 15 min
    python3 volcano_monitor.py --volcano semeru
    python3 volcano_monitor.py --html out.html # also write a dashboard file

Exit codes (handy for scripts / healthchecks):
    0 = ok            1 = fetch/parse failure        2 = ALERT: something escalated

No third-party dependencies. Python 3.8+.
"""

from __future__ import annotations

import argparse
import html as html_mod
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = "https://magma.esdm.go.id"
IMG_BASE = "https://magma.vsi.esdm.go.id"
UA = "Mozilla/5.0 (X11; Linux x86_64) volcano-monitor/1.0 (+personal safety monitoring)"

# Indonesian time-zone abbreviations used in MAGMA text.
TZ_OFFSETS = {"WIB": 7, "WITA": 8, "WIT": 9, "UTC": 0}

LEVEL_NAMES = {1: "Level I (Normal)", 2: "Level II (Waspada)",
               3: "Level III (Siaga)", 4: "Level IV (Awas)"}

# Some well-known MAGMA volcano codes. Anything else is matched by name.
KNOWN_CODES = {
    "anak krakatau": "KRA", "krakatau": "KRA", "semeru": "SEM", "merapi": "MRP",
    "lewotobi laki-laki": "LEW", "ili lewotolok": "LWK", "sinabung": "SIN",
    "ruang": "RUANG", "ibu": "IBU", "marapi": "MAR", "dukono": "DUK",
    "raung": "RAU", "kerinci": "KER", "bromo": "BRO", "agung": "AGU",
    "awu": "AWU", "karangetang": "KAR", "soputan": "SOP", "gamalama": "GML",
    "banda api": "BAN", "dempo": "DEM", "tambora": "TAM", "rinjani": "RIN",
}

# Registry volcanoes (src/volcanoes.py) override the generic guesses above;
# entries with magma_code=None are left to the runtime auto-discovery that
# reads the real code from the VONA page's volcano list.
import volcanoes as _volc_registry

for _e in _volc_registry.all_volcanoes():
    if _e.get("magma_code"):
        KNOWN_CODES.setdefault(_e["name"].lower(), _e["magma_code"])
        for _a in _e.get("aliases", []):
            KNOWN_CODES.setdefault(_a.lower(), _e["magma_code"])

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE  # some ESDM edge nodes serve incomplete chains


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
def fetch(url: str, retries: int = 3, timeout: int = 30) -> str:
    """GET a URL, returning decoded text. Retries with backoff."""
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
            with urllib.request.urlopen(req, timeout=timeout, context=_ctx) as r:
                raw = r.read()
            enc = r.headers.get_content_charset() or "utf-8"
            return raw.decode(enc, "replace")
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < retries - 1:
                time.sleep(2 ** attempt * 1.5)
    raise RuntimeError(f"failed to fetch {url}: {last}")


def strip_tags(s: str) -> str:
    s = re.sub(r"<(script|style)\b.*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html_mod.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


# --------------------------------------------------------------------------
# Parsers
# --------------------------------------------------------------------------
def parse_activity_levels(page: str) -> list[dict]:
    """Return [{name, province, level, level_name, report_url}] for all volcanoes."""
    out = []
    current_level = None
    # Walk the table rows in order; a row introducing a level sets current_level.
    for chunk in re.split(r"<tr\b", page)[1:]:
        m = re.search(r"Level\s+([IVX]+)\s*\(([A-Za-z ]+)\)", strip_tags(chunk))
        if m:
            roman, word = m.group(1), m.group(2).strip()
            current_level = {"I": 1, "II": 2, "III": 3, "IV": 4}.get(roman)
        # Volcano cell:  Anak Krakatau - Lampung <a href="...laporan/ID?signature=..."> ... Lihat laporan</a>
        for cm in re.finditer(
            r"<td>\s*([^<>]+?)\s+-\s*([^<>]+?)\s*<a\s+href=\"([^\"]*laporan[^\"]*)\"", chunk
        ):
            name, prov, url = cm.group(1).strip(), cm.group(2).strip(), cm.group(3)
            if not name or "Tidak ada" in name:
                continue
            out.append({
                "name": name,
                "province": prov,
                "level": current_level,
                "level_name": LEVEL_NAMES.get(current_level, f"Level {current_level}"),
                "report_url": html_mod.unescape(url) if url else None,
            })
    return out


def parse_eruptions(page: str, wanted: str | None = None, max_pages: int = 1) -> list[dict]:
    """Parse the near-real-time eruption timeline (informasi-letusan)."""
    events = []
    cur_date = None
    for item in re.split(r'<div class="timeline-item', page)[1:]:
        dm = re.search(r'<p class="timeline-date">([^<]+)</p>', item)
        if dm:
            cur_date = dm.group(1).strip()
            continue
        tm = re.search(r'<div class="timeline-time">\s*<small>\s*([^<]+?)\s*</small>', item)
        nm = re.search(r'<p class="timeline-title">\s*<a[^>]*>([^<]+)</a>', item)
        tx = re.search(r'<p class="timeline-text">(.*?)</p>', item, re.S)
        dt = re.search(r'href="([^"]*informasi-letusan/[^"]+)"', item)
        if not nm:
            continue
        name = strip_tags(nm.group(1))
        if wanted and wanted.lower() not in name.lower():
            continue
        events.append({
            "volcano": name,
            "date_label": cur_date,
            "time_label": strip_tags(tm.group(1)) if tm else None,
            "text": strip_tags(tx.group(1)) if tx else None,
            "author": _author(item),
            "url": html_mod.unescape(dt.group(1)) if dt else None,
            "utc": _local_to_utc(strip_tags(tx.group(1)) if tx else ""),
        })
    return events


def _author(item: str) -> str | None:
    m = re.search(r'<p class="timeline-author">Dibuat oleh\s*<a[^>]*>([^<]+)</a>', item)
    return m.group(1).strip() if m else None


def _local_to_utc(text: str) -> str | None:
    """'...08 September 2026, pukul 17:56 WIB.' -> ISO-8601 UTC."""
    m = re.search(r"(\d{1,2})\s+(\w+)\s+(\d{4}),\s*pukul\s*(\d{1,2}):(\d{2})\s*(WIB|WITA|WIT)", text)
    if not m:
        return None
    mon = _month(m.group(2))
    if not mon:
        return None
    off = TZ_OFFSETS[m.group(6)]
    dt = datetime(int(m.group(3)), mon, int(m.group(1)), int(m.group(4)), int(m.group(5)),
                  tzinfo=timezone(timedelta(hours=off)))
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


MONTHS = {m: i + 1 for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"])}
MONTHS_ID = {"Januari": 1, "Februari": 2, "Maret": 3, "April": 4, "Mei": 5, "Juni": 6,
             "Juli": 7, "Agustus": 8, "September": 9, "Oktober": 10, "November": 11,
             "Desember": 12}


def _month(s: str) -> int | None:
    return MONTHS.get(s) or MONTHS_ID.get(s.capitalize())


def parse_vona(page: str) -> list[dict]:
    """Parse the VONA (aviation notice) timeline."""
    out = []
    for item in re.split(r'<div class="timeline-item', page)[1:]:
        tm = re.search(r"<small>\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC)\s*</small>", item)
        lvl = re.search(r'btn-sm btn-(\w+)">\s*([^<]+?)\s*</a>', item)
        nm = re.search(r'<p class="timeline-title">\s*<a[^>]*>([^<]+)</a>', item)
        tx = re.search(r'<p class="timeline-text">(.*?)</p>', item, re.S)
        link = re.search(r'href="([^"]*vona/\d+[^"]*)"', item)
        if not tm:
            continue
        out.append({
            "issued_utc": tm.group(1),
            "code": strip_tags(lvl.group(2)).upper() if lvl else None,
            "notice": strip_tags(nm.group(1)) if nm else None,
            "text": strip_tags(tx.group(1)) if tx else None,
            "url": html_mod.unescape(link.group(1)) if link else None,
        })
    return out


def parse_report(page: str) -> dict:
    """Parse a single 6-hourly activity report (laporan) page."""
    t = strip_tags(page)
    rep: dict = {}
    m = re.search(r"(Anak Krakatau|[A-Za-z \-']+?),\s*((?:Senin|Selasa|Rabu|Kamis|Jumat|Sabtu|Minggu)\s*-\s*\d{2} \w+ \d{4}),\s*periode\s*([\d:.\- ]+WIB|[\d:.\- ]+WITA|[\d:.\- ]+WIT)", t)
    if m:
        rep["period"] = f"{m.group(2)} {m.group(3)}".strip()
    lv = re.search(r"Level\s+([IVX]+)\s*\(([A-Za-z ]+)\)", t)
    if lv:
        rep["level"] = {"I": 1, "II": 2, "III": 3, "IV": 4}.get(lv.group(1))
        rep["level_name"] = f"Level {lv.group(1)} ({lv.group(2).strip()})"
    for label, pat in [
        ("visual", r"Pengamatan Visual\s*(.*?)\s*(?:Keterangan Lainnya|Klimatologi|Pengamatan Kegempaan|Rekomendasi)"),
        ("climate", r"Klimatologi\s*(.*?)\s*Pengamatan Kegempaan"),
        ("seismic", r"Pengamatan Kegempaan\s*(.*?)\s*Rekomendasi"),
        ("recommendation", r"Rekomendasi\s*(.*?)(?:Copyright|Dibuat oleh:)"),
        ("author", r"Dibuat oleh,\s*([A-Za-z .,'\-]+?)(?:Gunung Api|Gunung api)"),
    ]:
        mm = re.search(pat, t, re.S)
        if mm:
            rep[label] = mm.group(1).strip()
    img = re.search(r'src="([^"]+/img/ga/[^"]+\.png)"', page)
    if img:
        rep["seismogram_image"] = html_mod.unescape(img.group(1))
    return rep


def vona_ash_top(text: str) -> tuple[int | None, int | None]:
    """VONA carries a satellite/ground ash-top estimate when ash is identified:
    'Best estimate of ash-cloud top is around 1782 FT (557 M) above sea level'.
    Returns (feet, metres) or (None, None)."""
    m = re.search(r"ash-cloud top is around ([0-9,]+)\s*FT\s*\(([0-9,]+)\s*M\)",
                  text or "", re.I)
    if not m:
        return None, None
    return int(m.group(1).replace(",", "")), int(m.group(2).replace(",", ""))


def parse_seismic_counts(text: str) -> dict:
    """Turn the prose seismic summary into {event_type: count}."""
    counts = {}
    if not text:
        return counts
    for m in re.finditer(r"(\d+)\s+kali\s+gempa\s+([A-Za-z /]+?)(?:\s+dengan|\s+S-P|\s*,)", text):
        counts[m.group(2).strip()] = int(m.group(1))
    tm = re.search(r"(\d+)\s+kali\s+gempa\s+Tremor Menerus", text)
    if tm:
        counts["Tremor Menerus"] = int(tm.group(1))
    tremor_only = re.search(r"1\s+kali\s+gempa\s+Tremor Menerus dengan amplitudo ([\d\- ]+mm)", text)
    if tremor_only:
        counts["Tremor Menerus"] = 1
    return counts


# --------------------------------------------------------------------------
# Collect
# --------------------------------------------------------------------------
def discover_vona_code(volcano: str) -> str | None:
    """Read the real MAGMA code for a volcano from the VONA page's volcano list."""
    try:
        page = fetch(f"{BASE}/v1/vona", timeout=25)
    except Exception:
        return None
    best = None
    for m in re.finditer(r"\?code=([A-Z]{3})[^>]*>([^<]+)", page):
        cand, label = m.group(1), m.group(2).strip()
        if label.lower() == volcano.lower().strip():
            return cand
        if volcano.lower().strip() in label.lower() or label.lower() in volcano.lower().strip():
            best = best or cand
    return best


def fetch_darwin_vaac(volcano: str) -> dict:
    """PRIMARY source for ash cloud, via darwin_vaac.py.

    Returns {"available": False, ...} rather than raising, so the monitor keeps
    working if BoM is unreachable — but it NEVER silently reports an all-clear.
    """
    try:
        import darwin_vaac
    except Exception as e:  # noqa: BLE001
        return {"available": False, "state": "error",
                "error": f"darwin_vaac module unavailable: {e}"}
    try:
        return {"available": True, **darwin_vaac.fetch(volcano)}
    except Exception as e:  # noqa: BLE001
        return {"available": False, "state": "error",
                "error": f"could not reach Darwin VAAC: {e}",
                "warning": "Ash-cloud primary source unavailable. Do not assume no hazard."}


def collect(volcano: str, code: str | None, with_report: bool = True,
            with_vaac: bool = True) -> dict:
    name_lc = volcano.lower().strip()
    code = (code or KNOWN_CODES.get(name_lc) or "").upper()

    levels = parse_activity_levels(fetch(f"{BASE}/v1/gunung-api/tingkat-aktivitas"))
    match = next((v for v in levels if name_lc in v["name"].lower()), None)
    if match:
        volcano = match["name"]
        name_lc = volcano.lower()
    if not code:
        code = (discover_vona_code(volcano) or "").upper()

    eruptions = parse_eruptions(fetch(f"{BASE}/v1/gunung-api/informasi-letusan"), volcano)
    vona = []
    if code:
        try:
            vona = parse_vona(fetch(f"{BASE}/v1/vona?code={code}"))
        except Exception as e:  # noqa: BLE001  — not every volcano has a VONA feed
            vona = [{"error": str(e)}]
        for v in vona:
            ft, mt = vona_ash_top(v.get("text"))
            v["ash_top_ft"], v["ash_top_m"] = ft, mt

    report = {}
    if with_report and match and match.get("report_url"):
        try:
            report = parse_report(fetch(match["report_url"]))
        except Exception as e:  # noqa: BLE001
            report = {"error": str(e)}
    if report.get("seismic"):
        report["seismic_counts"] = parse_seismic_counts(report["seismic"])

    vaac = fetch_darwin_vaac(volcano) if with_vaac else {"available": False, "state": "skipped"}

    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "source_hierarchy": {
            "primary_eruption_status": "MAGMA Indonesia / PVMBG (CVGHM), Badan Geologi",
            "primary_ash_cloud": "Darwin VAAC, Bureau of Meteorology (ICAO VAAC)",
            "secondary_ash_estimate": "Himawari-9 AMV + open-meteo (our own advection — unconfirmed)",
        },
        "darwin_vaac": vaac,
        "volcano": match["name"] if match else volcano,
        "code": code or None,
        "province": match["province"] if match else None,
        "level": match["level"] if match else None,
        "level_name": match["level_name"] if match else None,
        "latest_report": report,
        "recent_eruptions": eruptions[:10],
        "eruptions_today": sum(
            1 for e in eruptions[:10]
            if (e.get("utc") or "").startswith(datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        ),
        "latest_vona": vona[:5],
        "indonesia_level_counts": _level_counts(levels),
        "all_levels": levels,
    }


def _level_counts(levels: list[dict]) -> dict:
    c = {1: 0, 2: 0, 3: 0, 4: 0}
    for v in levels:
        if v["level"] in c:
            c[v["level"]] += 1
    return {LEVEL_NAMES[k].split("(")[1].rstrip(")"): n for k, n in c.items()}


# --------------------------------------------------------------------------
# State / alerting
# --------------------------------------------------------------------------
def load_state(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def diff(prev: dict, cur: dict) -> list[dict]:
    """Return a list of alert dicts describing what escalated/changed."""
    alerts = []
    if not prev:
        return alerts

    pl, cl = prev.get("level"), cur.get("level")
    if cl is not None and pl is not None and cl != pl:
        alerts.append({
            "kind": "LEVEL_CHANGE",
            "severity": "critical" if cl > pl else "info",
            "message": f"Alert level changed: {LEVEL_NAMES.get(pl)} -> {LEVEL_NAMES.get(cl)}",
        })

    seen_e = {e.get("url") or e.get("text") for e in prev.get("recent_eruptions", [])}
    for e in cur.get("recent_eruptions", []):
        if (e.get("url") or e.get("text")) not in seen_e:
            alerts.append({
                "kind": "NEW_ERUPTION", "severity": "high",
                "message": f"New eruption reported: {e.get('time_label')} — {(e.get('text') or '')[:180]}",
                "url": e.get("url"),
            })

    seen_v = {v.get("notice") for v in prev.get("latest_vona", [])}
    for v in cur.get("latest_vona", []):
        if v.get("notice") not in seen_v:
            sev = "critical" if v.get("code") == "RED" else "high" if v.get("code") == "ORANGE" else "info"
            alerts.append({
                "kind": "NEW_VONA", "severity": sev,
                "message": f"VONA {v.get('code')} issued {v.get('issued_utc')} — {(v.get('text') or '')[:180]}",
                "url": v.get("url"),
            })

    for key, label in [("visual", "visual observation"), ("recommendation", "official recommendation")]:
        a, b = (prev.get("latest_report") or {}).get(key), (cur.get("latest_report") or {}).get(key)
        if a and b and a != b:
            alerts.append({"kind": "REPORT_UPDATE", "severity": "info",
                           "message": f"New {label}: {b[:240]}"})
    return alerts


def send_webhook(url: str, payload: dict) -> None:
    """POST JSON to a webhook (Slack/Discord/ntfy/generic). Best effort."""
    try:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": UA})
        urllib.request.urlopen(req, timeout=15, context=_ctx).read()
    except Exception as e:  # noqa: BLE001
        print(f"[warn] webhook delivery failed: {e}", file=sys.stderr)


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------
BAR = "─" * 72


def render_text(d: dict, alerts: list[dict]) -> str:
    L = []
    L.append(BAR)
    L.append(f"  {d['volcano'].upper()}  ({d.get('code') or '—'})   {d.get('province') or ''}")
    lvl = d.get("level_name") or "unknown"
    icon = {1: "🟢", 2: "🟡", 3: "🟠", 4: "🔴"}.get(d.get("level"), "⚪")
    L.append(f"  {icon}  {lvl}")
    L.append(f"  fetched: {d['generated_utc']}")
    L.append(BAR)

    if alerts:
        L.append("\n  ⚠  CHANGES SINCE LAST CHECK")
        for a in alerts:
            L.append(f"   [{a['severity'].upper():8}] {a['message']}")
            if a.get("url"):
                L.append(f"              {a['url']}")
    else:
        L.append("\n  No changes since last check.")

    va = d.get("darwin_vaac") or {}
    L.append("\n  PRIMARY — ASH CLOUD (Darwin VAAC, ICAO)")
    if va.get("state") == "advisory" and va.get("advisory"):
        a = va["advisory"]
        L.append(f"   Advisory {a.get('advisory_nr')} issued {a.get('dtg_utc')} "
                 f"(age {va.get('age_hours')} h{'' if va.get('is_current') else ' — STALE'})")
        L.append(f"   Eruption: {a.get('eruption_details')}")
        for i, ly in enumerate(a.get("observed_layers") or [], 1):
            base = "SFC" if ly["base"] == "SFC" else f"FL{ly['base'].replace('FL','')} ({ly['base_km']} km)"
            top = f"FL{ly['top'].replace('FL','')} ({ly['top_km']} km)" if ly.get("top") else "?"
            L.append(f"    Layer {i}: {base} → {top}  moving TOWARD {ly['move_toward']} "
                     f"at {ly['speed_kt']} kt ({ly['speed_ms']} m/s)")
        if a.get("remarks"):
            L.append(f"   VAAC remark: {a['remarks'][:200]}")
    elif va.get("state") in ("nil", "stale"):
        L.append(f"   NO CURRENT ADVISORY for {va.get('vaac_name')} "
                 f"(feed state: {va.get('state')}).")
        L.append("   ⚠ This is NOT an all-clear. The VAAC only issues an advisory when ash")
        L.append("     is identifiable on satellite AND relevant to aviation. Weather cloud,")
        L.append("     a low plume, or dispersion can all produce 'nil'.")
        L.append("   ➜ Rely on MAGMA/CVGHM below for eruption status.")
    else:
        L.append(f"   ⚠ Unavailable: {str(va.get('error'))[:90]}")
        L.append("     Ash-cloud primary source could not be read. Do not assume no hazard.")

    r = d.get("latest_report") or {}
    if r and not r.get("error"):
        L.append(f"\n  LATEST OBSERVATION REPORT — {r.get('period', '?')}   (by {r.get('author', '?')})")
        if r.get("visual"):
            L.append(f"   Visual   : {r['visual']}")
        if r.get("climate"):
            L.append(f"   Weather  : {r['climate']}")
        sc = r.get("seismic_counts") or {}
        if sc:
            L.append("   Seismic  :")
            for k, v in sorted(sc.items(), key=lambda x: -x[1]):
                L.append(f"              {v:>4}×  {k}")
        if r.get("recommendation"):
            L.append(f"   ⚠ Official recommendation: {r['recommendation']}")
        if r.get("seismogram_image"):
            L.append(f"   Seismogram: {r['seismogram_image']}")

    er = d.get("recent_eruptions") or []
    if er:
        L.append(f"\n  ERUPTION EVENTS ({d.get('eruptions_today', 0)} today, showing last {min(len(er), 8)})")
        for e in er[:8]:
            L.append(f"   • {e.get('time_label') or '?':<12} {(e.get('text') or '')[:105]}")

    vn = [v for v in (d.get("latest_vona") or []) if v.get("issued_utc")]
    if vn:
        L.append("\n  PRIMARY — AVIATION NOTICES (VONA, issued by PVMBG)")
        for v in vn[:5]:
            L.append(f"   • [{str(v.get('code')):6}] {v.get('issued_utc')}  {(v.get('text') or '')[:95]}")

    L.append("\n  SECONDARY — our own Himawari-9 + open-meteo ash estimate is NOT included here.")
    L.append("             Run: python3 ash_transport.py --slots 4")
    L.append("             Treat it as an unconfirmed estimate, never as an advisory.")
    L.append("\n  INDONESIA-WIDE ALERT LEVELS: "
             + "  ".join(f"{k}={v}" for k, v in d.get("indonesia_level_counts", {}).items()))
    L.append(BAR)
    return "\n".join(L)


HTML_TMPL = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{volcano} — volcano monitor</title>
<style>
:root{{--bg:#0d1117;--card:#161b22;--bd:#30363d;--fg:#e6edf3;--mut:#8b949e}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;padding:24px}}
.wrap{{max-width:900px;margin:0 auto}}
h1{{font-size:22px;margin:0 0 4px}}
.sub{{color:var(--mut);font-size:13px;margin-bottom:20px}}
.card{{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:16px 18px;margin-bottom:14px}}
.badge{{display:inline-block;padding:4px 12px;border-radius:999px;font-weight:600;font-size:13px}}
.l1{{background:#1a7f37;color:#fff}}.l2{{background:#bf8700;color:#fff}}
.l3{{background:#d1751a;color:#fff}}.l4{{background:#c62828;color:#fff}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px}}
.k{{color:var(--mut);font-size:11px;text-transform:uppercase;letter-spacing:.06em}}
.v{{font-size:15px}}
ul{{margin:6px 0;padding-left:18px}}li{{margin-bottom:7px}}
.alert{{border-left:4px solid #d1751a}}
.critical{{border-left-color:#c62828}}.high{{border-left-color:#d1751a}}
.info{{border-left-color:#388bfd}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
td,th{{text-align:left;padding:5px 8px;border-bottom:1px solid var(--bd)}}
th{{color:var(--mut);font-weight:600;font-size:11px;text-transform:uppercase}}
.num{{text-align:right;font-variant-numeric:tabular-nums}}
a{{color:#58a6ff}}
code{{background:#0d1117;padding:1px 5px;border-radius:4px;font-size:12px}}
img{{max-width:100%;border-radius:6px;border:1px solid var(--bd)}}
</style></head><body><div class="wrap">
<h1>🌋 {volcano}</h1>
<div class="sub">{province} · code {code} · data fetched {generated} · source: MAGMA Indonesia (PVMBG)</div>
<div class="card"><span class="badge l{level}">{level_name}</span></div>
{alerts_html}
{report_html}
{eruptions_html}
{vona_html}
{seismogram_html}
<div class="card"><div class="k">Indonesia-wide alert levels</div><div class="grid">{counts_html}</div></div>
<div class="sub">Generated by volcano_monitor.py — data © PVMBG / Badan Geologi, Kementerian ESDM.</div>
</div></body></html>"""


def _link(url, esc):
    return '<a href="' + esc(url) + '">↗</a>' if url else ""


def _eruption_row(e, esc):
    return ('<tr><td>' + esc(e.get("time_label")) + '</td><td>' + esc(e.get("text")) +
            '</td><td>' + _link(e.get("url"), esc) + '</td></tr>')


def _vona_row(v, esc):
    return ('<tr><td>' + esc(v.get("issued_utc")) + '</td><td><strong>' + esc(v.get("code")) +
            '</strong></td><td>' + esc(v.get("text")) + '</td><td>' +
            _link(v.get("url"), esc) + '</td></tr>')


def render_html(d: dict, alerts: list[dict]) -> str:
    def esc(s):
        return html_mod.escape(str(s if s is not None else ""))

    alerts_html = ""
    if alerts:
        items = "".join(
            f'<div class="card alert {esc(a["severity"])}"><div class="k">{esc(a["kind"])} · {esc(a["severity"])}</div>'
            f'<div class="v">{esc(a["message"])}</div>'
            + (f'<div><a href="{esc(a["url"])}">source</a></div>' if a.get("url") else "")
            + "</div>"
            for a in alerts)
        alerts_html = items
    else:
        alerts_html = '<div class="card"><div class="k">status</div><div class="v">No changes since last check.</div></div>'

    r = d.get("latest_report") or {}
    report_html = ""
    if r and not r.get("error"):
        sc = r.get("seismic_counts") or {}
        rows = "".join(f'<tr><td>{esc(k)}</td><td class="num">{v}</td></tr>'
                       for k, v in sorted(sc.items(), key=lambda x: -x[1]))
        report_html = (
            '<div class="card"><div class="k">Latest observation report</div>'
            f'<div class="v"><strong>{esc(r.get("period"))}</strong> — {esc(r.get("author"))}</div>'
            f'<p><strong>Visual:</strong> {esc(r.get("visual"))}</p>'
            f'<p><strong>Weather:</strong> {esc(r.get("climate"))}</p>'
            + (f'<div class="k">Seismic events</div><table><tr><th>type</th><th class="num">count</th></tr>{rows}</table>' if rows else "")
            + f'<p style="border-left:3px solid #d1751a;padding-left:10px"><strong>⚠ Recommendation:</strong> {esc(r.get("recommendation"))}</p>'
            "</div>")

    er = d.get("recent_eruptions") or []
    eruptions_html = ""
    if er:
        rows = "".join(_eruption_row(e, esc) for e in er[:12])
        eruptions_html = ('<div class="card"><div class="k">Eruption events</div>'
                          f'<div class="v">{d.get("eruptions_today", 0)} today</div>'
                          f'<table><tr><th>time</th><th>report</th><th></th></tr>{rows}</table></div>')

    vn = [v for v in (d.get("latest_vona") or []) if v.get("issued_utc")]
    vona_html = ""
    if vn:
        rows = "".join(_vona_row(v, esc) for v in vn[:8])
        vona_html = ('<div class="card"><div class="k">Aviation notices (VONA)</div>'
                     f'<table><tr><th>issued (UTC)</th><th>code</th><th>details</th><th></th></tr>{rows}</table></div>')

    seismo = (r or {}).get("seismogram_image")
    seismogram_html = (f'<div class="card"><div class="k">Seismogram</div>'
                       f'<img src="{esc(seismo)}" alt="seismogram" loading="lazy"></div>') if seismo else ""

    counts_html = "".join(f'<div><div class="k">{esc(k)}</div><div class="v">{v}</div></div>'
                          for k, v in d.get("indonesia_level_counts", {}).items())

    return HTML_TMPL.format(
        volcano=esc(d["volcano"]), province=esc(d.get("province")), code=esc(d.get("code") or "—"),
        generated=esc(d["generated_utc"]), level=d.get("level") or 0,
        level_name=esc(d.get("level_name")), alerts_html=alerts_html, report_html=report_html,
        eruptions_html=eruptions_html, vona_html=vona_html, seismogram_html=seismogram_html,
        counts_html=counts_html)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def run_once(args) -> int:
    state_path = args.state
    prev = load_state(state_path)
    data = collect(args.volcano, args.code, with_report=not args.no_report)
    alerts = diff(prev, data)

    if args.json:
        payload = dict(data)
        payload["alerts"] = alerts
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_text(data, alerts))

    if args.html:
        with open(args.html, "w", encoding="utf-8") as f:
            f.write(render_html(data, alerts))
        if not args.json:
            print(f"\n[dashboard written to {args.html}]")

    if args.log:
        with open(args.log, "a", encoding="utf-8") as f:
            f.write(json.dumps({**data, "alerts": alerts}, ensure_ascii=False) + "\n")

    if state_path:
        os.makedirs(os.path.dirname(os.path.abspath(state_path)), exist_ok=True)
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)

    if alerts and args.webhook:
        send_webhook(args.webhook, {
            "volcano": data["volcano"], "level": data["level_name"],
            "alerts": alerts, "text": data["volcano"] + ": " + alerts[0]["message"]})

    return 2 if any(a["severity"] in ("critical", "high") for a in alerts) else 0


def main() -> int:
    p = argparse.ArgumentParser(description="Monitor Anak Krakatau via MAGMA Indonesia")
    p.add_argument("--volcano", default="Anak Krakatau", help="volcano name (default: Anak Krakatau)")
    p.add_argument("--code", default=None, help="MAGMA 3-letter code, e.g. KRA (auto-detected for common ones)")
    p.add_argument("--json", action="store_true", help="output JSON instead of text")
    p.add_argument("--html", metavar="PATH", help="also write an HTML dashboard")
    p.add_argument("--log", metavar="PATH", help="append each run as one JSON line (history)")
    p.add_argument("--state", metavar="PATH", default="state.json", help="state file for change detection")
    p.add_argument("--no-report", action="store_true", help="skip fetching the detailed report page (faster)")
    p.add_argument("--webhook", metavar="URL", help="POST alerts here (Slack/Discord/ntfy)")
    p.add_argument("--watch", action="store_true", help="loop forever instead of exiting (no cron needed)")
    p.add_argument("--interval", type=int, default=900, help="with --watch: seconds between polls (default 900)")
    args = p.parse_args()

    if not args.watch:
        try:
            return run_once(args)
        except Exception as e:  # noqa: BLE001
            print(f"[error] {e}", file=sys.stderr)
            return 1

    print(f"[watch] polling every {args.interval}s for {args.volcano} — Ctrl-C to stop", flush=True)
    while True:
        try:
            run_once(args)
        except Exception as e:  # noqa: BLE001
            print(f"[{datetime.now():%H:%M:%S}] error: {e}", file=sys.stderr)
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
