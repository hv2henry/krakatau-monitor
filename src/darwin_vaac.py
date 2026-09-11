#!/usr/bin/env python3
"""
darwin_vaac.py — PRIMARY source for volcanic ash cloud information.

The Darwin Volcanic Ash Advisory Centre (Bureau of Meteorology, Australia) is
one of nine ICAO-designated VAACs and is the AUTHORITY for ash cloud extent,
height and motion over Indonesia. Its Volcanic Ash Advisories (VAAs) are the
product that aviation and emergency services actually act on.

Feed:  https://www.bom.gov.au/aviation/volcanic-ash/darwin-va-advisory.shtml
       Server-rendered HTML containing raw WMO bulletins (FVAUxx ADRM ...).
       No key, no auth. Re-fetch any time; please keep it >= 15 min apart.

Format notes (learned the hard way):
  * Bulletins are hard-wrapped at ~70 chars, so a single logical field spans
    multiple physical lines. Fields must be reassembled before parsing.
  * Positions are degrees+minutes with no separator: "S0606" = 6 deg 06 min S.
  * Heights are flight levels: FL150 = 15,000 ft = 4.57 km AMSL.
  * Motion is in KNOTS, direction is where the cloud is GOING (not FROM).
  * A cloud may have several layers, each "SFC/FLxxx" or "FLxxx/FLyyy", each
    with its own polygon and its own motion vector.
  * The volcano is named "KRAKATAU 262000", not "Anak Krakatau" (MAGMA's name).
    Aliases are handled by NAME_ALIASES below.

*** THE CRITICAL CAVEAT — READ THIS ***

Darwin VAAC is authoritative WHEN IT HAS SOMETHING TO SAY. It is NOT a
continuous monitor. On 2026-09-08 the page reported:

    "Nil current Darwin Volcanic Ash Advisories."

...while MAGMA/CVGHM recorded FIVE separate Anak Krakatau eruptions that same
day. A VAA is issued when ash is identifiable and relevant to aviation; if the
plume is obscured by weather cloud, or below flight levels of concern, or has
dispersed, the VAAC may issue nothing at all.

Therefore:  NO ADVISORY  !=  NO HAZARD.

Never render a missing VAA as "all clear". Always surface the staleness
explicitly ("no current advisory; last Krakatau VAA was 2 days ago"). This
module returns `state="nil"` plus `last_seen_*` fields precisely so callers can
do that, and `is_current()` exists so you cannot accidentally skip the check.

Stdlib only. Python 3.8+.
"""

from __future__ import annotations

import html as html_mod
import json
import re
import urllib.parse
import ssl
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

VAA_URL = "https://www.bom.gov.au/aviation/volcanic-ash/darwin-va-advisory.shtml"
# The shtml page is a JS shell: its static HTML contains ONLY a "Nil current"
# fallback. Live bulletins arrive via this POST endpoint as JSON. A plain GET
# of the page therefore ALWAYS looks like "nil" — the exact trap this module
# exists to prevent. HTML is kept as a degraded fallback only.
VAA_ENDPOINT = "https://www.bom.gov.au/aviation/php/process.php"
GRAPHIC_BASE = "https://www.bom.gov.au/fwo/"
UA = ("Mozilla/5.0 (X11; Linux x86_64) volcano-monitor/1.0 "
      "(community safety monitoring; contact: set --contact in your fork)")

# MAGMA / common name -> the name Darwin VAAC uses in its bulletins.
NAME_ALIASES = {
    "anak krakatau": "KRAKATAU",
    "krakatau": "KRAKATAU",
    "krakatoa": "KRAKATAU",
    "ili lewotolok": "LEWOTOLOK",
    "lewotolok": "LEWOTOLOK",
    "lewotobi laki-laki": "LEWOTOBI",
    "lewotobi": "LEWOTOBI",
    "semeru": "SEMERU",
    "merapi": "MERAPI",
    "ibu": "IBU",
    "dukono": "DUKONO",
    "raung": "RAUNG",
    "rinjani": "RINJANI",
    "ruang": "RUANG",
    "marapi": "MARAPI",
    "sinabung": "SINABUNG",
    "banda api": "BANDA API",
    "soputan": "SOPUTAN",
    "karangetang": "KARANGETANG",
    "gamalama": "GAMALAMA",
    "tambora": "TAMBORA",
    "kerinci": "KERINCI",
    "awu": "AWU",
    "sangeangapi": "SANGEANGAPI",
}

# Registry volcanoes (src/volcanoes.py) take precedence over the generic
# table above: their VAAC name is maintained per-volcano, not guessed. The
# generic table still serves any OTHER Indonesian volcano passed via
# --volcano without a registry entry.
import volcanoes as _volc_registry

for _e in _volc_registry.all_volcanoes():
    if _e.get("vaac_name"):
        NAME_ALIASES.setdefault(_e["name"].lower(), _e["vaac_name"])
        for _a in _e.get("aliases", []):
            NAME_ALIASES.setdefault(_a.lower(), _e["vaac_name"])

FL_TO_M = 30.48          # 1 flight level = 100 ft = 30.48 m
KT_TO_MS = 0.514444
_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE


# --------------------------------------------------------------------------
# fetch
# --------------------------------------------------------------------------
def fetch_vaa_json(timeout: int = 40) -> dict:
    """THE primary path. POST to BoM's process.php -> JSON of live bulletins.

    Returns {"total": int, "advisories": {idx: {"name","text","graphic"}}, ...}
    Raises on transport failure; callers must treat an empty `total` as
    genuinely nil ONLY when this endpoint answered successfully.
    """
    body = urllib.parse.urlencode({"page": "volcanic-ash-darwin", "javascript": "1"}).encode()
    req = urllib.request.Request(VAA_ENDPOINT, data=body, method="POST", headers={
        "User-Agent": UA,
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": VAA_URL,
        "Accept": "application/json, text/javascript, */*",
    })
    with urllib.request.urlopen(req, timeout=timeout, context=_ctx) as r:
        raw = r.read().decode(r.headers.get_content_charset() or "utf-8", "replace")
    d = json.loads(raw)
    if not isinstance(d, dict) or "advisories" not in d:
        raise ValueError(f"unexpected Darwin VAAC payload shape: {list(d)[:6] if isinstance(d, dict) else type(d)}")
    return d


def fetch_vaa_page(url: str = VAA_URL, timeout: int = 40) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=timeout, context=_ctx) as r:
        raw = r.read()
    return raw.decode(r.headers.get_content_charset() or "utf-8", "replace")


def extract_bulletins(html: str) -> list[str]:
    """Pull raw WMO VAA bulletins out of the page.

    They are server-rendered into the HTML (no JS/XHR involved), but wrapped in
    markup and hard-wrapped, so we strip tags then split on the WMO header.
    """
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(p|div|li|tr|pre|h\d)>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_mod.unescape(text)

    # A VAA bulletin starts with its WMO abbreviation header, e.g.
    #   FVAU04 ADRM 062010
    parts = re.split(r"(?=^|\n)\s*(FVAU\d{2}\s+ADRM\s+\d{6})\s*\n", text)
    out = []
    i = 1
    while i < len(parts) - 1:
        header, body = parts[i], parts[i + 1]
        # cut at the next bulletin or at the page furniture
        body = re.split(r"FVAU\d{2}\s+ADRM", body)[0]
        for stop in ("Nil current Darwin", "Please direct enquiries",
                     "Aviation Weather Services", "View Graphical Advisory"):
            k = body.find(stop)
            if k > 0:
                body = body[:k]
        out.append((header.strip() + "\n" + body).strip())
        i += 2
    return out


# --------------------------------------------------------------------------
# parsing helpers
# --------------------------------------------------------------------------
FIELD_KEYS = [
    "DTG", "VAAC", "VOLCANO", "PSN", "AREA", "SOURCE ELEV", "ADVISORY NR",
    "INFO SOURCE", "ERUPTION DETAILS", "EST VA DTG", "OBS VA DTG",
    "EST VA CLD", "OBS VA CLD",
    "FCST VA CLD +6 HR", "FCST VA CLD +12 HR", "FCST VA CLD +18 HR",
    "RMK", "NXT ADVISORY",
]


def _reassemble(bulletin: str) -> dict:
    """Un-wrap the hard line breaks and collect each field's full text."""
    lines = [l.strip() for l in bulletin.splitlines() if l.strip()]
    joined = " ".join(lines)
    joined = re.sub(r"\s+", " ", joined)

    # Build a regex that finds any field label.
    labels = sorted(FIELD_KEYS, key=len, reverse=True)
    pat = re.compile(r"(?<![\w/+-])(" + "|".join(re.escape(l) for l in labels) + r")\s*:")
    fields: dict[str, str] = {}
    matches = list(pat.finditer(joined))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(joined)
        val = joined[m.end():end].strip()
        val = val.rstrip("=").strip()
        # "+6 HR" style labels lose their colon in some bulletins; tolerate both
        fields[m.group(1)] = val
    return fields


def parse_position(s: str) -> dict | None:
    """'S0606 E10525' -> {'lat': -6.1, 'lon': 105.4167}"""
    m = re.match(r"\s*([NS])(\d{2})(\d{2})\s+([EW])(\d{2,3})(\d{2})\s*$", s.strip())
    if not m:
        return None
    ns, la_d, la_m, ew, lo_d, lo_m = m.groups()
    lat = int(la_d) + int(la_m) / 60.0
    lon = int(lo_d) + int(lo_m) / 60.0
    return {"lat": round(-lat if ns == "S" else lat, 4),
            "lon": round(-lon if ew == "W" else lon, 4)}


def parse_node(s: str) -> dict | None:
    """One polygon vertex: 'S0614' + 'E10933', possibly space separated."""
    m = re.match(r"\s*([NS])(\d{2})(\d{2})\s*([EW])(\d{2,3})(\d{2})\s*$", s.strip())
    if not m:
        return None
    ns, la_d, la_m, ew, lo_d, lo_m = m.groups()
    lat = int(la_d) + int(la_m) / 60.0
    lon = int(lo_d) + int(lo_m) / 60.0
    return {"lat": round(-lat if ns == "S" else lat, 4),
            "lon": round(-lon if ew == "W" else lon, 4)}


def parse_dtg(s: str) -> datetime | None:
    """'20260906/2010Z' (or an ISO-8601 string) -> aware UTC datetime."""
    if not s:
        return None
    m = re.search(r"(\d{4})(\d{2})(\d{2})/(\d{2})(\d{2})Z", s)
    if m:
        y, mo, d, h, mi = (int(x) for x in m.groups())
        try:
            return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)
        except ValueError:
            return None
    try:                                    # already ISO-8601
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _iso(dt: datetime | None) -> str | None:
    """Compact, unambiguous UTC timestamp for output."""
    return dt.astimezone(timezone.utc).isoformat(timespec="minutes").replace("+00:00", "Z") if dt else None


def parse_obs_time(s: str) -> datetime | None:
    """'06/2030Z' -> datetime; needs the DTG for year/month context."""
    m = re.search(r"(\d{2})/(\d{2})(\d{2})Z", s or "")
    return m.groups() and m or None


def fl_to_km(fl: str | int | None) -> float | None:
    if fl is None:
        return None
    try:
        return round(int(fl) * FL_TO_M / 1000.0, 2)
    except (TypeError, ValueError):
        return None


def parse_cloud(text: str) -> list[dict]:
    """Parse an OBS/EST/FCST VA CLD field into one dict per ash layer.

    A field may hold several layers, each beginning with a height range:
        SFC/FL150 <polygon> MOV W 05KT  SFC/FL500 <polygon> MOV SW 20KT
    """
    if not text:
        return []
    text = text.strip()
    # strip a leading observation time like "06/2030Z"
    text = re.sub(r"^\d{2}/\d{4}Z\s*", "", text)

    # split on layer boundaries
    layer_pat = re.compile(r"(SFC/FL\d{3}|FL\d{3}/FL\d{3}|FL\d{3}|SFC)")
    starts = [(m.start(), m.group(1)) for m in layer_pat.finditer(text)]
    layers = []
    for i, (pos, heights) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(text)
        chunk = text[pos:end]

        base, top = (heights.split("/") + [None])[:2]
        mov = re.search(r"MOV\s+([NSEW]{1,2})\s+(\d{2,3})\s*KT", chunk)
        body = chunk[len(heights):]
        body = re.split(r"MOV\s+", body)[0]
        nodes = [parse_node(x) for x in re.findall(r"[NS]\d{4}\s*[EW]\d{4,5}", body)]
        nodes = [n for n in nodes if n]
        if not nodes and not mov:
            continue
        layers.append({
            "base": base, "top": top,
            "base_km": None if base == "SFC" else fl_to_km(base.replace("FL", "")),
            "top_km": fl_to_km(top.replace("FL", "")) if top else None,
            "move_toward": mov.group(1) if mov else None,
            "speed_kt": int(mov.group(2)) if mov else None,
            "speed_ms": round(int(mov.group(2)) * KT_TO_MS, 1) if mov else None,
            "polygon": nodes,
        })
    return layers


# --------------------------------------------------------------------------
# main parse
# --------------------------------------------------------------------------
def strip_bulletin_markup(text: str) -> str:
    """JSON bulletins arrive wrapped in <p class="product"> ... <br /> tags."""
    t = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    t = re.sub(r"</p>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    return html_mod.unescape(t).strip()


def parse_advisory(bulletin: str) -> dict:
    bulletin = strip_bulletin_markup(bulletin)
    f = _reassemble(bulletin)
    header = re.search(r"(FVAU\d{2})\s+(ADRM)\s+(\d{6})", bulletin)
    dtg = parse_dtg(f.get("DTG", ""))

    volc = f.get("VOLCANO", "")
    vm = re.match(r"([A-Z][A-Z \-]*?)\s*(\d{6})?$", volc.strip())
    vname, vnum = (vm.group(1).strip(), vm.group(2)) if vm else (volc.strip(), None)

    elev = re.search(r"(\d+)\s*M\s*AMSL", f.get("SOURCE ELEV", ""))

    obs_raw = f.get("OBS VA DTG") or f.get("EST VA DTG") or ""
    obs_is_estimated = "EST VA DTG" in f and "OBS VA DTG" not in f
    obs = None
    om = re.search(r"(\d{2})/(\d{2})(\d{2})Z", obs_raw)
    if om and dtg:
        d, h, mi = int(om.group(1)), int(om.group(2)), int(om.group(3))
        try:
            obs = dtg.replace(day=d, hour=h, minute=mi)
            if obs > dtg + timedelta(hours=1):      # wrapped to previous month
                obs = (dtg.replace(day=1) - timedelta(days=1)).replace(day=d, hour=h, minute=mi)
        except ValueError:
            obs = None

    cld_field = f.get("OBS VA CLD") or f.get("EST VA CLD") or ""
    layers = parse_cloud(cld_field)

    fcst = {}
    for h in (6, 12, 18):
        raw = f.get(f"FCST VA CLD +{h} HR", "")
        tm = re.search(r"(\d{2})/(\d{4})Z", raw)
        valid = None
        if tm and dtg:
            try:
                valid = dtg.replace(day=int(tm.group(1)),
                                    hour=int(tm.group(2)[:2]),
                                    minute=int(tm.group(2)[2:]))
                if valid < dtg:
                    nm = (dtg.replace(day=28) + timedelta(days=4)).replace(day=1)
                    valid = valid.replace(year=nm.year, month=nm.month)
            except ValueError:
                valid = None
        fcst[f"+{h}h"] = {"valid_utc": _iso(valid),
                          "layers": parse_cloud(raw)}

    nxt = f.get("NXT ADVISORY", "")
    nxt_dt = None
    nm = re.search(r"(\d{4})(\d{2})(\d{2})/(\d{2})(\d{2})Z", nxt)
    if nm:
        try:
            nxt_dt = datetime(int(nm.group(1)), int(nm.group(2)), int(nm.group(3)),
                              int(nm.group(4)), int(nm.group(5)), tzinfo=timezone.utc)
        except ValueError:
            pass

    return {
        "wmo_header": header.group(0) if header else None,
        "bulletin_id": header.group(1) if header else None,
        "dtg_utc": _iso(dtg),
        "vaac": f.get("VAAC"),
        "volcano": vname,
        "volcano_number": vnum,
        "area": f.get("AREA"),
        "position": parse_position(f.get("PSN", "")),
        "source_elev_m": int(elev.group(1)) if elev else None,
        "advisory_nr": f.get("ADVISORY NR"),
        "info_source": f.get("INFO SOURCE"),
        "eruption_details": f.get("ERUPTION DETAILS"),
        "observed": obs_is_estimated is False,
        "cloud_obs_dtg_utc": _iso(obs),
        "cloud_obs_is_estimated": obs_is_estimated,
        "observed_layers": layers,
        "forecast": fcst,
        "remarks": f.get("RMK"),
        "next_advisory_by_utc": _iso(nxt_dt),
        "raw": bulletin,
    }


def vaac_alias(name: str) -> str:
    """Map a MAGMA/common volcano name to Darwin VAAC's name."""
    k = name.lower().strip()
    return NAME_ALIASES.get(k, k.upper())


def fetch(volcano: str = "Anak Krakatau", page: str | None = None,
          payload: dict | None = None) -> dict:
    """Fetch and return the primary-source ash picture for one volcano.

    `state` is one of:
      "advisory" — a current VAA exists; `advisory` holds parsed data
      "nil"      — the endpoint answered successfully and reports zero
                   current advisories (genuine nil, not a fetch artifact)
      "stale"    — advisories exist, but none for this volcano
      "error"    — the feed could not be read; `error` explains why
    """
    target = vaac_alias(volcano)
    fetched_at = datetime.now(timezone.utc)

    via = "POST " + VAA_ENDPOINT
    try:
        d = payload if payload is not None else fetch_vaa_json()
    except Exception as e:  # noqa: BLE001
        # degraded fallback: static HTML. Its bulletin area is JS-rendered, so
        # it can only ever CONFIRM advisories, never prove their absence.
        via = "GET " + VAA_URL + " (degraded: cannot prove nil)"
        try:
            html = page if page is not None else fetch_vaa_page()
            bullets = extract_bulletins(html)
            d = {"total": len(bullets),
                 "advisories": {str(i): {"name": "", "text": b, "graphic": None}
                                for i, b in enumerate(bullets)}}
        except Exception as e2:  # noqa: BLE001
            return {"state": "error", "volcano": volcano, "vaac_name": target,
                    "error": f"endpoint failed ({e}); html fallback failed ({e2})",
                    "fetched_utc": _iso(fetched_at), "via": via}

    adv_raw = d.get("advisories") or {}
    items = adv_raw.items() if isinstance(adv_raw, dict) else enumerate(adv_raw)
    parsed = []
    for idx, a in items:
        txt = a.get("text") if isinstance(a, dict) else a
        name = a.get("name") if isinstance(a, dict) else ""
        graphic = a.get("graphic") if isinstance(a, dict) else None
        if not txt:
            continue
        p = parse_advisory(txt)
        p["vaac_label"] = (name or p.get("volcano") or "").strip()
        p["graphic_url"] = (GRAPHIC_BASE + graphic) if graphic else None
        p["feed_index"] = str(idx)
        parsed.append(p)
    parsed = [p for p in parsed if p.get("volcano")]

    result = {
        "source": "Darwin VAAC (Bureau of Meteorology, Australia)",
        "source_url": VAA_URL,
        "data_url": VAA_ENDPOINT,
        "authority": "ICAO-designated Volcanic Ash Advisory Centre - primary for ash cloud",
        "volcano": volcano,
        "vaac_name": target,
        "fetched_utc": _iso(fetched_at),
        "via": via,
        "all_bulletins": len(parsed),
        "other_volcanoes": sorted({p["volcano"] for p in parsed}),
    }

    if not parsed:
        result.update({
            "state": "nil",
            "advisory": None,
            "warning": ("Darwin VAAC endpoint returned zero current advisories. This does "
                        "NOT mean no hazard - a VAA is only issued when ash is identifiable "
                        "and relevant to aviation. Check MAGMA/CVGHM for eruption status."),
        })
        return result

    mine = [p for p in parsed if target.split()[0] in (p["volcano"] or "").upper()]
    if not mine:
        newest = max(parsed, key=lambda p: p.get("dtg_utc") or "")
        result.update({
            "state": "stale",
            "advisory": None,
            "newest_other": {"volcano": newest["volcano"], "dtg_utc": newest["dtg_utc"]},
            "warning": (f"Darwin VAAC has {len(parsed)} current advisories but none for "
                        f"{target}. Absence of an advisory is not an all-clear."),
        })
        return result

    adv = max(mine, key=lambda p: p.get("dtg_utc") or "")
    dtg = parse_dtg(adv.get("dtg_utc") or "")
    age_h = round((fetched_at - dtg).total_seconds() / 3600.0, 1) if dtg else None

    nxt_iso = adv.get("next_advisory_by_utc")
    nxt_dt = parse_dtg(nxt_iso) if nxt_iso else None
    overdue = bool(nxt_dt and fetched_at > nxt_dt)

    result.update({
        "state": "advisory",
        "advisory": adv,
        "age_hours": age_h,
        "is_current": bool(age_h is not None and age_h <= 12),
        "next_advisory_overdue": overdue,
    })
    if age_h is not None and age_h > 12:
        result["warning"] = (f"This advisory is {age_h} h old. Darwin VAAC promised the next "
                             f"by {nxt_iso}. Treat the ash picture as OUT OF DATE and re-check.")
    elif overdue:
        result["warning"] = (f"Darwin VAAC promised the next advisory by {nxt_iso} and it has "
                             f"not appeared. The feed may be lagging - verify at the source URL.")
    return result


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------
def render(res: dict) -> str:
    L = []
    L.append("=" * 74)
    L.append("  DARWIN VAAC — PRIMARY SOURCE FOR ASH CLOUD")
    L.append("  ICAO Volcanic Ash Advisory Centre, Bureau of Meteorology (Australia)")
    L.append("=" * 74)
    L.append(f"  queried for : {res['volcano']}  (VAAC name: {res['vaac_name']})")
    L.append(f"  fetched     : {res['fetched_utc']}")

    st = res.get("state")
    if st == "error":
        L.append(f"\n  ⚠ FEED ERROR: {res.get('error')}")
        L.append("    Falling back to MAGMA (eruption status) only. Do NOT assume all-clear.")
        return "\n".join(L)

    if st in ("nil", "stale"):
        L.append(f"\n  STATE: {st.upper()} — no current advisory for {res['vaac_name']}")
        L.append("")
        for line in _wrap(res.get("warning", ""), 66):
            L.append("  " + line)
        if res.get("other_volcanoes"):
            L.append("")
            L.append("  Advisories currently published for other volcanoes:")
            for v in res["other_volcanoes"]:
                L.append(f"     • {v}")
        L.append("")
        L.append("  ➜ Use MAGMA/CVGHM as the primary eruption source, and treat any")
        L.append("    Himawari/open-meteo plume estimate as SECONDARY and unconfirmed.")
        return "\n".join(L)

    a = res["advisory"]
    L.append(f"\n  STATE: CURRENT ADVISORY   (age {res.get('age_hours')} h"
             f"{'' if res.get('is_current') else ' — STALE, re-check'})")
    L.append("  " + "-" * 70)
    L.append(f"  Advisory NR    : {a['advisory_nr']}      WMO bulletin: {a['wmo_header']}")
    L.append(f"  Issued (DTG)   : {a['dtg_utc']}")
    L.append(f"  Volcano        : {a['volcano']} {a.get('volcano_number') or ''}  ({a.get('area')})")
    if a.get("position"):
        L.append(f"  Position       : {a['position']['lat']}°, {a['position']['lon']}°")
    L.append(f"  Source elev    : {a['source_elev_m']} m AMSL")
    L.append(f"  Info source    : {a['info_source']}")
    L.append(f"  Eruption       : {a['eruption_details']}")
    L.append(f"  Cloud observed : {a['cloud_obs_dtg_utc']}"
             f"{'  (ESTIMATED, not directly observed)' if a['cloud_obs_is_estimated'] else ''}")

    L.append("\n  OBSERVED ASH CLOUD — the authoritative answer to 'which way is it going'")
    if not a["observed_layers"]:
        L.append("     (no polygon parsed)")
    for i, ly in enumerate(a["observed_layers"], 1):
        base = "SFC" if ly["base"] == "SFC" else f"FL{ly['base'].replace('FL','')} ({ly['base_km']} km)"
        top = f"FL{ly['top'].replace('FL','')} ({ly['top_km']} km)" if ly["top"] else "?"
        L.append(f"    Layer {i}: {base} → {top}")
        L.append(f"       moving TOWARD {ly['move_toward'] or '?'} at {ly['speed_kt']} kt "
                 f"({ly['speed_ms']} m/s)")
        L.append(f"       polygon: {len(ly['polygon'])} vertices"
                 + (f", first {ly['polygon'][0]}" if ly["polygon"] else ""))

    for k, v in (a.get("forecast") or {}).items():
        if not v.get("layers"):
            continue
        L.append(f"\n  FORECAST {k}  (valid {v['valid_utc']})")
        for i, ly in enumerate(v["layers"], 1):
            base = "SFC" if ly["base"] == "SFC" else f"FL{ly['base'].replace('FL','')} ({ly['base_km']} km)"
            top = f"FL{ly['top'].replace('FL','')} ({ly['top_km']} km)" if ly["top"] else "?"
            L.append(f"    Layer {i}: {base} → {top}   ({len(ly['polygon'])} vertices)")

    if a.get("remarks"):
        L.append("\n  VAAC REMARKS (read this — it states how much they trust their own data)")
        for line in _wrap(a["remarks"], 66):
            L.append("    " + line)
    L.append(f"\n  Next advisory no later than: {a.get('next_advisory_by_utc')}")
    if res.get("warning"):
        L.append("\n  ⚠ " + res["warning"])
    L.append("=" * 74)
    return "\n".join(L)


def _wrap(text: str, width: int) -> list[str]:
    words, out, cur = (text or "").split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            out.append(cur); cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        out.append(cur)
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Darwin VAAC primary ash advisory")
    ap.add_argument("--volcano", default="Anak Krakatau")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--fixture", help="parse a saved HTML file instead of fetching")
    a = ap.parse_args()
    page = open(a.fixture, encoding="utf-8", errors="replace").read() if a.fixture else None
    try:
        r = fetch(a.volcano, page=page)
    except Exception as e:  # noqa: BLE001
        print(f"[error] could not reach Darwin VAAC: {e}", file=sys.stderr)
        sys.exit(1)
    if a.json:
        import json
        print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    else:
        print(render(r))
