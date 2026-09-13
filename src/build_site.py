#!/usr/bin/env python3
"""
build_site.py — regenerate the whole static dashboard in one command.

    python3 build_site.py                    # collect + assets + data files
    python3 build_site.py --approve-forecast --approver "Nama Kamu"
                                             # ALSO publish the model section
                                             # (human-in-the-loop gate)

What it produces under site/ (namespaced per volcano, <slug> from
src/volcanoes.py — e.g. anak-krakatau):
    data/volcanoes.json          the volcano registry, for the frontend boot
    data/<slug>/snapshot.json          live official data (MAGMA + Darwin VAAC)
    data/<slug>/forecast_candidate.json our Himawari/open-meteo model, UNPUBLISHED
    data/<slug>/forecast_model.json    the same model, only after a human approves
    data/<slug>/backtest.jsonl         model-vs-VAAC performance ledger
    assets/seismogram.png       PVMBG's own seismogram snapshot
    assets/vaac_graphic.png     Darwin VAAC's own advisory chart
    assets/sat_snpp.jpg         Suomi NPP VIIRS true colour (NASA GIBS), stitched
    assets/sat_aqua.jpg         Aqua MODIS true colour (NASA GIBS), stitched

The web page never scrapes anything: it only reads these files. That is what
makes the site static, free to host, and impossible to break by a upstream
markup change at render time.

Stdlib + Pillow (for tile stitching). Pillow missing => satellite step skipped.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import os
import re
import shutil
import ssl
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

import volcano_monitor
import darwin_vaac
import validate as V
try:  # pure hull helper from the model module (numpy is in requirements.txt)
    from ash_transport import union_envelope
except Exception:  # noqa: BLE001
    union_envelope = None
import volcanoes
import activity_state as ACT

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(REPO, "site")
WIB = timezone(timedelta(hours=7), "WIB")

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE

GIBS = ("https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/{layer}/default/"
        "{date}/GoogleMapsCompatible_Level9/{z}/{row}/{col}.jpg")
GIBS_LAYERS = {"snpp": "VIIRS_SNPP_CorrectedReflectance_TrueColor",
               "aqua": "MODIS_Aqua_CorrectedReflectance_TrueColor",
               "terra": "MODIS_Terra_CorrectedReflectance_TrueColor"}
# region of interest for the daily picture (overridden per volcano by the
# registry's sat_box via framing_for(); module default mirrors the primary)
SAT_BOX = (103.6, 107.2, -7.4, -4.8)   # lon0 lon1 lat0 lat1
SAT_Z = 9                               # GIBS native max for VIIRS/MODIS true colour (~250 m/px)

CREDIT_GIBS = ("NASA GIBS/Earthdata—{sensor} Corrected Reflectance (True Color), "
               "{date}. https://worldview.earthdata.nasa.gov")


def http(url: str, timeout: int = 60, retries: int = 3) -> bytes | None:
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "krakatau-dashboard-build/1.0"})
            with urllib.request.urlopen(req, timeout=timeout, context=_ctx) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            if i == retries - 1:
                print(f"  [warn] GET {url[:80]}: {e}", file=sys.stderr)
                return None
    return None


def wib(dt: datetime | None) -> str | None:
    if not dt:
        return None
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    d = dt.astimezone(WIB)
    return d.strftime("%Y-%m-%dT%H:%M:%S+07:00")


def _parse_any(iso: str) -> datetime | None:
    s = str(iso).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S UTC", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            d = datetime.strptime(s, fmt)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def wib_human(iso: str | None) -> str | None:
    if not iso:
        return None
    d = _parse_any(iso)
    if d is None:
        return str(iso)
    MONTHS_ID = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]
    d = d.astimezone(WIB)
    return f"{d.day:02d} {MONTHS_ID[d.month-1]} {d.year}, {d.hour:02d}:{d.minute:02d} WIB"


COMPASS_DEG = {"N": 0, "NNE": 22.5, "NE": 45, "ENE": 67.5, "E": 90, "ESE": 112.5,
               "SE": 135, "SSE": 157.5, "S": 180, "SSW": 202.5, "SW": 225,
               "WSW": 247.5, "W": 270, "WNW": 292.5, "NW": 315, "NNW": 337.5}


def fl_human(fl: str | None, lang: str = "id") -> str | None:
    """FL070 -> 'FL070 = 7.000 ft ≈ 2,1 km di atas permukaan laut'."""
    if not fl or fl == "SFC":
        return ("permukaan tanah (SFC)" if lang == "id" else "ground level (SFC)")
    m = re.match(r"FL(\d{3})", fl)
    if not m:
        return fl
    feet = int(m.group(1)) * 100
    km = feet * 0.3048 / 1000
    # Short forms by maintainer decision; the abbreviation is explained in the
    # page caption (dpl = di atas permukaan laut / asl = above sea level).
    if lang == "id":
        return f"FL{m.group(1)} = {feet:,} ft ≈ {km:.1f} km dpl".replace(",", ".")
    return f"FL{m.group(1)} = {feet:,} ft ≈ {km:.1f} km asl"


# ------------------------------------------------------- v2.1 model wiring
def obs_polygon_args(vaac: dict | None) -> list[str] | None:
    """CLI args that hand the Darwin OBS polygon to ash_transport (v2.1).

    The model inverts the OBS polygon's cross-track width into a cloud age,
    so the initial spread and airborne mass reflect the EMISSION HISTORY
    instead of silently assuming fresh ash at the analysis time. We use the
    first observed layer that carries a polygon (VAAC lists layers bottom-up
    and the lowest polygon usually describes the bulk of the detected
    cloud); one age is then applied to every band — see ash_transport.py,
    section "EMISSION HISTORY (v2.1)". Pure on the darwin_vaac JSON so it
    can be unit-tested offline. Returns None when nothing usable exists
    (nil/stale advisory, layer without vertices): the model then keeps its
    fresh-emission t=0 default.
    """
    if not vaac or vaac.get("state") != "advisory":
        return None
    layers = (vaac.get("advisory") or {}).get("observed_layers") or []
    ly = next((l for l in layers if len(l.get("polygon") or []) >= 3), None)
    if not ly or ly.get("top_km") is None:
        return None
    parts = []
    for p in ly["polygon"]:
        if isinstance(p, dict):
            lat, lon = p.get("lat"), p.get("lon")
        else:
            lon, lat = p[0], p[1]
        if lat is None or lon is None:
            return None
        parts.append(f"{lat},{lon}")
    base_km = 0.0 if ly.get("base") in (None, "SFC") else (ly.get("base_km") or 0.0)
    # "=" form: southern-hemisphere latitudes start with "-", which argparse
    # would otherwise read as an option flag
    out = ["--obs-polygon=" + ";".join(parts)]
    if ly.get("move_toward"):
        out += ["--obs-mov-deg", str(ly["move_toward"])]
    out += ["--obs-layer-km", f"{base_km},{ly['top_km']}"]
    return out


def width_ledger_entry(vaac: dict | None, cand: dict | None) -> dict:
    """Width bookkeeping for the backtest ledger (v2.1) — the re-fit loop's food.

    Records, per build: the OBS polygon width the model was seeded with, the
    emission age it inferred, the VAAC FCST polygon widths at +6/+12/+18 h,
    and the model's own full widths (2 x detectable half-width, max over the
    bands whose trajectory altitudes intersect the OBS layer) at the same
    hours. Rows stay additive: older rows without these keys keep working.
    Uses ash_transport's pure width helper; if the import fails (numpy or
    netCDF4 missing in this environment) the width block is simply skipped.
    """
    entry: dict = {}
    em = (cand or {}).get("envelope_emission") or {}
    if em.get("obs_width_km") is not None:
        entry["obs_width_km"] = em.get("obs_width_km")
        entry["emission_age_h"] = em.get("emission_age_h")
        entry["emission_source"] = em.get("source")
    if not vaac or vaac.get("state") != "advisory":
        return entry
    adv = vaac.get("advisory") or {}
    ols = adv.get("observed_layers") or []
    ly = next((l for l in ols if len(l.get("polygon") or []) >= 3), None)
    if not ly or ly.get("top_km") is None:
        return entry
    mov_deg = COMPASS_DEG.get(ly.get("move_toward"))
    try:
        import ash_transport as AT                     # noqa: PLC0415
        width_fn = AT.polygon_cross_track_width_km
    except Exception:                                   # noqa: BLE001
        return entry
    base_km = 0.0 if ly.get("base") in (None, "SFC") else (ly.get("base_km") or 0.0)
    top_km = ly["top_km"]
    fcst = []
    # runtime fetch() shape uses "forecast" (parse_advisory output); the
    # site snapshot shape uses "forecasts" — accept both
    fcsts = adv.get("forecast") or adv.get("forecasts") or {}
    for hkey in ("+6h", "+12h", "+18h"):
        layers = (fcsts.get(hkey) or {}).get("layers") or []
        fly = next((l for l in layers if len(l.get("polygon") or []) >= 3), None)
        if not fly:
            continue
        w = width_fn(fly["polygon"], mov_deg)
        if w is None:
            continue
        h = int(hkey[1:-1])
        row = {"h": h, "vaac_km": round(w, 1), "model_km": None}
        # model counterpart: full width at the same hour, max over the bands
        # whose altitude span strictly overlaps the OBS layer
        for pts in (cand or {}).get("trajectories_forecast", {}).values():
            alts = [p.get("alt_km") for p in pts if p.get("alt_km") is not None]
            if not alts or not (min(alts) < top_km and max(alts) > base_km):
                continue
            for p in pts:
                if p.get("hours") == h and p.get("width_km") is not None:
                    row["model_km"] = round(max(row["model_km"] or 0.0, 2 * p["width_km"]), 1)
        fcst.append(row)
    if fcst:
        entry["fcst_widths"] = fcst
    return entry


# ------------------------------------------------------------------ tiles
def _tile_idx(lon: float, lat: float, z: int):
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n)
    return x, y


def _trim_box(im, thresh: int = 8):
    """Bounding box of non-black content (the swath-edge wedge MODIS leaves)."""
    try:
        import numpy as np
        a = np.asarray(im.convert("L"))
        xs = np.where(a.mean(axis=0) > thresh)[0]
        ys = np.where(a.mean(axis=1) > thresh)[0]
        if len(xs) > 10 and len(ys) > 10:
            return (int(xs[0]), int(ys[0]), int(xs[-1]) + 1, int(ys[-1]) + 1)
    except Exception:
        pass
    return (0, 0, im.width, im.height)


def _trim_black(im, thresh: int = 8):
    return im.crop(_trim_box(im, thresh))


def stitch_gibs(sensor: str, date: str, out_path: str) -> dict | None:
    try:
        from PIL import Image
    except ImportError:
        print("  [warn] Pillow missing; skipping satellite stitch", file=sys.stderr)
        return None
    lon0, lon1, lat0, lat1 = SAT_BOX
    x0, y1 = _tile_idx(lon0, lat0, SAT_Z)      # y1 = bottom row (lat0 is south)
    x1, y0 = _tile_idx(lon1, lat1, SAT_Z)
    layer = GIBS_LAYERS[sensor]
    imgs, missing = {}, 0
    for row in range(y0, y1 + 1):
        for col in range(x0, x1 + 1):
            b = http(GIBS.format(layer=layer, date=date, z=SAT_Z, row=row, col=col), 30, 2)
            if not b:
                missing += 1
                continue
            try:
                imgs[(row, col)] = Image.open(io.BytesIO(b)).convert("RGB")
            except Exception:
                missing += 1
    if not imgs:
        return None
    W = (x1 - x0 + 1) * 256
    H = (y1 - y0 + 1) * 256
    canvas = Image.new("RGB", (W, H), (18, 24, 32))
    for (row, col), im in imgs.items():
        canvas.paste(im, ((col - x0) * 256, (row - y0) * 256))
    canvas.save(out_path, "JPEG", quality=82, optimize=True)
    return {"sensor": sensor, "gibs_layer": layer, "date": date,
            "asset": os.path.relpath(out_path, SITE),
            "bbox": list(SAT_BOX), "zoom": SAT_Z,
            "missing_tiles": missing,
            "credit": CREDIT_GIBS.format(
                sensor={"snpp": "Suomi NPP VIIRS", "aqua": "Aqua MODIS",
                        "terra": "Terra MODIS"}[sensor], date=date)}


def latest_gibs_date(lat: float, lon: float) -> str:
    """GIBS lags ~1 day; try today then step back (probing at the vent)."""
    now = datetime.now(timezone.utc)
    for back in range(0, 5):
        d = (now - timedelta(days=back)).strftime("%Y-%m-%d")
        x, y = _tile_idx(lon, lat, SAT_Z)
        if http(GIBS.format(layer=GIBS_LAYERS["snpp"], date=d, z=SAT_Z, row=y, col=x), 25, 1):
            return d
    return (now - timedelta(days=1)).strftime("%Y-%m-%d")


EMBED = {}


def embed_into_index(embed: dict, slug: str) -> None:
    """Inline the snapshot (+ approved model) into index.html so the page
    renders with zero network (offline copy / sandboxed preview). The
    __SLUG__ placeholder in the template's data references is filled with
    the active volcano's data folder (idempotent on every rebuild)."""
    ip = os.path.join(SITE, "index.html")
    html = open(ip, encoding="utf-8").read()
    html = html.replace("__SLUG__", slug)

    def payload(obj):
        if not obj:
            return ""
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")

    snap = payload(embed.get("snapshot"))
    model = None
    mp = os.path.join(SITE, "data", slug, "forecast_model.json")
    if os.path.exists(mp):
        try:
            m = json.load(open(mp, encoding="utf-8"))
            model = m if m.get("status") == "approved" else None
        except Exception:
            model = None
    html = re.sub(r'(<script id="boot-snapshot" type="application/json">).*?(</script>)',
                  lambda m: m.group(1) + snap + m.group(2), html, flags=re.S)
    html = re.sub(r'(<script id="boot-model" type="application/json">).*?(</script>)',
                  lambda m: m.group(1) + payload(model) + m.group(2), html, flags=re.S)
    open(ip, "w", encoding="utf-8").write(html)
    print(f"[build] embedded boot payload into index.html ({len(snap)/1024:.0f} KB snapshot)")


# ------------------------------------------------------------------ loop
# Day/night band selection: visible (1 km, Level7) in daylight, IR (Level6) at night.
LOOP_BANDS = {
    "ir": {"layer": "Himawari_AHI_Band13_Clean_Infrared", "z": 7 - 1, "tms": 6,
           "label": "IR 10.4 µm", "upscale": 2},
    "vis": {"layer": "Himawari_AHI_Band3_Red_Visible_1km", "z": 7, "tms": 7,
            "label": "Visible 0.64 µm (1 km)", "upscale": 1},
}
LOOP_Z = 6
LOOP_BOX = (100.0, 111.0, -11.0, 0.0)      # lon0 lon1 lat0 lat1 (tiles to fetch)
LOOP_CROP = (100.4, 110.4, -10.5, -2.5)   # ~910x736 native at z7: full-width AND sharp     # lon0 lon1 lat0 lat1 (pixel crop after stitch)
LOOP_FRAMES = 12                            # 12 x 10 min = 2 h of motion
LOOP_CREDIT = ("Himawari-9 AHI Band 13 (10.4 um) clean infrared, 10-min cadence, via "
               "NASA GIBS (JMA/NOAA open data). IR brightness: cold/high cloud = white.")


def gibs_time_values(layer: str) -> list[str] | None:
    """Parse the latest available timestamps for a GIBS layer from capabilities."""
    caps = http("https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/1.0.0/WMTSCapabilities.xml", 60, 2)
    if not caps:
        return None
    import re as _re
    for b in _re.findall(r"<Layer>(.*?)</Layer>", caps.decode("utf-8", "replace"), _re.S):
        ids = _re.findall(r"<ows:Identifier>([^<]+)</ows:Identifier>", b)
        if ids and ids[0] == layer:
            vals = _re.findall(r"<Value>([^<]+)</Value>", b)
            out = []
            for v in vals[-6:]:
                m = _re.match(r"([^/]+)/([^/]+)/P(.+)", v)
                if not m:
                    continue
                start, end, step = m.groups()
                fmt = "%Y-%m-%dT%H:%M:%SZ"
                try:
                    t0 = datetime.strptime(start, fmt); t1 = datetime.strptime(end, fmt)
                except ValueError:
                    continue
                mins = 10 if step == "T10M" else 30
                t = t0
                while t <= t1:
                    out.append(t.strftime(fmt)); t += timedelta(minutes=mins)
            return out
    return None


def build_loop(out_dir: str, n: int = LOOP_FRAMES) -> dict | None:
    try:
        from PIL import Image, ImageOps
    except ImportError:
        print("  [warn] Pillow missing; skipping loop", file=sys.stderr)
        return None
    times = gibs_time_values(LOOP_BANDS["ir"]["layer"])
    if not times:
        return None
    times = times[-n:]
    # daylight rule in WIB (UTC+7): use visible band only if the WHOLE window is lit
    def wib_hour(tstr):
        return (datetime.strptime(tstr, "%Y-%m-%dT%H:%M:%SZ") + timedelta(hours=7)).hour
    band = "vis" if all(6.5 <= wib_hour(t) <= 17.5 for t in times) else "ir"
    B = LOOP_BANDS[band]
    lon0, lon1, lat0, lat1 = LOOP_BOX
    z = B["z"]
    x0, y1 = _tile_idx(lon0, lat0, z)
    x1, y0 = _tile_idx(lon1, lat1, z)
    frames = []
    frames_raw = []
    os.makedirs(out_dir, exist_ok=True)
    for stale in os.listdir(out_dir):            # no cross-vintage frame mixing
        if stale.startswith("f") and stale.endswith(".jpg"):
            os.remove(os.path.join(out_dir, stale))
    for tstr in times:
        imgs = {}
        for row in range(y0, y1 + 1):
            for col in range(x0, x1 + 1):
                u = (f"https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/{B['layer']}"
                     f"/default/{tstr}/GoogleMapsCompatible_Level{B['tms']}/{z}/{row}/{col}.png")
                b = http(u, 25, 2)
                if b:
                    try:
                        imgs[(row, col)] = Image.open(io.BytesIO(b)).convert("L")
                    except Exception:
                        pass
        if len(imgs) < (x1 - x0 + 1) * (y1 - y0 + 1) - 2:
            continue
        W = (x1 - x0 + 1) * 256; H = (y1 - y0 + 1) * 256
        canvas = Image.new("L", (W, H), 0)
        for (row, col), im in imgs.items():
            canvas.paste(im, ((col - x0) * 256, (row - y0) * 256))
        # pixel-crop to the strait, using global web-mercator math
        npx = 256 * 2 ** z
        def gx(lon): return (lon + 180.0) / 360.0 * npx
        def gy(lat):
            r = math.radians(lat)
            return (1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * npx
        cx0, cx1 = gx(LOOP_CROP[0]) - x0 * 256, gx(LOOP_CROP[1]) - x0 * 256
        cy0, cy1 = gy(LOOP_CROP[3]) - y0 * 256, gy(LOOP_CROP[2]) - y0 * 256
        crop = canvas.crop((max(0, int(cx0)), max(0, int(cy0)), min(W, int(cx1)), min(H, int(cy1))))
        frames_raw.append(crop)
        frames.append({"t_utc": tstr, "t_wib": wib_human(tstr),
                       "asset": f"assets/loop/f{len(frames):02d}.jpg"})
    if len(frames) < 4:
        return None

    # NO per-frame trimming in the animation: independent trims gave late-window
    # frames (orbit swath edge entering the view) a different aspect than early
    # ones - the "stretched frames 9-12" bug. Fixed grid = constant geometry;
    # a black swath wedge on some frames is an honest satellite artifact.
    for im, meta in zip(frames_raw, frames):
        c = im
        c = ImageOps.autocontrast(c, cutoff=1)
        if B["upscale"] > 1:
            c = c.resize((c.width * B["upscale"], c.height * B["upscale"]), Image.LANCZOS)
        c.save(os.path.join(out_dir, meta["asset"].split("/")[-1]), "JPEG",
               quality=80, optimize=True)

    # ---- verification, so the timestamps are not just our word for it ----
    # 1) every frame time must sit on Himawari's published 10-minute imaging grid
    grid_ok = all(
        (datetime.strptime(f["t_utc"], "%Y-%m-%dT%H:%M:%SZ").minute % 10 == 0)
        and datetime.strptime(f["t_utc"], "%Y-%m-%dT%H:%M:%SZ").second == 0
        for f in frames)
    # 2) independent confirmation that the satellite really imaged that slot:
    #    NOAA's S3 bucket holds Himawari-9 products named with the observation
    #    window (s<start>_e<end>); an existing slot directory proves the image.
    last = frames[-1]["t_utc"]
    lt = datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ")
    slot = f"AHI-L2-FLDK-Winds%2F{lt:%Y}%2F{lt:%m}%2F{lt:%d}%2F{lt:%H%M}%2F"
    noaa_ok = False
    try:
        import urllib.parse as _up
        xml = http(f"https://noaa-himawari9.s3.amazonaws.com/?prefix={slot}&max-keys=1", 25, 2)
        noaa_ok = bool(xml) and b"<Key>" in (xml.encode() if isinstance(xml, str) else xml)
    except Exception:
        noaa_ok = None
    # 3) a directly openable source tile for the newest frame, so any visitor
    #    can verify with one click
    zx, zy = _tile_idx((LOOP_CROP[0] + LOOP_CROP[1]) / 2, (LOOP_CROP[2] + LOOP_CROP[3]) / 2, B["z"])
    verify_url = (f"https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/{B['layer']}"
                  f"/default/{last}/GoogleMapsCompatible_Level{B['tms']}/{B['z']}/{zy}/{zx}.png")

    return {"source": "NASA GIBS/JMA Himawari-9 AHI", "layer": B["layer"],
            "band": band, "band_label": B["label"],
            "interval_min": 10, "frames": frames, "credit": LOOP_CREDIT,
            "roi": list(LOOP_CROP), "zoom": B["z"], "tms": B["tms"],
            "verified": {"ten_minute_grid": grid_ok,
                         "noaa_s3_slot_exists": noaa_ok,
                         "noaa_slot": f"{lt:%Y-%m-%d %H:%M}Z",
                         "verify_url": verify_url},
            "built_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")}


# ------------------------------------------------------------- registry i/o
def framing_for(volc: dict) -> tuple:
    """Per-volcano imagery framing: explicit registry override, else boxes
    centred on the vent. Registered volcanoes pin their framing explicitly
    so a rendered page never silently shifts."""
    lat, lon = volc["lat"], volc["lon"]
    sat = volc.get("sat_box") or (lon - 1.8, lon + 1.8, lat - 1.3, lat + 1.3)
    box = volc.get("loop_box") or (lon - 5.5, lon + 5.5, lat - 5.5, lat + 5.5)
    crop = volc.get("loop_crop") or (lon - 5.1, lon + 5.1, lat - 5.0, lat + 5.0)
    return sat, box, crop


def write_volcanoes_index() -> None:
    """site/data/volcanoes.json — the boot registry the frontend reads to
    decide which volcano's data folder to load (URL ?volcano=<slug> picks
    another entry). Machine consumers get the same list here."""
    out = {
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "volcanoes": [{"slug": e["slug"], "name": e["name"], "region": e.get("region"),
                       "lat": e["lat"], "lon": e["lon"]} for e in volcanoes.all_volcanoes()],
    }
    p = os.path.join(SITE, "data", "volcanoes.json")
    json.dump(out, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[build] {p} ({len(out['volcanoes'])} volcano(s) registered)")


# ------------------------------------------------------------------ build
def verdict_hard_failures(embed) -> bool:
    """The 6h auto-publish gate: refuse unless validation found zero hard fails."""
    vf = embed.get("validation_hard_failures")
    return bool(vf) if vf is not None else False


def archive_run(embed, vaac, slug: str, models_too: bool = True) -> None:
    """Six-hourly memory: our model, the VAAC state, and the official chart.
    Namespaced per volcano under archive/<slug>/. Pruned so the repo stays
    light: 60 model snapshots, 40 VAAC states, 30 graphical advisories
    (one per advisory number).

    models_too=False in a quiet state: the candidate on disk belongs to the
    ended episode, so copying it under TODAY's stamp would forge history —
    only the VAAC state (e.g. the terminating bulletin) is archived."""
    base = os.path.join(REPO, "archive", slug)   # repo root: archive data, NOT website files
    mdir, vdir = os.path.join(base, "models"), os.path.join(base, "vaac")
    os.makedirs(mdir, exist_ok=True)
    os.makedirs(vdir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%MZ")
    cand = os.path.join(SITE, "data", slug, "forecast_candidate.json")
    if models_too and os.path.exists(cand):
        shutil.copy(cand, os.path.join(mdir, f"model-{stamp}.json"))
    if not models_too:
        print("[build] archive: model snapshot skipped (quiet); VAAC state still archived")
    vstate = {"t": stamp, "state": vaac.get("state"),
              "advisory_nr": (vaac.get("advisory") or {}).get("advisory_nr"),
              "dtg_utc": (vaac.get("advisory") or {}).get("dtg_utc"),
              "observed_layers": (vaac.get("advisory") or {}).get("observed_layers"),
              "remarks": (vaac.get("advisory") or {}).get("remarks")}
    json.dump(vstate, open(os.path.join(vdir, f"vaac-{stamp}.json"), "w",
                           encoding="utf-8"), ensure_ascii=False, indent=1)
    gfx = (vaac.get("advisory") or {}).get("graphic_url")
    nr = ((vaac.get("advisory") or {}).get("advisory_nr") or "unknown").replace("/", "-")
    if gfx:
        b = http(gfx, 40, 2)
        if b:
            open(os.path.join(vdir, f"graphic-{nr}.png"), "wb").write(b)
    # index for humans and machines
    models = sorted(f for f in os.listdir(mdir) if f.endswith(".json"))[-60:]
    for f in sorted(os.listdir(mdir)):
        if f.endswith(".json") and f not in models:
            os.remove(os.path.join(mdir, f))
    vstates = sorted(f for f in os.listdir(vdir) if f.startswith("vaac-"))[-40:]
    for f in sorted(os.listdir(vdir)):
        if f.startswith("vaac-") and f not in vstates:
            os.remove(os.path.join(vdir, f))
    gfxs = sorted(f for f in os.listdir(vdir) if f.startswith("graphic-"))[-30:]
    for f in sorted(os.listdir(vdir)):
        if f.startswith("graphic-") and f not in gfxs:
            os.remove(os.path.join(vdir, f))
    json.dump({"updated": stamp,
               "models": [f"models/{f}" for f in models],
               "vaac_states": [f"vaac/{f}" for f in vstates],
               "vaac_graphics": [f"vaac/{f}" for f in gfxs]},
              open(os.path.join(base, "index.json"), "w", encoding="utf-8"), indent=1)
    print(f"[build] archived: model-{stamp}, vaac-{stamp}, graphics={len(gfxs)}")


def dual_union(cand: dict, top_km):
    """v2.3: two combined envelopes instead of one monolithic hull.

    Returns (union_top, union_all):
      union_top — hull of the bands at/below the OFFICIAL cloud top
        (+0.5 km grace, the same rule as the table's relevance star):
        the shape comparable to what Darwin actually draws, because VAAC
        only draws layers where ash is observed/forecast.
        None when there is no official top, or when nothing gets filtered
        (high cloud top / quiet day) — then one hull tells the whole story.
      union_all — the model's own hull over every band 0-16 km, i.e. the
        worst-case view. The site only carries it when it differs from
        union_top, so visitors never see two identical polygons.
    """
    union_all = cand.get("envelope_union")
    envs = cand.get("envelopes") or {}
    if union_envelope is None or not envs:
        return None, union_all
    if union_all is None:
        # older cached model runs predate the union field — rebuild it from
        # the per-band envelopes so the worst-case hull is never lost
        union_all = union_envelope(envs)
    if top_km is None:
        return None, union_all

    def _alt_km(row):
        a = row.get("alt_km") or row.get("mean_altitude_km")
        if a:
            return float(a)
        pr = row.get("pressure_range") or [750, 900]
        return 44.3308 * (1 - (((pr[0] + pr[1]) / 2) / 1013.25) ** 0.190284)

    keep = {r["layer"]: envs[r["layer"]]
            for r in cand.get("observed_wind_profile", [])
            if r.get("layer") in envs and _alt_km(r) <= float(top_km) + 0.5}
    if not keep or set(keep) == set(envs):
        return None, union_all
    return union_envelope(keep), union_all


def build(args) -> int:
    embed = {}
    # ---- resolve the target volcano from the registry (src/volcanoes.py) ----
    volc = volcanoes.resolve(args.volcano or volcanoes.primary()["name"])
    args.volcano = volc["name"]
    if args.lat is None:
        args.lat = volc["lat"]
    if args.lon is None:
        args.lon = volc["lon"]
    slug = volc["slug"]
    DD = os.path.join(SITE, "data", slug)      # site/data/<slug>/
    os.makedirs(DD, exist_ok=True)
    os.makedirs(os.path.join(SITE, "assets"), exist_ok=True)
    write_volcanoes_index()
    # per-volcano imagery framing (module-level constants, set per run)
    global SAT_BOX, LOOP_BOX, LOOP_CROP
    SAT_BOX, LOOP_BOX, LOOP_CROP = framing_for(volc)
    now = datetime.now(timezone.utc)

    print("[build] collecting MAGMA / PVMBG ...")
    mon, mon_err = None, None
    if os.environ.get("KRAKATAU_TEST_MAGMA_FAIL"):
        mon_err = RuntimeError("simulated outage")
    else:
        for base in ("https://magma.esdm.go.id", "https://magma.vsi.esdm.go.id"):
            volcano_monitor.BASE = base
            try:
                mon = volcano_monitor.collect(args.volcano, None, with_report=True)
                mon_err = None
                break
            except Exception as e:  # noqa: BLE001
                mon_err = e
                print(f"  [warn] {base} failed: {str(e)[:90]}", file=sys.stderr)
    if mon is None:
        print(f"  [degraded] MAGMA unreachable: {str(mon_err)[:90]} — publishing with last-known gaps labelled", file=sys.stderr)
        mon = {"volcano": args.volcano, "code": volc.get("magma_code"),
               "province": volc.get("region"),
               "level": None, "level_name": None, "generated_utc": None,
               "recent_eruptions": [], "latest_vona": [], "latest_report": {},
               "indonesia_level_counts": None, "error": str(mon_err)[:200]}

    print("[build] collecting Darwin VAAC ...")
    if os.environ.get("KRAKATAU_TEST_VAAC_FAIL"):
        vaac = {"state": "error", "error": "simulated outage", "advisory": None}
    else:
        try:
            vaac = darwin_vaac.fetch(args.volcano)
        except Exception as e:  # noqa: BLE001
            print(f"  [degraded] Darwin VAAC unreachable: {str(e)[:90]}", file=sys.stderr)
            vaac = {"state": "error", "error": str(e)[:200], "advisory": None}

    # ---- snapshot ---------------------------------------------------------
    rep = mon.get("latest_report") or {}
    seismo_asset = None
    if rep.get("seismogram_image"):
        b = http(rep["seismogram_image"], 40, 2)
        if b:
            p = os.path.join(SITE, "assets", "seismogram.png")
            open(p, "wb").write(b)
            seismo_asset = "assets/seismogram.png"

    adv = vaac.get("advisory") or {}
    graphic_asset = None
    if adv.get("graphic_url"):
        b = http(adv["graphic_url"], 40, 2)
        if b:
            p = os.path.join(SITE, "assets", "vaac_graphic.png")
            open(p, "wb").write(b)
            graphic_asset = "assets/vaac_graphic.png"

    snapshot_vona = []
    eruptions = []
    for e in mon.get("recent_eruptions", [])[:12]:
        eruptions.append({
            "time_label_wib": e.get("time_label"),
            "utc": e.get("utc"),
            "wib": wib_human(e.get("utc")),
            "text": e.get("text"),
            "url": e.get("url"),
        })

    vona = []
    for v in mon.get("latest_vona", [])[:8]:
        if not v.get("issued_utc"):
            continue
        e = {"code": v.get("code"), "issued_utc": v.get("issued_utc"),
             "wib": wib_human(v.get("issued_utc")), "text": v.get("text"),
             "url": v.get("url"), "ash_top_m": v.get("ash_top_m"),
             "ash_top_ft": v.get("ash_top_ft")}
        vona.append(e)
        snapshot_vona.append(e)

    # ---- activity state: live episode vs NORMAL (the 2026-09-12 lesson) ----
    # Read the PREVIOUS snapshot (before it is overwritten below) for
    # hysteresis + since_utc carry-forward. Rules agreed with the maintainer:
    # no real-time data from MAGMA *and* Darwin VAAC => NORMAL (never a
    # silent fallback to old data); an explicit VAAC "ADVISORY TERMINATED"
    # bulletin closes the episode authoritatively; an unreachable source can
    # never vote for normal (absence of data != absence of ash, 2026-09-08).
    prev_activity = None
    prev_snap_path = os.path.join(DD, "snapshot.json")
    if os.path.exists(prev_snap_path):
        try:
            prev_activity = (json.load(open(prev_snap_path, encoding="utf-8"))
                             or {}).get("activity")
        except Exception:
            prev_activity = None
    activity = ACT.assess_activity(
        vaac, snapshot_vona, eruptions, now=now,
        previous=prev_activity, magma_ok=not mon.get("error"))
    print(f"[build] activity: {activity['state']} ({activity['reason_code']})")
    # The verdict lands in snapshot["activity"] below; the scheduler side
    # reads that field to decide whether the 6-hourly model runs dispatch.

    def layer_json(ly):
        return {"base": ly.get("base"), "top": ly.get("top"),
                "top_km": ly.get("top_km"),
                "top_human_id": fl_human(ly.get("top"), "id"),
                "top_human_en": fl_human(ly.get("top"), "en"),
                "base_human_id": fl_human(ly.get("base"), "id"),
                "base_human_en": fl_human(ly.get("base"), "en"),
                "move_toward": ly.get("move_toward"),
                "speed_kt": ly.get("speed_kt"), "speed_ms": ly.get("speed_ms"),
                "polygon": [[pt["lon"], pt["lat"]] if isinstance(pt, dict) else list(pt)
                            for pt in (ly.get("polygon") or [])]}

    vaac_json = {
        "state": vaac.get("state"),
        "advisory_nr": adv.get("advisory_nr"),
        "dtg_utc": adv.get("dtg_utc"),
        "dtg_wib": wib_human(adv.get("dtg_utc")),
        "age_hours": vaac.get("age_hours"),
        "is_current": vaac.get("is_current"),
        # episode end in the VAAC's own words (2026/217: ADVISORY TERMINATED)
        "advisory_status": vaac.get("advisory_status") or adv.get("episode_status"),
        "terminated": bool(vaac.get("terminated") or adv.get("terminated")),
        "eruption_details": adv.get("eruption_details"),
        "info_source": adv.get("info_source"),
        "observed_layers": [layer_json(l) for l in adv.get("observed_layers", [])],
        "forecasts": {k: {"valid_utc": v.get("valid_utc"),
                          "valid_wib": wib_human(v.get("valid_utc")),
                          "layers": [layer_json(l) for l in v.get("layers", [])]}
                      for k, v in (adv.get("forecast") or {}).items()},
        "remarks": adv.get("remarks"),
        "next_advisory_by_utc": adv.get("next_advisory_by_utc"),
        "next_advisory_by_wib": wib_human(adv.get("next_advisory_by_utc")),
        "graphic_asset": graphic_asset,
        "bulletin_text": adv.get("raw"),
        "source_url": vaac.get("source_url"),
        "data_url": vaac.get("data_url"),
        "via": vaac.get("via"),
        "warning": vaac.get("warning"),
    }

    def _gap_fraction(path):
        """Fraction of near-black pixels in the centre: MODIS swath gaps show up
        as a black blob right where the strait is (Aqua, some days)."""
        try:
            from PIL import Image
            im = Image.open(path).convert("L")
            w, h = im.size
            c = im.crop((int(w * .3), int(h * .3), int(w * .7), int(h * .7)))
            px = list(c.getdata())
            return sum(1 for v in px if v < 12) / len(px)
        except Exception:
            return 0.0

    sat = {}
    if args.no_sat:
        old = os.path.join(DD, "snapshot.json")
        if os.path.exists(old):
            try:
                sat = (json.load(open(old, encoding="utf-8")) or {}).get("satellite") or {}
            except Exception:
                sat = {}
    if not args.no_sat:
        print("[build] stitching NASA GIBS daily imagery ...")
        date = latest_gibs_date(args.lat, args.lon)
        # slot 1: Suomi VIIRS (wide swath, rarely gapped)
        out = os.path.join(SITE, "assets", "sat_snpp.jpg")
        r = stitch_gibs("snpp", date, out)
        if r:
            sat["snpp"] = r
            print(f"  snpp: {r['asset']} ({date}, {r['missing_tiles']} missing tiles)")
        # slot 2: cleanest of Aqua / Terra — MODIS orbits leave black swath
        # gaps over the strait on some days; measure, and if every pass today
        # is gapped, look back up to 2 days for a clean one (labelled honestly).
        chosen = None
        for doff in (0, 1, 2):
            d2 = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=doff)).strftime("%Y-%m-%d")
            cands = []
            for sensor in ("aqua", "terra"):
                tmp = os.path.join(SITE, "assets", f"sat_{sensor}.jpg")
                rr = stitch_gibs(sensor, d2, tmp)
                if rr:
                    cands.append((sensor, rr, _gap_fraction(tmp)))
            if not cands:
                continue
            sensor, rr, gap = min(cands, key=lambda t: t[2])
            if gap <= 0.10 or doff == 2:
                chosen = (d2, sensor, rr, gap)
                break
        if chosen:
            d2, sensor, rr, gap = chosen
            final = os.path.join(SITE, "assets", "sat_modis.jpg")
            os.replace(os.path.join(SITE, "assets", f"sat_{sensor}.jpg"), final)
            for other in ("aqua", "terra"):
                pth = os.path.join(SITE, "assets", f"sat_{other}.jpg")
                if os.path.exists(pth):
                    os.remove(pth)
            rr = dict(rr, asset="assets/sat_modis.jpg", date=d2,
                      sensor_label={"aqua": "Aqua (MODIS)", "terra": "Terra (MODIS)"}[sensor])
            if gap > 0.02:
                rr["sensor_note"] = f"Orbit gap visible ({sensor}, {d2}); cleanest available pass shown"
            if d2 != date:
                rr["sensor_note"] = (rr.get("sensor_note", "") + " " +
                                     f"No clean pass on {date}; showing {d2}.").strip()
            sat["modis"] = rr
            print(f"  modis: {sensor} {d2} chosen (gap {gap:.1%})")

    loop = None
    if not args.no_loop:
        print("[build] assembling Himawari-9 IR loop (10-min frames) ...")
        loop = build_loop(os.path.join(SITE, "assets", "loop"), args.loop_frames)
        if loop:
            print(f"  loop: {len(loop['frames'])} frames "
                  f"({loop['frames'][0]['t_wib']} -> {loop['frames'][-1]['t_wib']})")

    source_errors = {}
    if mon.get("error"):
        source_errors["magma"] = mon["error"]
    if vaac.get("state") == "error":
        source_errors["darwin_vaac"] = vaac.get("error")

    snapshot = {
        "schema_version": 1,
        "source_errors": source_errors,
        "generated_utc": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "generated_wib": wib_human(now.isoformat()),
        "volcano": {"name": mon.get("volcano"), "code": mon.get("code"),
                    "province": mon.get("province"), "lat": args.lat, "lon": args.lon},
        "status": {"level": mon.get("level"), "level_name": mon.get("level_name"),
                   "source": "MAGMA Indonesia/PVMBG",
                   "source_url": "https://magma.esdm.go.id/v1/gunung-api/tingkat-aktivitas",
                   "fetched_utc": mon.get("generated_utc"),
                   "fetched_wib": wib_human(mon.get("generated_utc")),
                   "indonesia_counts": mon.get("indonesia_level_counts")},
        "activity": activity,
        "report": {"period": rep.get("period"), "author": rep.get("author"),
                   "visual": rep.get("visual"), "climate": rep.get("climate"),
                   "seismic_counts": rep.get("seismic_counts"),
                   "recommendation": rep.get("recommendation"),
                   "seismogram_asset": seismo_asset,
                   "source": "MAGMA Indonesia/PVMBG",
                   "source_url": "https://magma.esdm.go.id/v1/gunung-api/laporan",
                   "fetched_wib": wib_human(mon.get("generated_utc"))},
        "eruptions": eruptions,
        "vona": vona,
        "vaac": vaac_json,
        "satellite": sat,
        "loop": loop,
        "official_links": {
            "magma": "https://magma.esdm.go.id",
            "pvmbg": "https://geologi.esdm.go.id",
            "bnpb": "https://www.bnpb.go.id",
            "darwin_vaac": "https://www.bom.gov.au/aviation/volcanic-ash/darwin-va-advisory.shtml",
            # per-volcano regional links from the registry (BPBD etc.)
            **(volc.get("links") or {}),
        },
    }
    sp = os.path.join(DD, "snapshot.json")
    json.dump(snapshot, open(sp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[build] {sp} ({os.path.getsize(sp)/1024:.0f} KB)")
    embed["snapshot"] = snapshot

    # ---- forecast model (candidate; publish only with a human) -----------
    import subprocess
    cand = None
    if activity["state"] == "quiet":
        # Graceful stop, part 1 (build-side): with no live episode there is
        # nothing to model. The last computed candidate/model stays on disk
        # as the archive of the ended episode; the site labels it as such.
        # Part 2 (infra-side): the committed snapshot's activity.state is
        # what pg_cron's sync job reads every 5 min to pause the 6-hourly
        # dispatch (and a fresh advisory/VONA resumes it).
        print("[build] quiet state — secondary model SKIPPED "
              "(no ash episode in progress)")
    else:
        print("[build] computing secondary model (Himawari-9 + open-meteo) ...")
        try:
            # v2.1: seed the model with the advisory's OBS polygon so the cloud
            # age (emission history) is derived from the observed width instead
            # of silently assuming fresh ash at the analysis time
            obs_args = obs_polygon_args(vaac)
            if obs_args:
                print("[build] seeding model with Darwin OBS polygon "
                      "(emission-history inversion enabled)")
            r = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "ash_transport.py"),
                                "--json", "--no-svg",
                                "--lat", str(args.lat), "--lon", str(args.lon),
                                "--name", volc["name"],
                                "--slots", str(args.slots), "--hours", "12"]
                               + (obs_args or []),
                               capture_output=True, text=True, timeout=args.ash_timeout)
            if r.returncode == 0:
                cand = json.loads(r.stdout)
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] model: {e}", file=sys.stderr)

    if cand:
        verdict = V.validate(mon, vaac, cand.get("observed_wind_profile"),
                             {"levels": {int(k): x for k, x in cand.get("forecast_wind", {}).items()},
                              "times": []},
                             firms=cand.get("firms"), volcano=args.volcano,
                             ash_model=cand)
        # Official plume-top height (Darwin VAAC observed cloud top), if any.
        # This is what decides WHICH model layers matter today: on 2026-09-08 the
        # top was ~2.1 km (low layers steer the ash); on 2026-09-05 it was
        # ~15 km (FL500), when the upper layers mattered instead.
        plume_top = None
        obs_layers = (vaac.get("advisory") or {}).get("observed_layers") or []
        if vaac.get("state") == "advisory" and obs_layers:
            top_layer = max(obs_layers, key=lambda l: l.get("top_km") or 0)
            if top_layer.get("top_km"):
                plume_top = {"km": top_layer["top_km"], "fl": top_layer.get("top"),
                             "human_id": fl_human(top_layer.get("top"), "id"),
                             "human_en": fl_human(top_layer.get("top"), "en"),
                             "source": "Darwin VAAC observed cloud top"}
        if plume_top is None:
            # VONA fallback: MAGMA's own ash-top estimate keeps the height anchor
            # alive on nil/stale VAAC days (the 2026-09-08 failure mode) — but
            # ONLY while fresh. The 2026-09-12 mirror-image failure: no fresh
            # VONA for 19 days, and this loop happily quoted the 24-Aug 557 m
            # estimate as the OFFICIAL top "today". activity_state.vona_plume_top
            # age-gates the anchor (<= 24 h) and the quiet state suppresses it
            # entirely — normal is normal, not "the eruption's memory, daily".
            v = (ACT.vona_plume_top(snapshot_vona, now=now)
                 if activity["state"] == "active" else None)
            if v and v.get("ash_top_m"):
                km = round(v["ash_top_m"] / 1000.0, 2)
                plume_top = {"km": km, "fl": f"{v['ash_top_m']} M",
                             "human_id": f"≈ {km:.1f} km dpl",
                             "human_en": f"≈ {km:.1f} km asl",
                             "source": "VONA/MAGMA ash-top estimate",
                             "issued_wib": v.get("wib")}
            elif any(x.get("ash_top_m") for x in (snapshot_vona or [])):
                print(f"[build] VONA ash-top anchor SKIPPED: newest VONA with an "
                      f"ash-top is {activity['evidence'].get('vona_age_hours')} h old "
                      f"(> {ACT.VONA_FRESH_H:.0f} h) — no official top today")
        if plume_top is None and activity["state"] == "active":
            # last resort, clearly labelled: the highest well-consistent AMV
            # cloud band near the vent. Cloud top, NOT confirmed ash — the label
            # says so, and a caveat repeats it in plain language.
            # (reads the model's wind profile directly: the display `layers`
            # list is only built further down, and this branch must work on
            # nil-advisory + no-VONA days too — the case that once crashed here.
            # Quiet state: skipped — a speculative cloud estimate must never
            # stand in for an ash top when there is no episode at all.)
            cands = [r for r in cand.get("observed_wind_profile", [])
                     if r.get("data") and r.get("consistency_R") is not None
                     and r["consistency_R"] >= 0.7 and (r.get("n_eff") or 0) >= 5
                     and (r.get("nearest_vector_km") or 999) <= 250]
            if cands:
                top = max(cands, key=lambda r: r["mean_altitude_km"])
                plume_top = {"km": top["mean_altitude_km"], "fl": None,
                             "human_id": f"≈ {top['mean_altitude_km']:.1f} km dpl (estimasi awan)",
                             "human_en": f"≈ {top['mean_altitude_km']:.1f} km asl (cloud estimate)",
                             "source": "AMV cloud-top estimate (speculative, not confirmed ash)"}

        def _caveats(layers_list, plume_top, vaac, cand, verdict):
            """The human report's honesty, mirrored for machine consumers."""
            out = []
            nearest = [l.get("nearest_vector_km") for l in layers_list
                       if l.get("nearest_vector_km") and l.get("relevant_today")]
            if nearest and min(nearest) > 150:
                out.append({
                    "id": f"Tidak ada vektor angin satelit dalam {int(min(nearest))} km dari kawah; ini aliran REGIONAL, bukan pengukuran di kawah.",
                    "en": f"No satellite wind vector within {int(min(nearest))} km of the vent; this is REGIONAL flow, not a crater measurement.",
                    "plain_id": "Angin satelit terdekat berjarak ratusan kilometer dari kawah—ini gambaran wilayah luas, bukan titik persis.",
                    "plain_en": "The nearest satellite winds are hundreds of kilometres from the crater—a wide-area picture, not a pinpoint."})
            lowR = [l for l in layers_list if l.get("consistency_R") is not None
                    and l["consistency_R"] < 0.7 and l.get("relevant_today")]
            if lowR:
                out.append({
                    "id": "Vektor satelit pada lapisan relevan tidak saling sepakat (R<0.7); arah lapisan tersebut tidak pasti.",
                    "en": "Satellite vectors in a relevant layer disagree (R<0.7); that layer's direction is uncertain.",
                    "plain_id": "Untuk lapisan ini pengukuran satelit saling bertentangan—arahnya belum pasti.",
                    "plain_en": "For this layer the satellite measurements disagree—its direction is not yet certain."})
            agg = (verdict or {}).get("direction_corroboration", {}).get("agreement")
            if agg == "divergent":
                out.append({
                    "id": "Sumber-sumber berbeda arah 45-90° (divergen): bukan koroborasi, bukan pula konflik tegas.",
                    "en": "Sources diverge by 45-90 degrees: neither corroborated nor squarely conflicting.",
                    "plain_id": "Sumber resmi dan perhitungan satelit tidak sepenuhnya sepakat hari ini.",
                    "plain_en": "Official and satellite sources do not fully agree today."})
            if agg == "single_source":
                out.append({
                    "id": "Hanya satu sumber berbicara per lapisan; belum ada koroborasi independen.",
                    "en": "Only one source speaks per layer; no independent corroboration yet.",
                    "plain_id": "Baru satu sumber yang berbicara untuk lapisan ini.",
                    "plain_en": "Only one source speaks for this layer."})
            wet = [l for l in layers_list if any(
                (v.get("wet_points") or [])
                for v in (l.get("settling_classes") or {}).values())]
            if wet:
                out.append({
                    "id": "Sebagian lintasan melewati sel hujan (open-meteo): deposisi basah diterapkan (Λ=1e-4/s per mm/h); titik biru = perpotongan hujan → interpretasi risiko ashfall.",
                    "en": "Part of the trajectory crosses rain cells (open-meteo): wet deposition applied (Λ=1e-4/s per mm/h); blue dots = rain crossings → ashfall-risk interpretation.",
                    "plain_id": "Sebagian lintasan melewati hujan—sebagian abu bisa jatuh lebih dulu di sana.",
                    "plain_en": "Part of the path crosses rain—some ash may fall out there first."})
            out.append({
                "id": "Kecepatan endapan dikoreksi kepadatan udara v(h)=v0·√(ρ0/ρ(h)); sebaran memakai σ²=σ0²+2Kt+(geser·t)² yang mengikuti massa airborne Φ(t): ambang deteksi ikut menipis saat awan menyebar, sehingga lebar bisa naik lalu turun.",
                "en": "Settling velocity density-corrected v(h)=v0·√(ρ0/ρ(h)); spread uses σ²=σ0²+2Kt+(shear·t)² coupled to the airborne mass Φ(t): the detection threshold dilutes as the cloud spreads, so the width can rise and then fall.",
                    "plain_id": "Perhitungan memakai abu yang jatuh perlahan, menyebar, dan angin yang berubah theo ketinggian—dengan ketidakpastian yang jujur.",
                    "plain_en": "The calculation accounts for ash settling slowly, spreading, and wind changing with height—with honest uncertainty."})
            out.append({
                "id": "ECMWF/GFS/ICON beresolusi ~9-25 km: sirkulasi lokal mesoscale (angin laut/darat, topografi) tidak tertangkap.",
                "en": "ECMWF/GFS/ICON run at ~9-25 km grids: local mesoscale circulations (sea/land breeze, terrain flows) are not resolved.",
                    "plain_id": "Angin lokal (pantai, lembah, gunung) terlalu kecil untuk terlihat model cuaca global.",
                    "plain_en": "Local winds (coastal, valley, mountain) are too small for global weather models to see."})
            spreads = [l.get("ensemble_spread_deg") for l in layers_list
                       if l.get("ensemble_spread_deg") and l["ensemble_spread_deg"] > 30]
            if spreads:
                out.append({
                    "id": f"Model cuaca (ECMWF/GFS/ICON) saling berbeda hingga {int(max(spreads))}° pada sebagian lapisan.",
                    "en": f"NWP models (ECMWF/GFS/ICON) disagree by up to {int(max(spreads))} degrees on some layers.",
                    "plain_id": "Model cuaca saling berbeda cukup jauh di sebagian ketinggian.",
                    "plain_en": "The weather models differ noticeably at some heights."})
            if vaac.get("state") != "advisory":
                if plume_top and "VONA" in (plume_top.get("source") or ""):
                    out.append({
                        "id": "Darwin VAAC nihil/kedaluwarsa; tinggi puncak memakai estimasi VONA/MAGMA.",
                        "en": "Darwin VAAC nil/stale; cloud-top height uses the VONA/MAGMA estimate.",
                    "plain_id": "Tanpa advisori Darwin hari ini, tinggi awan memakai laporan MAGMA.",
                    "plain_en": "With no Darwin advisory today, cloud height uses MAGMA's report."})
                else:
                    out.append({
                        "id": "Tidak ada tinggi puncak awan abu resmi hari ini; relevansi lapisan tidak ditandai.",
                        "en": "No official ash-cloud top today; layer relevance is unflagged.",
                    "plain_id": "Tidak ada tinggi awan abu resmi hari ini.",
                    "plain_en": "There is no official ash-cloud height today."})
            if plume_top and "speculative" in (plume_top.get("source") or ""):
                out.append({
                    "id": "Tinggi puncak hari ini adalah ESTIMASI awan dari satelit angin, bukan abu terkonfirmasi.",
                    "en": "Today's cloud top is a speculative estimate from wind-satellite clouds, not confirmed ash.",
                    "plain_id": "Tinggi awan hari ini perkiraan dari satelit, bukan abu yang dipastikan.",
                    "plain_en": "Today's cloud height is a satellite estimate, not confirmed ash."})
            if not (cand or {}).get("firms"):
                out.append({
                    "id": "Tanpa kunci FIRMS: tidak ada uji-silak hotspot independen untuk 'erupsi berlangsung'.",
                    "en": "No FIRMS key set: no independent hotspot cross-check for 'eruption ongoing'.",
                    "plain_id": "Belum ada pemeriksaan silang hotspot NASA hari ini.",
                    "plain_en": "No NASA hotspot cross-check today."})
            out.append({
                "id": "Lintasan memakai angin prakiraan per jam + pengendapan 3 kelas abu + difusi; varian angin-tetap ikut disertakan.",
                "en": "Trajectories use hourly-evolving forecast wind + 3 settling classes + diffusion; a steady-wind variant ships alongside.",
                    "plain_id": "Garis pergerakan adalah prakiraan, bukan jaminan.",
                    "plain_en": "The tracks are a forecast, not a promise."})
            return out

        def _plume_vector(layers_list, h_target):
            pts = sorted(((l["alt_km"], l["blended_u_ms"], l["blended_v_ms"],
                           l.get("uncertainty_deg") or 30)
                          for l in layers_list
                          if l.get("blended_u_ms") is not None and l.get("alt_km")))
            if not pts or h_target is None:
                return None
            if h_target <= pts[0][0]:
                u, v, unc = pts[0][1], pts[0][2], pts[0][3]
            elif h_target >= pts[-1][0]:
                u, v, unc = pts[-1][1], pts[-1][2], pts[-1][3]
            else:
                for a, b in zip(pts, pts[1:]):
                    if a[0] <= h_target <= b[0]:
                        w = (h_target - a[0]) / ((b[0] - a[0]) or 1)
                        u = a[1] * (1 - w) + b[1] * w
                        v = a[2] * (1 - w) + b[2] * w
                        unc = max(a[3], b[3])
                        break
                else:
                    u, v, unc = pts[-1][1], pts[-1][2], pts[-1][3]
            return {"toward_deg": round(float(math.degrees(math.atan2(u, v)) % 360), 1),
                    "speed_ms": round(float(math.hypot(u, v)), 1),
                    "uncertainty_deg": round(float(unc), 1),
                    "at_km": h_target}

        layers = []
        for row in cand.get("observed_wind_profile", []):
            if not row.get("data"):
                # show the gap instead of hiding it: visitors see WHICH layers
                # had no satellite coverage this run, not a silently shorter table
                tr_fc = (cand.get("trajectories_forecast") or {}).get(row["layer"], [])
                p_mid = (row.get("pressure_range") or [750, 900])[0:2]
                nom = round(44.3308 * (1 - (((p_mid[0] + p_mid[1]) / 2) / 1013.25) ** 0.190284), 1)
                layers.append({"layer": row["layer"], "data": False,
                               "n": row.get("n", 0), "nom_alt_km": nom,
                               "note_id": "tidak ada vektor satelit pada slot ini; garis = model cuaca saja",
                               "note_en": "no satellite vectors this slot; line = weather model only",
                               "trajectory_kind": "nwp-only" if tr_fc else None,
                               "trajectory": [[q["lat"], q["lon"], q["hours"]] for q in tr_fc],
                               "envelope": (cand.get("envelopes") or {}).get(row["layer"]),
                               "settling_classes": {
                                   c: {"pts": [[q["lat"], q["lon"], q["hours"], q["alt_km"]] for q in v["pts"]],
                                       "mass_remaining": v.get("mass_remaining"),
                                       "wet_points": v.get("wet_points", [])}
                                   for c, v in ((cand.get("trajectories_settling") or {})
                                                .get(row["layer"], {}) or {}).items()
                                   if isinstance(v, dict) and "pts" in v}})
                continue
            tr_steady = (cand.get("trajectories_observed") or {}).get(row["layer"], [])
            tr_fc = (cand.get("trajectories_forecast") or {}).get(row["layer"], [])
            layers.append({
                "layer": row["layer"],
                "data": True,
                "alt_km": row["mean_altitude_km"],
                "alt_human_id": f"± {row['mean_altitude_km']:.1f} km dpl",
                "alt_human_en": f"± {row['mean_altitude_km']:.1f} km asl",
                "from_deg": row["from_deg"], "from_compass": row["from_compass"],
                "toward_deg": row["toward_deg"], "toward_compass": row["toward_compass"],
                "speed_ms": row["speed_ms"],
                "consistency_R": row["consistency_R"],
                "n_vectors": row["n"],
                "nearest_vector_km": row.get("nearest_vector_km"),
                "confidence": row.get("confidence"),
                # displayed path = hourly-evolving forecast wind when available;
                # steady-wind variant kept for transparency and fallback
                "uncertainty_deg": row.get("uncertainty_deg"),
                "ensemble_spread_deg": row.get("ensemble_spread_deg"),
                "n_eff": row.get("n_eff"),
                "blended_toward_deg": row.get("blended_toward_deg"),
                "blended_u_ms": row.get("blended_u_ms"),
                "blended_v_ms": row.get("blended_v_ms"),
                "trajectory": [[p["lat"], p["lon"], p["hours"]] for p in (tr_fc or tr_steady)],
                "trajectory_kind": "forecast-evolving" if tr_fc else "steady-wind",
                "trajectory_steady": [[p["lat"], p["lon"], p["hours"]] for p in tr_steady],
                "settling_classes": {
                    c: {"pts": [[q["lat"], q["lon"], q["hours"], q["alt_km"]] for q in v["pts"]],
                        "mass_remaining": v.get("mass_remaining"),
                        "wet_points": v.get("wet_points", [])}
                    for c, v in ((cand.get("trajectories_settling") or {})
                                 .get(row["layer"], {}) or {}).items()
                    if isinstance(v, dict) and "pts" in v},
                "envelope": (cand.get("envelopes") or {}).get(row["layer"]),
                "relevant_today": bool(plume_top and
                                       row["mean_altitude_km"] <= plume_top["km"] + 0.5),
            })
        # v2.3: dual combined envelope — "at/below the official top" (the
        # VAAC-comparable shape) plus the all-bands worst case, so the map
        # can show both under separate toggles instead of one hull that
        # covers layers where there is provably no ash today
        union_top, union_all = dual_union(cand, plume_top["km"] if plume_top else None)
        model = {
            "schema_version": 1,
            "status": "candidate",
            "computed_utc": cand.get("analysis_utc"),
            "computed_wib": wib_human(cand.get("analysis_utc")),
            "validation": {"hard_failures": len(verdict["hard_failures"]),
                           "direction_agreement": verdict["direction_corroboration"].get("agreement"),
                           "worst_disagreement_deg": verdict["direction_corroboration"].get("worst_disagreement_deg"),
                           "occurrence_sources": verdict["occurrence_corroboration"]["sources"],
                           "content_hash": verdict["content_hash"]},
            "layers": layers,
            "caveats": _caveats(layers, plume_top, vaac, cand, verdict),
            "plume_vector": _plume_vector(layers, plume_top["km"] if plume_top else None),
            "vaac_motion": (lambda mv: {"compass": mv, "deg": COMPASS_DEG.get(mv)}
                            if (vaac.get("state") == "advisory"
                                and (vaac.get("advisory") or {}).get("observed_layers")
                                and (vaac["advisory"]["observed_layers"][0].get("move_toward")))
                            else None)(
                (vaac.get("advisory") or {}).get("observed_layers", [{}])[0].get("move_toward")
                if (vaac.get("advisory") or {}).get("observed_layers") else None),
            "backtest": None,
            "plume_top": plume_top,
            # v2.1 envelope outputs — union hull + emission history, so the
            # site (and machine consumers) see the same fields the model
            # reports on its own JSON path
            "envelope_model": cand.get("envelope_model"),
            "envelope_emission": cand.get("envelope_emission"),
            "envelope_union": union_top or union_all,
            "envelope_union_all": union_all if union_top else None,
            "envelope_union_rule": ("bands <= official top + 0.5 km"
                                    if union_top else "all bands"),
            "sources": ["Himawari-9 AMV (NOAA S3, JMA product)", "open-meteo pressure-level winds"],
        }
        cp = os.path.join(DD, "forecast_candidate.json")
        json.dump(model, open(cp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"[build] {cp} ({len(layers)} layers)")

        if args.approve_forecast:
            if verdict["hard_failures"]:
                print("[build] REFUSED to publish model: validation hard failures present")
                for c in verdict["hard_failures"]:
                    print("   FAIL", c["gate"], c["name"], c["detail"])
                return 2
            model["status"] = "approved"
            model["approved_by"] = args.approver or os.environ.get("USER") or "operator"
            model["approved_utc"] = now.isoformat(timespec="seconds").replace("+00:00", "Z")
            model["approved_wib"] = wib_human(now.isoformat())
            mp = os.path.join(DD, "forecast_model.json")
            json.dump(model, open(mp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print(f"[build] MODEL PUBLISHED by {model['approved_by']} -> {mp}")
    else:
        print("[build] no model computed" +
              (" (quiet state)" if activity["state"] == "quiet" else ""))

    # ---- backtest ledger: model direction vs VAAC observed motion, per build ----
    bt_path = os.path.join(DD, "backtest.jsonl")
    rows = []
    if os.path.exists(bt_path):
        for line in open(bt_path, encoding="utf-8"):
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    vaac_motion = None
    if vaac.get("state") == "advisory":
        ols = (vaac.get("advisory") or {}).get("observed_layers") or []
        if ols:
            comp = {"N": 0, "NE": 45, "E": 90, "SE": 135, "S": 180,
                    "SW": 225, "W": 270, "NW": 315}
            vaac_motion = comp.get(ols[0].get("move_toward"))
    model_vec = None
    try:
        model_vec = (json.load(open(os.path.join(DD, "forecast_candidate.json"),
                                    encoding="utf-8")) or {}).get("plume_vector")
    except Exception:
        model_vec = None
    stamp_now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:00Z")
    if model_vec and activity["state"] == "active" \
            and not any(r.get("t") == stamp_now for r in rows):
        rows.append({"t": stamp_now,
                     "model_toward": model_vec.get("toward_deg"),
                     "model_unc": model_vec.get("uncertainty_deg"),
                     "vaac_toward": vaac_motion,
                     # v2.1: width bookkeeping (OBS width, inferred emission
                     # age, VAAC FCST widths vs model widths) — the dataset
                     # future envelope re-fits will be driven from
                     **width_ledger_entry(vaac, cand)})
        with open(bt_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rows[-1]) + "\n")
    pairs = [r for r in rows if r.get("model_toward") is not None
             and r.get("vaac_toward") is not None]
    bt_stats = None
    if pairs:
        errs = [abs((r["model_toward"] - r["vaac_toward"] + 180) % 360 - 180) for r in pairs]
        bt_stats = {"n": len(pairs), "mean_abs_deg": round(sum(errs) / len(errs), 1),
                    "median_abs_deg": round(sorted(errs)[len(errs) // 2], 1)}
    embed["backtest"] = bt_stats
    embed["validation_hard_failures"] = len(verdict["hard_failures"]) if "verdict" in dir() else 0
    mp = os.path.join(DD, "forecast_model.json")
    if bt_stats and os.path.exists(mp):
        try:
            mj = json.load(open(mp, encoding="utf-8"))
            mj["backtest"] = bt_stats
            json.dump(mj, open(mp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        except Exception:
            pass

    # ---- 6-hourly auto-publish (maintainer decision for the decreasing-activity
    # window): clean validation only, always stamped, always carrying caveats.
    # Quiet state: NO auto-publish — stamping a fresh approval time onto a
    # stale model would manufacture "current" out of history. ----
    if args.auto_publish and activity["state"] != "active":
        print("[build] auto-publish SKIPPED (quiet state — no live episode to model)")
    if args.auto_publish and activity["state"] == "active":
        mp = os.path.join(DD, "forecast_model.json")
        try:
            cand_now = json.load(open(os.path.join(DD, "forecast_candidate.json"),
                                      encoding="utf-8"))
        except Exception:
            cand_now = None
        if cand_now and not verdict_hard_failures(embed):
            cand_now["status"] = "approved"
            cand_now["auto_published"] = True
            cand_now["approved_by"] = "auto:6h (" + (os.environ.get("GITHUB_ACTOR") or "ci") + ")"
            cand_now["approved_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            cand_now["approved_wib"] = wib_human(cand_now["approved_utc"])
            cand_now["backtest"] = embed.get("backtest")
            json.dump(cand_now, open(mp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print("[build] model auto-published by 6h schedule (validation clean)")

    # ---- archive: our finding + Darwin VAAC state + graphical advisory ----
    if args.archive:
        # quiet: keep archiving the VAAC state (the terminated bulletin IS the
        # official end-of-episode record) but do not stamp the stale model
        # snapshot with a fresh archive time.
        archive_run(embed, vaac, slug, models_too=activity["state"] == "active")

    embed_into_index(embed, slug)
    if source_errors and len(source_errors) >= 2:
        print("[build] ALL primary sources unreachable — keeping the previous site "
              "instead of publishing a hollow one.", file=sys.stderr)
        return 1
    if source_errors:
        print(f"[build] done (degraded: {', '.join(source_errors)} labelled on-page).")
    else:
        print("[build] done.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--volcano", default=None,
                    help="volcano name/slug from src/volcanoes.py (default: primary registry entry)")
    ap.add_argument("--lat", type=float, default=None,
                    help="vent latitude override (default: registry)")
    ap.add_argument("--lon", type=float, default=None,
                    help="vent longitude override (default: registry)")
    ap.add_argument("--no-sat", action="store_true")
    ap.add_argument("--no-loop", action="store_true")
    ap.add_argument("--loop-frames", type=int, default=12)
    ap.add_argument("--archive", action="store_true",
                    help="archive this run's model + Darwin VAAC state & graphical advisory")
    ap.add_argument("--auto-publish", action="store_true",
                    help="publish the model without a human if validation is clean "
                         "(6-hourly schedule only; stamped auto_published)")
    ap.add_argument("--slots", type=int, default=3)
    ap.add_argument("--ash-timeout", type=int, default=600)
    ap.add_argument("--approve-forecast", action="store_true",
                    help="human-in-the-loop gate: publish the model section")
    ap.add_argument("--approver", default=None, help="who is approving (your name)")
    a = ap.parse_args()
    sys.exit(build(a))
