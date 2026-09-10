#!/usr/bin/env python3
"""
build_site.py — regenerate the whole static dashboard in one command.

    python3 build_site.py                    # collect + assets + data files
    python3 build_site.py --approve-forecast --approver "Nama Kamu"
                                             # ALSO publish the model section
                                             # (human-in-the-loop gate)

What it produces under site/:
    data/snapshot.json          live official data (MAGMA + Darwin VAAC)
    data/forecast_candidate.json our Himawari/open-meteo model, UNPUBLISHED
    data/forecast_model.json    the same model, only after a human approves
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
# region of interest for the daily picture
SAT_BOX = (100.0, 112.0, -12.0, -1.0)   # lon0 lon1 lat0 lat1
SAT_Z = 7

CREDIT_GIBS = ("NASA GIBS/Earthdata — {sensor} Corrected Reflectance (True Color), "
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


# ------------------------------------------------------------------ tiles
def _tile_idx(lon: float, lat: float, z: int):
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n)
    return x, y


def _trim_black(im, thresh: int = 8):
    """Cut empty swath-edge columns/rows (the black wedge MODIS leaves)."""
    try:
        import numpy as np
        a = np.asarray(im.convert("L"))
        xs = np.where(a.mean(axis=0) > thresh)[0]
        ys = np.where(a.mean(axis=1) > thresh)[0]
        if len(xs) > 10 and len(ys) > 10:
            return im.crop((int(xs[0]), int(ys[0]), int(xs[-1]) + 1, int(ys[-1]) + 1))
    except Exception:
        pass
    return im


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


def latest_gibs_date() -> str:
    """GIBS lags ~1 day; try today then step back."""
    now = datetime.now(timezone.utc)
    for back in range(0, 5):
        d = (now - timedelta(days=back)).strftime("%Y-%m-%d")
        x, y = _tile_idx(105.42, -6.10, SAT_Z)
        if http(GIBS.format(layer=GIBS_LAYERS["snpp"], date=d, z=SAT_Z, row=y, col=x), 25, 1):
            return d
    return (now - timedelta(days=1)).strftime("%Y-%m-%d")


EMBED = {}


def embed_into_index(embed: dict) -> None:
    """Inline the snapshot (+ approved model) into index.html so the page
    renders with zero network (offline copy / sandboxed preview)."""
    ip = os.path.join(SITE, "index.html")
    html = open(ip, encoding="utf-8").read()

    def payload(obj):
        if not obj:
            return ""
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")

    snap = payload(embed.get("snapshot"))
    model = None
    mp = os.path.join(SITE, "data", "forecast_model.json")
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
LOOP_CROP = (101.5, 109.5, -9.5, -3.0)     # lon0 lon1 lat0 lat1 (pixel crop after stitch)
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
    os.makedirs(out_dir, exist_ok=True)
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
        crop = _trim_black(crop)
        crop = ImageOps.autocontrast(crop, cutoff=1)
        if B["upscale"] > 1:
            crop = crop.resize((crop.width * B["upscale"], crop.height * B["upscale"]),
                               Image.LANCZOS)
        fname = f"f{len(frames):02d}.jpg"
        crop.save(os.path.join(out_dir, fname), "JPEG", quality=80, optimize=True)
        frames.append({"t_utc": tstr, "t_wib": wib_human(tstr), "asset": f"assets/loop/{fname}"})
    if len(frames) < 4:
        return None

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

    return {"source": "NASA GIBS / JMA Himawari-9 AHI", "layer": B["layer"],
            "band": band, "band_label": B["label"],
            "interval_min": 10, "frames": frames, "credit": LOOP_CREDIT,
            "roi": list(LOOP_CROP), "zoom": B["z"], "tms": B["tms"],
            "verified": {"ten_minute_grid": grid_ok,
                         "noaa_s3_slot_exists": noaa_ok,
                         "noaa_slot": f"{lt:%Y-%m-%d %H:%M}Z",
                         "verify_url": verify_url},
            "built_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")}


# ------------------------------------------------------------------ build
def verdict_hard_failures(embed) -> bool:
    """The 6h auto-publish gate: refuse unless validation found zero hard fails."""
    vf = embed.get("validation_hard_failures")
    return bool(vf) if vf is not None else False


def archive_run(embed, vaac) -> None:
    """Six-hourly memory: our model, the VAAC state, and the official chart.
    Pruned so the repo stays light: 60 model snapshots, 40 VAAC states,
    30 graphical advisories (one per advisory number)."""
    base = os.path.join(SITE, "data", "archive")
    mdir, vdir = os.path.join(base, "models"), os.path.join(base, "vaac")
    os.makedirs(mdir, exist_ok=True)
    os.makedirs(vdir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%MZ")
    cand = os.path.join(SITE, "data", "forecast_candidate.json")
    if os.path.exists(cand):
        shutil.copy(cand, os.path.join(mdir, f"model-{stamp}.json"))
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


def build(args) -> int:
    embed = {}
    os.makedirs(os.path.join(SITE, "data"), exist_ok=True)
    os.makedirs(os.path.join(SITE, "assets"), exist_ok=True)
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
        mon = {"volcano": args.volcano, "code": "KRA", "province": "Lampung",
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
        old = os.path.join(SITE, "data", "snapshot.json")
        if os.path.exists(old):
            try:
                sat = (json.load(open(old, encoding="utf-8")) or {}).get("satellite") or {}
            except Exception:
                sat = {}
    if not args.no_sat:
        print("[build] stitching NASA GIBS daily imagery ...")
        date = latest_gibs_date()
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
                   "source": "MAGMA Indonesia / PVMBG",
                   "source_url": "https://magma.esdm.go.id/v1/gunung-api/tingkat-aktivitas",
                   "fetched_utc": mon.get("generated_utc"),
                   "fetched_wib": wib_human(mon.get("generated_utc")),
                   "indonesia_counts": mon.get("indonesia_level_counts")},
        "report": {"period": rep.get("period"), "author": rep.get("author"),
                   "visual": rep.get("visual"), "climate": rep.get("climate"),
                   "seismic_counts": rep.get("seismic_counts"),
                   "recommendation": rep.get("recommendation"),
                   "seismogram_asset": seismo_asset,
                   "source": "MAGMA Indonesia / PVMBG",
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
            "bpbd_lampung": "https://bpbd.lampungprov.go.id",
            "bpbd_banten": "https://bpbd.bantenprov.go.id",
            "darwin_vaac": "https://www.bom.gov.au/aviation/volcanic-ash/darwin-va-advisory.shtml",
        },
    }
    sp = os.path.join(SITE, "data", "snapshot.json")
    json.dump(snapshot, open(sp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[build] {sp} ({os.path.getsize(sp)/1024:.0f} KB)")
    embed["snapshot"] = snapshot

    # ---- forecast model (candidate; publish only with a human) -----------
    print("[build] computing secondary model (Himawari-9 + open-meteo) ...")
    import subprocess
    cand = None
    try:
        r = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "ash_transport.py"),
                            "--json", "--no-svg",
                            "--slots", str(args.slots), "--hours", "12"],
                           capture_output=True, text=True, timeout=args.ash_timeout)
        if r.returncode == 0:
            cand = json.loads(r.stdout)
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] model: {e}", file=sys.stderr)

    if cand:
        verdict = V.validate(mon, vaac, cand.get("observed_wind_profile"),
                             {"levels": {int(k): x for k, x in cand.get("forecast_wind", {}).items()},
                              "times": []},
                             firms=cand.get("firms"), volcano=args.volcano)
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
            # alive on nil/stale VAAC days (the 2026-09-08 failure mode).
            for v in (snapshot_vona or []):
                if v.get("ash_top_m"):
                    km = round(v["ash_top_m"] / 1000.0, 2)
                    plume_top = {"km": km, "fl": f"{v['ash_top_m']} M",
                                 "human_id": f"≈ {km:.1f} km dpl",
                                 "human_en": f"≈ {km:.1f} km asl",
                                 "source": "VONA/MAGMA ash-top estimate",
                                 "issued_wib": v.get("wib")}
                    break

        def _caveats(layers_list, plume_top, vaac, cand, verdict):
            """The human report's honesty, mirrored for machine consumers."""
            out = []
            nearest = [l.get("nearest_vector_km") for l in layers_list
                       if l.get("nearest_vector_km") and l.get("relevant_today")]
            if nearest and min(nearest) > 150:
                out.append({
                    "id": f"Tidak ada vektor angin satelit dalam {int(min(nearest))} km dari kawah; ini aliran REGIONAL, bukan pengukuran di kawah.",
                    "en": f"No satellite wind vector within {int(min(nearest))} km of the vent; this is REGIONAL flow, not a crater measurement."})
            lowR = [l for l in layers_list if l.get("consistency_R") is not None
                    and l["consistency_R"] < 0.7 and l.get("relevant_today")]
            if lowR:
                out.append({
                    "id": "Vektor satelit pada lapisan relevan tidak saling sepakat (R<0.7); arah lapisan tersebut tidak pasti.",
                    "en": "Satellite vectors in a relevant layer disagree (R<0.7); that layer's direction is uncertain."})
            agg = (verdict or {}).get("direction_corroboration", {}).get("agreement")
            if agg == "divergent":
                out.append({
                    "id": "Sumber-sumber berbeda arah 45-90° (divergen): bukan koroborasi, bukan pula konflik tegas.",
                    "en": "Sources diverge by 45-90 degrees: neither corroborated nor squarely conflicting."})
            if agg == "single_source":
                out.append({
                    "id": "Hanya satu sumber berbicara per lapisan; belum ada koroborasi independen.",
                    "en": "Only one source speaks per layer; no independent corroboration yet."})
            wet = [l for l in layers_list if any(
                (v.get("wet_points") or [])
                for v in (l.get("settling_classes") or {}).values())]
            if wet:
                out.append({
                    "id": "Sebagian lintasan melewati sel hujan (open-meteo): deposisi basah diterapkan (Λ=1e-4/s per mm/h); titik biru = perpotongan hujan → interpretasi risiko ashfall.",
                    "en": "Part of the trajectory crosses rain cells (open-meteo): wet deposition applied (Λ=1e-4/s per mm/h); blue dots = rain crossings → ashfall-risk interpretation."})
            out.append({
                "id": "Kecepatan endapan dikoreksi kepadatan udara v(h)=v0·√(ρ0/ρ(h)); difusi memakai σ(t)=√(2K0t)+g·t (K tumbuh bersama plume); geser dalam lapisan ditambahkan ke ±derajat.",
                "en": "Settling velocity density-corrected v(h)=v0·√(ρ0/ρ(h)); diffusion uses σ(t)=√(2K0t)+g·t (K grows with plume size); within-band shear added into ±degrees."})
            out.append({
                "id": "ECMWF/GFS/ICON beresolusi ~9-25 km: sirkulasi lokal mesoscale (angin laut/darat, topografi) tidak tertangkap.",
                "en": "ECMWF/GFS/ICON run at ~9-25 km grids: local mesoscale circulations (sea/land breeze, terrain flows) are not resolved."})
            spreads = [l.get("ensemble_spread_deg") for l in layers_list
                       if l.get("ensemble_spread_deg") and l["ensemble_spread_deg"] > 30]
            if spreads:
                out.append({
                    "id": f"Model cuaca (ECMWF/GFS/ICON) saling berbeda hingga {int(max(spreads))}° pada sebagian lapisan.",
                    "en": f"NWP models (ECMWF/GFS/ICON) disagree by up to {int(max(spreads))} degrees on some layers."})
            if vaac.get("state") != "advisory":
                if plume_top and "VONA" in (plume_top.get("source") or ""):
                    out.append({
                        "id": "Darwin VAAC nihil/kedaluwarsa; tinggi puncak memakai estimasi VONA/MAGMA.",
                        "en": "Darwin VAAC nil/stale; cloud-top height uses the VONA/MAGMA estimate."})
                else:
                    out.append({
                        "id": "Tidak ada tinggi puncak awan abu resmi hari ini; relevansi lapisan tidak ditandai.",
                        "en": "No official ash-cloud top today; layer relevance is unflagged."})
            if not (cand or {}).get("firms"):
                out.append({
                    "id": "Tanpa kunci FIRMS: tidak ada uji-silak hotspot independen untuk 'erupsi berlangsung'.",
                    "en": "No FIRMS key set: no independent hotspot cross-check for 'eruption ongoing'."})
            out.append({
                "id": "Lintasan memakai angin prakiraan per jam + pengendapan 3 kelas abu + difusi; varian angin-tetap ikut disertakan.",
                "en": "Trajectories use hourly-evolving forecast wind + 3 settling classes + diffusion; a steady-wind variant ships alongside."})
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
                continue
            tr_steady = (cand.get("trajectories_observed") or {}).get(row["layer"], [])
            tr_fc = (cand.get("trajectories_forecast") or {}).get(row["layer"], [])
            layers.append({
                "layer": row["layer"],
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
            "backtest": None,
            "plume_top": plume_top,
            "sources": ["Himawari-9 AMV (NOAA S3, JMA product)", "open-meteo pressure-level winds"],
        }
        cp = os.path.join(SITE, "data", "forecast_candidate.json")
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
            mp = os.path.join(SITE, "data", "forecast_model.json")
            json.dump(model, open(mp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print(f"[build] MODEL PUBLISHED by {model['approved_by']} -> {mp}")
    else:
        print("[build] no model computed")

    # ---- backtest ledger: model direction vs VAAC observed motion, per build ----
    bt_path = os.path.join(SITE, "data", "backtest.jsonl")
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
        model_vec = (json.load(open(os.path.join(SITE, "data", "forecast_candidate.json"),
                                    encoding="utf-8")) or {}).get("plume_vector")
    except Exception:
        model_vec = None
    stamp_now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:00Z")
    if model_vec and not any(r.get("t") == stamp_now for r in rows):
        rows.append({"t": stamp_now,
                     "model_toward": model_vec.get("toward_deg"),
                     "model_unc": model_vec.get("uncertainty_deg"),
                     "vaac_toward": vaac_motion})
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
    mp = os.path.join(SITE, "data", "forecast_model.json")
    if bt_stats and os.path.exists(mp):
        try:
            mj = json.load(open(mp, encoding="utf-8"))
            mj["backtest"] = bt_stats
            json.dump(mj, open(mp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        except Exception:
            pass

    # ---- 6-hourly auto-publish (maintainer decision for the decreasing-activity
    # window): clean validation only, always stamped, always carrying caveats ----
    if args.auto_publish:
        mp = os.path.join(SITE, "data", "forecast_model.json")
        try:
            cand_now = json.load(open(os.path.join(SITE, "data", "forecast_candidate.json"),
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
        archive_run(embed, vaac)

    embed_into_index(embed)
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
    ap.add_argument("--volcano", default="Anak Krakatau")
    ap.add_argument("--lat", type=float, default=-6.102)
    ap.add_argument("--lon", type=float, default=105.423)
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
