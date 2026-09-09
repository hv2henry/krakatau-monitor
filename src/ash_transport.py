#!/usr/bin/env python3
"""
ash_transport.py — Where is Anak Krakatau's ash going?

Combines two independent wind sources to answer the only question that matters
during an eruption: which way is the plume moving, and what's downwind?

  1. OBSERVED  — Himawari-9 Atmospheric Motion Vectors (AMV) from the public
                 NOAA S3 bucket `s3://noaa-himawari9/AHI-L2-FLDK-Winds`.
                 These are real cloud/water-vapour features tracked between
                 satellite images, with a pressure (=> altitude) assignment and
                 a quality indicator. Updated every 10 minutes, ~40 min latency.
                 Bands used: 07 (3.8 um, low cloud), 13/14 (10-11 um, mid/high),
                 08 (6.2 um, upper).

  2. FORECAST  — open-meteo pressure-level winds (ECMWF/GFS blend), free, no key,
                 hourly, 7 pressure levels. Used to advect the plume forward.

Output: a vertical wind profile, forward trajectories per altitude band, and a
self-contained SVG map of the projected plume.

Requires:  numpy, netCDF4      (pip install numpy netCDF4)
Optional:  NASA FIRMS hotspots (needs a free MAP_KEY, see --firms-key)

Usage:
    python3 ash_transport.py                       # profile + trajectories + SVG
    python3 ash_transport.py --radius 5 --hours 12
    python3 ash_transport.py --json
    python3 ash_transport.py --lat -6.102 --lon 105.423 --name "Anak Krakatau"

IMPORTANT: this is a diagnostic aid, not an official ash advisory. For aviation
and safety decisions use the Darwin VAAC and PVMBG. See README.
"""

from __future__ import annotations

import argparse
import bz2
import io
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone

import numpy as np
import netCDF4

# ---------------------------------------------------------------- constants
S3 = "https://noaa-himawari9.s3.amazonaws.com/"
WINDS_PREFIX = "AHI-L2-FLDK-Winds"
L1B_PREFIX = "AHI-L1b-FLDK"
UA = "Mozilla/5.0 (X11; Linux x86_64) ash-transport/1.0"

# Which AMV products to use, in order of preference for low/mid-level ash.
#   CT = cloudy-target (dense, tracks real cloud -> what carries ash)
#   CS = clear-sky water vapour (sparse, upper level only)
AMV_BANDS = [
    ("C14CT", "11.2 um IR window", "mid/high cloud"),
    ("C07CT", "3.8 um SWIR", "low cloud"),
    ("C08CT", "6.24 um WV", "upper"),
    ("C09CS", "6.9 um WV clear", "upper"),
]

# Pressure level -> representative altitude (km) used for both sources.
LEVELS = [
    (1000, 0.1, "surface"),
    (925, 0.8, "boundary layer"),
    (850, 1.5, "low — ASH-CRITICAL"),
    (700, 3.0, "mid-low — ASH-CRITICAL"),
    (600, 4.2, "mid"),
    (500, 5.6, "mid-high"),
    (300, 9.2, "upper"),
    (250, 10.5, "tropopause"),
]

OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
KMH_TO_MS = 1.0 / 3.6
FIRMS = "https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{src}/{box}/{days}"

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE

COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
           "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def compass(deg: float) -> str:
    return COMPASS[int((((deg % 360) + 360) % 360 + 11.25) // 22.5) % 16]


def pressure_to_km(p_hpa: float) -> float:
    """Barometric height for a standard tropical atmosphere."""
    if p_hpa is None or not np.isfinite(p_hpa) or p_hpa <= 0:
        return float("nan")
    return 44.3308 * (1.0 - (p_hpa / 1013.25) ** 0.190284)


# ---------------------------------------------------------------- http
class FetchError(RuntimeError):
    """Carries an HTTP status code when there was one."""
    def __init__(self, msg: str, status: int | None = None):
        super().__init__(msg)
        self.status = status


def http_get(url: str, timeout: int = 60, retries: int = 3) -> bytes:
    last, status = None, None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout, context=_ctx) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            status, last = e.code, e
            if e.code in (400, 401, 403, 404):   # not transient, don't retry
                break
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(1.5 * (2 ** i))
    raise FetchError(f"GET failed {url}: {last}", status)


def s3_list(prefix: str) -> list[tuple[str, int]]:
    """List an S3 prefix. NOTE: this bucket only matches when slashes are
    percent-encoded in the `prefix` query value — a quirk worth preserving."""
    q = urllib.parse.quote(prefix, safe="")
    out, marker = [], ""
    for _ in range(20):
        url = f"{S3}?prefix={q}&max-keys=1000"
        if marker:
            url += f"&marker={urllib.parse.quote(marker, safe='')}"
        xml = http_get(url, 45).decode("utf-8", "replace")
        for block in re.findall(r"<Contents>(.*?)</Contents>", xml, re.S):
            k = re.search(r"<Key>([^<]+)</Key>", block)
            s = re.search(r"<Size>(\d+)</Size>", block)
            if k and s:
                out.append((k.group(1), int(s.group(1))))
        if "<IsTruncated>true</IsTruncated>" not in xml:
            break
        nm = re.findall(r"<Key>([^<]+)</Key>", xml)
        if not nm:
            break
        marker = nm[-1]
    return out


# ---------------------------------------------------------------- Himawari AMV
def latest_amv_slot(when: datetime | None = None, max_back: int = 24) -> tuple[str, datetime] | None:
    """Find the most recent 10-minute slot that has AMV files published.
    NOAA publishes with ~25-45 min latency, so scan backwards."""
    now = when or datetime.now(timezone.utc)
    slot = now.replace(second=0, microsecond=0, minute=(now.minute // 10) * 10)
    for i in range(max_back):
        t = slot - timedelta(minutes=10 * i)
        pre = f"{WINDS_PREFIX}/{t:%Y/%m/%d/%H%M}/"
        if s3_list(pre):
            return pre, t
    return None


def amv_slots(n: int, when: datetime | None = None) -> list[tuple[str, datetime]]:
    """The n most recent published AMV slots, newest first."""
    out = []
    cursor = when or datetime.now(timezone.utc)
    for _ in range(n * 6 + 12):
        if len(out) >= n:
            break
        found = latest_amv_slot(cursor)
        if not found:
            break
        out.append(found)
        cursor = found[1] - timedelta(minutes=10)
    return out


def pick_amv_files(slot_prefix: str) -> dict[str, str]:
    """Return {band_tag: s3_key} for the CT/CS products present in a slot."""
    out = {}
    for key, size in s3_list(slot_prefix):
        m = re.search(r"NDMW-AHI-(C\d\d[A-Z]{2})_", key)
        if m:
            out.setdefault(m.group(1), key)
    return out


def read_amv(key: str, cache_dir: str, slot_tag: str = "") -> dict:
    """Download + parse one AMV NetCDF into flat arrays."""
    tag = re.search(r"NDMW-AHI-(C\d\d[A-Z]{2})_", key).group(1)
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, (tag + ("_" + slot_tag if slot_tag else "")) + ".nc")
    if not (os.path.exists(path) and os.path.getsize(path) > 1000):
        with open(path, "wb") as f:
            f.write(http_get(S3 + key, timeout=300))
    ds = netCDF4.Dataset(path)
    ds.set_auto_maskandscale(False)

    def g(name):
        return ds[name][:] if name in ds.variables else None

    lat, lon, spd, wd = g("Latitude"), g("Longitude"), g("Wind_Speed"), g("Wind_Dir")
    u1, v1, u2, v2 = g("UComponent1"), g("VComponent1"), g("UComponent2"), g("VComponent2")
    prs, qi, bt, ee = g("MedianPress"), g("QI"), g("MedianBT"), g("ExpectedErr")
    if lat is None or spd is None:
        return {}

    def ok(a, fill=-999.0):
        return np.isfinite(a) & (a != fill)

    u = (np.where(ok(u1), u1, np.nan) + np.where(ok(u2), u2, np.nan)) / 2.0
    v = (np.where(ok(v1), v1, np.nan) + np.where(ok(v2), v2, np.nan)) / 2.0
    valid = (ok(spd) & (spd > 0.3) & ok(lat) & ok(lon) & ok(prs) &
             ok(qi, -98) & ok(u) & ok(v))
    meta = {
        "tag": tag,
        "band": getattr(ds, "sensor_band_identifier", None),
        "wavelength": getattr(ds, "sensor_band_central_radiation_wavelength", None),
        "window_start": str(getattr(ds, "time_coverage_start", "")),
        "window_end": str(getattr(ds, "time_coverage_end", "")),
        "n_global": int(valid.sum()),
    }
    ds.close()
    idx = np.where(valid)[0]
    return {
        "meta": meta,
        "lat": lat[idx].astype(float), "lon": lon[idx].astype(float),
        "u": u[idx].astype(float), "v": v[idx].astype(float),
        "spd": spd[idx].astype(float), "dir": (wd[idx] % 360).astype(float) if ok(wd).any() else np.full(len(idx), np.nan),
        "prs": prs[idx].astype(float), "qi": qi[idx].astype(float),
        "bt": bt[idx].astype(float) if bt is not None else np.full(len(idx), np.nan),
        "ee": ee[idx].astype(float) if ee is not None else np.full(len(idx), np.nan),
    }


def _confidence(cons, n, nearest_km):
    """Directional agreement + sample size + how close the evidence is to the vent."""
    if cons >= 0.90 and n >= 8 and nearest_km < 150:
        return "high"
    if cons >= 0.70 and n >= 5 and nearest_km < 250:
        return "moderate"
    if nearest_km >= 250:
        return "LOW — no vectors near vent"
    if cons < 0.70:
        return "LOW — vectors disagree"
    return "LOW — too few vectors"


def circular_mean(deg: np.ndarray) -> tuple[float, float]:
    """Mean of angles + R (0..1 consistency; 1 = all vectors agree)."""
    d = deg[np.isfinite(deg)]
    if d.size == 0:
        return float("nan"), 0.0
    r = np.radians(d)
    s, c = np.mean(np.sin(r)), np.mean(np.cos(r))
    return float(np.degrees(np.arctan2(s, c)) % 360), float(np.hypot(s, c))


LEVELS_P = [1000, 925, 850, 700, 600, 500, 400, 300]
ENSEMBLE_MODELS = ["ecmwf_ifs025", "gfs_global", "icon_seamless"]
K_DIFFUSIVITY = 5e3        # m2/s horizontal eddy diffusivity (ash-plume literature order)
SETTLE_CLASSES = {"fine": 0.02, "medium": 0.12, "coarse": 0.60}   # m/s Stokes settling


def nwp_profile(lat: float, lon: float, hours: int = 12) -> dict:
    """One open-meteo call: winds + temperatures at 8 pressure levels, hourly,
    plus the same wind direction from 3 independent NWP models (ensemble spread).
    Temperatures make the pressure<->altitude conversion real (hypsometric)
    instead of assuming an isothermal atmosphere."""
    hourly = []
    for p in LEVELS_P:
        hourly += [f"wind_speed_{p}hPa", f"wind_direction_{p}hPa", f"temperature_{p}hPa"]
    # with &models=, open-meteo returns plain keys for best_match plus
    # model-suffixed keys for each member — do NOT request suffixed names manually
    url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
           f"&timezone=GMT&forecast_hours={hours}&hourly=" + ",".join(hourly) +
           "&models=best_match," + ",".join(ENSEMBLE_MODELS))
    d = json.loads(http_get(url).decode("utf-8"))
    h = d["hourly"]
    # with &models=, every variable is per-model suffixed; best_match is our base
    kmh = 1.0 / 3.6 if d.get("hourly_units", {}).get("wind_speed_700hPa_best_match") == "km/h" else 1.0
    n = len(h["time"])
    out = {"times": h["time"], "levels": {}, "ensemble": {}, "units_km_h": bool(kmh != 1.0)}
    for p in LEVELS_P:
        sp_c, dd_c, tt_c = (h.get(f"wind_speed_{p}hPa_best_match") or [None] * n,
                            h.get(f"wind_direction_{p}hPa_best_match") or [None] * n,
                            h.get(f"temperature_{p}hPa_best_match") or [None] * n)
        recs = []
        for i, t in enumerate(h["time"]):
            if sp_c[i] is None or dd_c[i] is None:
                continue
            spd = sp_c[i] * kmh
            frm = dd_c[i]
            recs.append({"t": t, "speed_ms": spd, "from_deg": frm,
                         "u_ms": -spd * np.sin(np.radians(frm)),
                         "v_ms": -spd * np.cos(np.radians(frm)),
                         "temp_c": tt_c[i]})
        out["levels"][p] = recs
    for p in (850, 700, 500):
        dirs = [h[f"wind_direction_{p}hPa_{m}"][0] for m in ENSEMBLE_MODELS
                if h.get(f"wind_direction_{p}hPa_{m}")]
        out["ensemble"][p] = dirs
    return out


def level_heights_km(nwp: dict) -> dict:
    """Hypsometric integration of the model's own temperature profile:
    dz = (Rd * Tv / g) * ln(p1/p2). Falls back to standard-atmosphere formula."""
    temps = {}
    for p, recs in (nwp or {}).get("levels", {}).items():
        if recs and recs[0].get("temp_c") is not None:
            temps[p] = recs[0]["temp_c"]
    heights = {}
    if len(temps) >= 3:
        ps = sorted(temps, reverse=True)
        z = 0.1
        heights[ps[0]] = z
        for a, b in zip(ps, ps[1:]):
            tv = 273.15 + (temps[a] + temps[b]) / 2.0
            z += (287.05 * tv / 9.81) * np.log(a / b) / 1000.0
            heights[b] = z
    for p in LEVELS_P:
        if p not in heights:
            heights[p] = 44.3308 * (1.0 - (p / 1013.25) ** 0.190284)
    return heights


def ensemble_spread_deg(nwp: dict, p: int) -> float | None:
    dirs = [d for d in (nwp or {}).get("ensemble", {}).get(p, []) if d is not None]
    if len(dirs) < 2:
        return None
    r = np.radians(dirs)
    R = np.hypot(np.mean(np.sin(r)), np.mean(np.cos(r)))
    return float(min(90.0, np.degrees(np.sqrt(-2.0 * np.log(max(R, 0.05))))))


def wind_at(nwp: dict, heights: dict, t_idx: int, h_km: float):
    """Interpolant of the NWP wind field at (time index, height)."""
    ps = sorted((p for p in nwp["levels"] if nwp["levels"][p]),
                key=lambda p: heights[p])
    if not ps:
        return 0.0, 0.0
    lo = hi = None
    for p in ps:
        if heights[p] <= h_km:
            lo = p
        else:
            hi = p
            break
    def rec(p):
        rs = nwp["levels"][p]
        return rs[t_idx] if t_idx < len(rs) else rs[-1]
    if lo is None:
        return rec(ps[0])["u_ms"], rec(ps[0])["v_ms"]
    if hi is None:
        return rec(ps[-1])["u_ms"], rec(ps[-1])["v_ms"]
    z0, z1 = heights[lo], heights[hi]
    w = 0.0 if z1 == z0 else (h_km - z0) / (z1 - z0)
    r0, r1 = rec(lo), rec(hi)
    return r0["u_ms"] * (1 - w) + r1["u_ms"] * w, r0["v_ms"] * (1 - w) + r1["v_ms"] * w


def amv_profile(observations, lat, lon, radius, qmin, nwp=None, heights=None):
    """Weighted band statistics. Every vector counts by how close it is to the
    vent (Gaussian kernel, sigma 150 km) and by its own quality (QI), because a
    vector 300 km away across the Sunda Strait is NOT the wind at the crater.
    Also blends the NWP background wind and reports an honest uncertainty."""
    rows = []
    edges = [(900, 1060), (750, 900), (600, 750), (450, 600), (300, 450), (100, 300)]
    labels = ["~0-1 km surface", "~1-2 km ASH-CRITICAL", "~2-4 km ASH-CRITICAL",
              "~4-6 km", "~6-9 km", "~9-16 km upper"]
    SIGMA_KM = 150.0
    for (plo, phi), lab in zip(edges, labels):
        agg = []
        for o in observations:
            if not o:
                continue
            m = ((np.abs(o["lat"] - lat) < radius) & (np.abs(o["lon"] - lon) < radius) &
                 (o["prs"] >= plo) & (o["prs"] < phi) & (o["qi"] >= qmin))
            for i in np.where(m)[0]:
                d = haversine(lat, lon, float(o["lat"][i]), float(o["lon"][i]))
                w = float(np.exp(-(d / SIGMA_KM) ** 2) * (float(o["qi"][i]) / 100.0))
                agg.append((w, d, float(o["u"][i]), float(o["v"][i]), float(o["qi"][i])))
        if len(agg) < 3:
            rows.append({"layer": lab, "data": False, "n": len(agg),
                         "pressure_range": [plo, phi]})
            continue
        W = np.array([a[0] for a in agg])
        U = np.array([a[2] for a in agg])
        V = np.array([a[3] for a in agg])
        uw, vw = float((W * U).sum() / W.sum()), float((W * V).sum() / W.sum())
        unit_u = U / np.hypot(U, V)
        unit_v = V / np.hypot(U, V)
        R = float(np.hypot((W * unit_u).sum(), (W * unit_v).sum()) / W.sum())
        n_eff = float(W.sum() ** 2 / (W ** 2).sum())
        toward = float(np.degrees(np.arctan2(uw, vw)) % 360)
        spd = float(np.hypot(uw, vw))
        unc = float(min(60.0, max(5.0, np.degrees(np.sqrt(-2.0 * np.log(max(R, 0.05)))) / np.sqrt(max(n_eff, 1.0)))))
        p_mid = (plo + phi) / 2
        # NWP background at the band's pressure, t0
        nwp_u = nwp_v = None
        if nwp:
            near_p = min(nwp["levels"], key=lambda p: abs(p - p_mid))
            rs = nwp["levels"][near_p]
            if rs:
                nwp_u, nwp_v = rs[0]["u_ms"], rs[0]["v_ms"]
        # transparent blend: AMV weight grows with effective sample & quality
        qi_mean = float(np.mean([a[4] for a in agg]))
        w_amv = min(1.0, n_eff / 10.0) * (qi_mean / 100.0)
        w_nwp = 0.5
        if nwp_u is not None:
            bu = (w_amv * uw + w_nwp * nwp_u) / (w_amv + w_nwp)
            bv = (w_amv * vw + w_nwp * nwp_v) / (w_amv + w_nwp)
        else:
            bu, bv = uw, vw
        h_km = None
        if heights:
            near_p = min(heights, key=lambda p: abs(p - p_mid))
            h_km = round(float(heights[near_p]), 2)
        espread = None
        if nwp:
            near_p = min(nwp["levels"], key=lambda p: abs(p - p_mid))
            espread = ensemble_spread_deg(nwp, near_p)
        rows.append({
            "layer": lab, "data": True, "n": len(agg), "n_eff": round(n_eff, 1),
            "pressure_range": [plo, phi],
            "alt_km": h_km if h_km is not None else round(float(pressure_to_km(p_mid)), 2),
            "mean_altitude_km": h_km if h_km is not None else round(float(pressure_to_km(p_mid)), 2),
            "from_deg": float((toward + 180) % 360),
            "from_compass": compass(float((toward + 180) % 360)),
            "toward_deg": toward, "toward_compass": compass(toward),
            "speed_ms": round(spd, 1),
            "u_ms": round(uw, 2), "v_ms": round(vw, 2),
            "blended_u_ms": round(bu, 2), "blended_v_ms": round(bv, 2),
            "blended_toward_deg": float(np.degrees(np.arctan2(bu, bv)) % 360),
            "blended_speed_ms": round(float(np.hypot(bu, bv)), 1),
            "blend_weights": {"amv": round(w_amv, 2), "nwp": w_nwp},
            "consistency_R": round(R, 3),
            "uncertainty_deg": round(unc, 1),
            "ensemble_spread_deg": round(espread, 1) if espread is not None else None,
            "mean_qi": round(qi_mean, 1),
            "nearest_vector_km": round(float(min(a[1] for a in agg)), 1),
            "median_vector_km": round(float(np.median([a[1] for a in agg])), 1),
            "confidence": ("high" if R >= 0.90 and n_eff >= 8 else
                           "moderate" if R >= 0.70 else "LOW - vectors disagree"),
        })
    return rows


# ---------------------------------------------------------------- trajectories
def integrate(lat0: float, lon0: float, u_of_t, v_of_t, start: datetime,
              hours: float, dt_min: int = 10) -> list[dict]:
    """Forward-integrate a parcel. u,v in m/s (u=+east, v=+north)."""
    lat, lon = lat0, lon0
    out = [{"t": start.isoformat(), "lat": lat0, "lon": lon0, "hours": 0.0}]
    dt = dt_min * 60.0
    n = int(hours * 60 / dt_min)
    for i in range(n):
        t = start + timedelta(minutes=dt_min * (i + 0.5))
        u, v = u_of_t(t), v_of_t(t)
        if u is None or v is None:
            break
        m_per_deg = 111320.0
        lat += (v * dt) / m_per_deg
        lon += (u * dt) / (m_per_deg * max(np.cos(np.radians(lat)), 1e-6))
        if abs(lat) > 85:
            break
        out.append({"t": t.isoformat(), "lat": round(lat, 4), "lon": round(lon % 360, 4),
                    "hours": round((i + 1) * dt_min / 60.0, 2),
                    "dist_km": round(haversine(lat0, lon0, lat, lon), 1)})
    return out


def trajectory_settling(lat0, lon0, h0_km, nwp, heights, start, hours=12,
                        dt_min=10):
    """Advect parcels for three settling classes through the time-evolving,
    height-interpolated NWP wind field, plus a horizontal diffusion envelope
    sigma(t) = sqrt(2 K t). A pure advection line oversells precision; this
    turns it into an honest envelope with only stdlib+numpy."""
    out = {}
    steps = int(hours * 60 / dt_min)
    for cname, vs in SETTLE_CLASSES.items():
        lat, lon, h = lat0, lon0, h0_km
        pts = [{"lat": round(lat, 4), "lon": round(lon, 4), "hours": 0.0,
                "alt_km": round(h, 2), "sigma_km": 0.0}]
        for i in range(steps):
            t_idx = min(i, len(nwp["times"]) - 1)
            u, v = wind_at(nwp, heights, t_idx, max(h, 0.05))
            dt = dt_min * 60.0
            lat += (v * dt) / 111320.0
            lon += (u * dt) / (111320.0 * max(np.cos(np.radians(lat)), 1e-6))
            h = max(0.05, h - vs * dt / 1000.0)
            t_sec = (i + 1) * dt
            sigma = np.sqrt(2.0 * K_DIFFUSIVITY * t_sec) / 1000.0
            pts.append({"lat": round(lat, 4), "lon": round(lon, 4),
                        "hours": round((i + 1) * dt_min / 60.0, 2),
                        "alt_km": round(h, 2), "sigma_km": round(sigma, 1)})
        out[cname] = pts
    return out


def envelope_polygon(fine_pts):
    """Cross-track buffer polygon from the diffusion sigma at each point."""
    left, right = [], []
    for i, pt in enumerate(fine_pts):
        nxt = fine_pts[min(i + 1, len(fine_pts) - 1)]
        prv = fine_pts[max(i - 1, 0)]
        dlon = nxt["lon"] - prv["lon"]
        dlat = nxt["lat"] - prv["lat"]
        norm = np.hypot(dlon, dlat) or 1e-9
        px, py = -dlat / norm, dlon / norm
        sg = pt.get("sigma_km", 0.0)
        dlat_s = sg / 111.32
        dlon_s = sg / (111.32 * max(np.cos(np.radians(pt["lat"])), 1e-6))
        left.append([round(pt["lon"] + px * dlon_s, 4), round(pt["lat"] + py * dlat_s, 4)])
        right.append([round(pt["lon"] - px * dlon_s, 4), round(pt["lat"] - py * dlat_s, 4)])
    return left + right[::-1]


def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp, dl = np.radians(lat2 - lat1), np.radians(lon2 - lon1)
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return float(R * 2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a)))


LANDMARKS = [
    ("Bandar Lampung (Sumatra)", -5.43, 105.26, 1_100_000),
    ("Jakarta", -6.21, 106.85, 3_000_000),
    ("Serang / W Banten", -6.11, 106.15, 900_000),
    ("Bandung", -6.91, 107.61, 2_500_000),
    ("Cilegon / Merak ferry terminal", -5.97, 106.02, 500_000),
    ("Tanjung Karang", -5.42, 105.32, 1_000_000),
    ("Krui / W Sumatra coast", -5.15, 103.90, 200_000),
    ("Pulau Sebesi (inhabited)", -5.85, 105.49, 3_000),
    ("Soekarno-Hatta Intl Airport", -6.13, 106.66, 40_000_000),
]


def exposure(lat, lon, traj_by_level, start, skip_km: float = 25.0):
    """Closest approach of the MODELLED PLUME to each landmark.

    Only trajectory points that have already travelled further than `skip_km`
    from the vent count. Without that guard every landmark near the volcano
    would trivially report ~0 km, because all trajectories originate AT the
    vent — which says nothing about whether ash actually reaches the place.
    """
    rows = []
    for name, llat, llon, pop in LANDMARKS:
        d_vent = haversine(lat, lon, llat, llon)
        best = None
        for lab, tr in traj_by_level.items():
            for pt in tr:
                if pt.get("dist_km", 0) < skip_km:
                    continue          # still essentially at the vent
                d = haversine(llat, llon, pt["lat"], pt["lon"])
                if best is None or d < best["dist_km"]:
                    best = {"dist_km": round(d, 1), "level": lab, "hours": pt["hours"]}
        rows.append({"place": name, "population": pop, "km_from_vent": round(d_vent, 1),
                     **(best or {"dist_km": None, "level": "not on any plume path",
                                 "hours": None})})
    rows.sort(key=lambda r: (r["dist_km"] is None, r["dist_km"] if r["dist_km"] is not None else 0))
    return rows


# ---------------------------------------------------------------- FIRMS (optional)
def firms_hotspots(key: str, lat: float, lon: float, pad: float = 1.0, days: int = 1) -> dict:
    """NASA FIRMS thermal anomalies. Aqua/Terra = MODIS, Suomi NPP + NOAA-20 = VIIRS.
    Needs a free MAP_KEY: https://firms.modaps.eosdis.nasa.gov/api/
    A volcano's summit registers as a persistent thermal anomaly — useful as an
    independent confirmation that an eruption is ongoing, though it cannot see
    ash (ash is cold in the infrared)."""
    box = f"{lon-pad},{lat-pad},{lon+pad},{lat+pad}"
    out = {}
    for src in ["VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "MODIS_C6_1_NRT"]:
        try:
            csv = http_get(FIRMS.format(key=key, src=src, box=box, days=days), 40).decode()
            rows = [l.split(",") for l in csv.strip().splitlines()]
            if len(rows) > 1:
                hdr, data = rows[0], rows[1:]
                out[src] = {"n": len(data), "header": hdr, "sample": data[:12]}
            else:
                out[src] = {"n": 0}
        except FetchError as e:
            hint = {401: "MAP_KEY rejected", 403: "MAP_KEY rejected / not authorised",
                    400: "bad request — MAP_KEY or area box", 404: "unknown source name"}.get(e.status)
            out[src] = {"error": f"HTTP {e.status} — {hint or 'request failed'}. "
                                 f"Get a free key at firms.modaps.eosdis.nasa.gov/api/"}
        except Exception as e:  # noqa: BLE001
            out[src] = {"error": str(e)[:90]}
    return out


# ---------------------------------------------------------------- SVG map
def svg_map(lat, lon, traj_by_level, amv_rows, radius=6.0, path="ash_map.svg"):
    """Self-contained SVG: plume trajectories + observed wind barbs."""
    W, H = 880, 700
    dlat, dlon = radius, radius * (W / H) * abs(np.cos(np.radians(lat)))
    la0, la1 = lat + dlat, lat - dlat
    lo0, lo1 = lon - dlon, lon + dlon
    X = lambda v: (v - lo0) / (lo1 - lo0) * W
    Y = lambda v: (la0 - v) / (la0 - la1) * H

    COLORS = {"~0-1 km surface": "#8b949e", "~1-2 km ASH-CRITICAL": "#f0883e",
              "~2-4 km ASH-CRITICAL": "#db6d28", "~4-6 km": "#a371f7",
              "~6-9 km": "#388bfd", "~9-16 km upper": "#3fb950"}
    p = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
         f'font-family="-apple-system,Segoe UI,Helvetica,Arial,sans-serif">',
         f'<rect width="{W}" height="{H}" fill="#0d1117"/>']
    # graticule
    for la in range(int(np.floor(la1)), int(np.ceil(la0)) + 1):
        if la1 <= la <= la0:
            p.append(f'<line x1="0" y1="{Y(la):.1f}" x2="{W}" y2="{Y(la):.1f}" stroke="#21262d" stroke-width="1"/>')
            p.append(f'<text x="6" y="{Y(la)-4:.1f}" fill="#484f58" font-size="10">{la}°</text>')
    for lo in range(int(np.floor(lo0)), int(np.ceil(lo1)) + 1):
        if lo0 <= lo <= lo1:
            p.append(f'<line x1="{X(lo):.1f}" y1="0" x2="{X(lo):.1f}" y2="{H}" stroke="#21262d" stroke-width="1"/>')
            p.append(f'<text x="{X(lo)+4:.1f}" y="{H-8}" fill="#484f58" font-size="10">{lo}°E</text>')
    # landmarks
    for name, llat, llon, pop in LANDMARKS:
        if la1 <= llat <= la0 and lo0 <= llon <= lo1:
            p.append(f'<circle cx="{X(llon):.1f}" cy="{Y(llat):.1f}" r="3" fill="#e6edf3" opacity="0.85"/>')
            p.append(f'<text x="{X(llon)+7:.1f}" y="{Y(llat)+4:.1f}" fill="#c9d1d9" font-size="11">{name}</text>')
    # trajectories
    for lab, tr in traj_by_level.items():
        if len(tr) < 2:
            continue
        col = COLORS.get(lab, "#e6edf3")
        pts = " ".join(f"{X(pt['lon']):.1f},{Y(pt['lat']):.1f}" for pt in tr
                       if lo0 - 1 <= pt["lon"] <= lo1 + 1 and la1 - 1 <= pt["lat"] <= la0 + 1)
        if not pts:
            continue
        p.append(f'<polyline points="{pts}" fill="none" stroke="{col}" stroke-width="2.6" '
                 f'stroke-linecap="round" opacity="0.95"/>')
        e = tr[-1]
        if lo0 <= e["lon"] <= lo1 and la1 <= e["lat"] <= la0:
            p.append(f'<circle cx="{X(e["lon"]):.1f}" cy="{Y(e["lat"]):.1f}" r="4.5" fill="{col}"/>')
            p.append(f'<text x="{X(e["lon"])+8:.1f}" y="{Y(e["lat"])+4:.1f}" fill="{col}" '
                     f'font-size="11" font-weight="600">+{e["hours"]:g}h</text>')
    # volcano
    p.append(f'<g><circle cx="{X(lon):.1f}" cy="{Y(lat):.1f}" r="14" fill="#f85149" opacity="0.18"/>'
             f'<circle cx="{X(lon):.1f}" cy="{Y(lat):.1f}" r="7" fill="#f85149" stroke="#0d1117" stroke-width="2"/>'
             f'<text x="{X(lon)+16:.1f}" y="{Y(lat)+5:.1f}" fill="#ff7b72" font-size="13" font-weight="700">'
             f'Anak Krakatau</text></g>')
    # legend
    p.append('<rect x="14" y="14" width="252" height="' + str(34 + 20 * len(traj_by_level)) +
             '" rx="8" fill="#161b22" stroke="#30363d"/>')
    p.append('<text x="28" y="36" fill="#e6edf3" font-size="13" font-weight="700">'
             'Projected ash trajectories by altitude</text>')
    yy = 56
    for lab in traj_by_level:
        p.append(f'<line x1="28" y1="{yy}" x2="52" y2="{yy}" stroke="{COLORS.get(lab,"#e6edf3")}" stroke-width="3"/>')
        p.append(f'<text x="60" y="{yy+4}" fill="#c9d1d9" font-size="11">{lab}</text>')
        yy += 20
    p.append(f'<text x="14" y="{H-14}" fill="#484f58" font-size="10">'
             f'Himawari-9 AMV + open-meteo forecast · generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC · '
             f'diagnostic only — not an official ash advisory</text>')
    p.append("</svg>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("".join(p))
    return path


# ---------------------------------------------------------------- text report
def verdict_block(amv_rows, traj):
    """Plain-language answer first, with the confidence caveats attached."""
    ash = [r for r in amv_rows if r.get("data") and "ASH-CRITICAL" in r["layer"]]
    lines = ["", "  VERDICT — which way is the ash going?", "  " + "-" * 62]
    if not ash:
        lines.append("  No usable satellite wind vectors in the ash layer this pass.")
    for r in ash:
        conf = r.get("confidence", "")
        good = conf in ("high", "moderate")
        mark = "✔" if good else "?"
        lines.append(f"   {mark} {r['layer']:22} -> {r['toward_compass']:4s} ({r['toward_deg']:5.1f}°) "
                     f"at {r['speed_ms']:.1f} m/s   [{conf}]")
    strong = [r for r in ash if r.get("confidence") in ("high", "moderate")]
    weak = [r for r in ash if r.get("confidence") not in ("high", "moderate")]
    lines.append("")
    if len(strong) >= 2 and len({r["toward_compass"] for r in strong}) == 1:
        lines.append(f"   Consistent signal: ash in the 1-4 km layer is heading {strong[0]['toward_compass']}.")
    elif len(strong) == 1:
        r = strong[0]
        band = r["layer"].split("ASH")[0].strip()
        lines.append(f"   Best-supported answer: {r['toward_compass']} ({r['toward_deg']:.0f}°) in the "
                     f"{band} layer")
        lines.append(f"   ({r['n']} satellite vectors, consistency R={r['consistency_R']:.2f}).")
        if weak:
            w = ", ".join(f"{x['layer'].split('ASH')[0].strip()} -> {x['toward_compass']} (weak)" for x in weak)
            lines.append(f"   The other ash layer is unresolved: {w}. Do not treat one")
            lines.append("   layer's answer as the answer for the whole plume.")
    elif len(strong) >= 2:
        lines.append("   Mixed signal across the ash layer (" +
                     ", ".join(f"{r['layer'].split('ASH')[0].strip()} -> {r['toward_compass']}" for r in strong) +
                     ") — the plume will smear rather than travel as one beam.")
    else:
        lines.append("   No well-supported direction this pass. The satellite vectors are too few")
        lines.append("   or too scattered; rely on the forecast table below and re-run shortly.")
    lines.append("")
    lines.append("   ⚠ Every vector used here is 150+ km from the vent: Himawari can only")
    lines.append("     track wind where there is cloud, and the sky over the volcano is")
    lines.append("     often clear. This is REGIONAL flow, not a measurement at the crater.")
    lines.append("   ⚠ Satellite AMV speeds carry ±3-6 m/s error. Trust direction, not speed.")
    lines.append("   ⚠ Ash altitude decides everything: the same eruption can send one")
    lines.append("     layer south and another northwest. Check the whole profile, not one row.")
    lines.append("")
    return "\n".join(lines)


def render(args, amv_rows, om, traj, start, exposure_rows, firms, traj_fc=None):
    L = []
    L.append("=" * 76)
    L.append(f"  ASH TRANSPORT — {args.name}  ({args.lat}, {args.lon})")
    L.append(f"  analysis time: {start:%Y-%m-%d %H:%M} UTC  =  {start.astimezone(timezone(timedelta(hours=7))):%H:%M} WIB")
    L.append("=" * 76)

    L.append(verdict_block(amv_rows, traj))
    L.append("\n  OBSERVED WIND — Himawari-9 Atmospheric Motion Vectors")
    L.append(f"  (real cloud/WV features tracked between images; ±{args.radius}° of summit)\n")
    hdr = (f"  {'layer':22}{'n':>4} {'spd':>7} {'alt':>7}  {'wind FROM':>13}  {'ash goes TOWARD':>17}"
           f"  {'R':>5} {'±err':>6} {'nearest':>9}  confidence")
    L.append(hdr)
    L.append("  " + "-" * (len(hdr) - 2))
    for r in amv_rows:
        if not r.get("data"):
            L.append(f"  {r['layer']:24}{r['n']:>4}  — no usable vectors (QI>={args.qmin})")
            continue
        nk = r.get("nearest_vector_km")
        L.append(f"  {r['layer']:22}{r['n']:>4} {r['speed_ms']:6.1f}m {r['mean_altitude_km']:6.2f}k "
                 f" {r['from_deg']:6.1f}° {r['from_compass']:4s}   {r['toward_deg']:6.1f}° {r['toward_compass']:4s}"
                 f"       {r['consistency_R']:5.2f} {r['median_expected_error_ms']:5.1f}m "
                 f"{(str(int(nk))+'km') if nk else '—':>9}  {r.get('confidence','')}")
    L.append("\n  R = directional consistency (1.00 = every vector agrees).")
    L.append("  nearest = distance from the vent to the CLOSEST vector used. AMVs need")
    L.append("            trackable cloud, so a clear sky over the volcano means the")
    L.append("            evidence is regional, not local — treat those rows with care.")
    L.append("  ±err = NOAA's own expected error on the wind SPEED. Where that is")
    L.append("         comparable to the speed, trust the direction, not the magnitude.")

    L.append("\n\n  FORECAST WIND — open-meteo (ECMWF/GFS blend), direction ash travels TOWARD")
    L.append(f"  {'level':>7} {'~alt':>7} | " + "  ".join(f"{t[11:16]:>11}" for t in om["times"][:6]))
    for p, alt, lab in LEVELS:
        recs = om["levels"].get(p)
        if not recs:
            continue
        cells = [f"{compass((r['from_deg']+180)%360):>3} {r['speed_ms']:>6.1f}" for r in recs[:6]]
        L.append(f"  {p:>5}hPa {alt:>5.1f}km | " + "  ".join(f"{c:>11}" for c in cells))
    L.append("  (hours are UTC; add 7 for WIB)")

    def _traj_block(title, data):
        L.append("\n\n  " + title)
        for lab, tr in data.items():
            _one_traj(lab, tr)

    def _one_traj(lab, tr):
        if len(tr) < 2:
            return
        L.append(f"\n   {lab}")
        for pt in tr:
            if pt["hours"] in (1, 2, 3, 4, 6, 9, 12) or pt == tr[-1]:
                L.append(f"      +{pt['hours']:>4}h  {pt['lat']:7.3f}° {pt['lon']:8.3f}°   "
                         f"{pt.get('dist_km',0):7.1f} km from vent")
        if len(tr) > 1:
            e = tr[-1]
            brg = bearing(args.lat, args.lon, e["lat"], e["lon"])
            L.append(f"      net transport: {brg:5.1f}° ({compass(brg)}) — "
                     f"{e.get('dist_km',0):.0f} km in {e['hours']:g} h")

    _traj_block("PROJECTED PLUME — observed Himawari-9 wind held steady (primary)", traj)
    if traj_fc:
        _traj_block("PROJECTED PLUME — forecast wind evolving hourly (open-meteo; may diverge)", traj_fc)

    if exposure_rows:
        L.append("\n\n  DOWNWIND EXPOSURE — how close the modelled plume passes to each place")
        L.append(f"  {'place':30}{'plume passes':>13} {'at':>7}  {'layer':>24}   {'vent dist':>10}")
        for r in exposure_rows:
            if r["dist_km"] is None:
                L.append(f"  {r['place']:30}{'—':>13} {'':>7}  {r['level']:>24}   {r['km_from_vent']:8.0f}km")
                continue
            flag = "  ⚠ IN PATH" if r["dist_km"] < 60 else ("  · near" if r["dist_km"] < 150 else "")
            L.append(f"  {r['place']:30}{r['dist_km']:10.0f}km {r['hours']:>5}h  {r['level']:>24}   {r['km_from_vent']:8.0f}km{flag}")
        L.append("\n  'plume passes' = closest the advected parcel comes to that place,")
        L.append("  ignoring fallout. It is a trajectory, not an ash-fall forecast.")

    if firms:
        L.append("\n\n  THERMAL ANOMALIES (NASA FIRMS — Aqua/Terra MODIS, Suomi NPP & NOAA-20 VIIRS)")
        for src, d in firms.items():
            if d.get("error"):
                L.append(f"   {src:20} {d['error']}")
            else:
                L.append(f"   {src:20} {d.get('n',0)} hotspot(s) in ±{args.pad}° over {args.days} day(s)")
        L.append("   Note: FIRMS sees HEAT, not ash. Ash is cold in the infrared —")
        L.append("   it confirms an eruption is ongoing but cannot show the plume.")

    L.append("\n" + "=" * 76)
    return "\n".join(L)


def bearing(la1, lo1, la2, lo2):
    p1, p2 = np.radians(la1), np.radians(la2)
    dl = np.radians(lo2 - lo1)
    y = np.sin(dl) * np.cos(p2)
    x = np.cos(p1) * np.sin(p2) - np.sin(p1) * np.cos(p2) * np.cos(dl)
    return float((np.degrees(np.arctan2(y, x)) + 360) % 360)


# ---------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description="Ash transport for Anak Krakatau")
    ap.add_argument("--lat", type=float, default=-6.102)
    ap.add_argument("--lon", type=float, default=105.423)
    ap.add_argument("--name", default="Anak Krakatau")
    ap.add_argument("--radius", type=float, default=4.0,
                    help="degrees around the summit to gather AMVs (default 4)")
    ap.add_argument("--qmin", type=float, default=60, help="min AMV quality indicator (default 60)")
    ap.add_argument("--hours", type=float, default=12, help="trajectory integration length")
    ap.add_argument("--bands", default="C14CT,C07CT,C08CT", help="AMV products to download")
    ap.add_argument("--cache", default="sat_cache", help="NetCDF cache directory")
    ap.add_argument("--svg", default="ash_map.svg")
    ap.add_argument("--no-svg", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--firms-key", default=os.environ.get("FIRMS_MAP_KEY"),
                    help="NASA FIRMS MAP_KEY (free; also via $FIRMS_MAP_KEY)")
    ap.add_argument("--pad", type=float, default=1.0, help="FIRMS box half-width, degrees")
    ap.add_argument("--days", type=int, default=1, help="FIRMS lookback days")
    ap.add_argument("--skip-km", type=float, default=25.0,
                    help="ignore plume points closer than this to the vent when scoring exposure")
    ap.add_argument("--slots", type=int, default=1,
                    help="how many 10-min AMV slots to pool (more = better coverage in the ash layer)")
    ap.add_argument("--offline", action="store_true",
                    help="reuse cached AMVs, skip S3 slot discovery")
    args = ap.parse_args()

    start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)

    # --- observed winds ---
    observations = []
    slot = None
    want = [b.strip() for b in args.bands.split(",")]
    if not args.offline:
        try:
            slots = amv_slots(args.slots)
            if slots:
                slot = slots[0][0]
            for sl_prefix, sl_time in slots:
                keys = pick_amv_files(sl_prefix)
                for tag in want:
                    if tag not in keys:
                        continue
                    try:
                        # cache filename must stay unique per slot+band
                        o = read_amv(keys[tag], args.cache, slot_tag=f"{sl_time:%H%M}")
                        if o:
                            o["meta"]["slot"] = f"{sl_time:%H:%M}Z"
                            observations.append(o)
                    except Exception as e:  # noqa: BLE001
                        print(f"[warn] AMV {tag} {sl_time:%H:%M}Z: {e}", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] could not reach NOAA S3: {e}", file=sys.stderr)
    if not observations:  # fall back to whatever is cached
        cache_files = sorted(
            (f for f in os.listdir(args.cache) if f.endswith(".nc"))
            if os.path.isdir(args.cache) else [])
        for fname in cache_files:
            path = os.path.join(args.cache, fname)
            tag = fname.split("_")[0].replace(".nc", "")
            try:
                o = _read_cached(path, tag)
                if o:
                    o["meta"]["slot"] = fname
                    observations.append(o)
            except Exception:
                pass

    # --- NWP background: winds + temperatures + 3-model ensemble, one call ---
    nwp = None
    try:
        nwp = nwp_profile(args.lat, args.lon, int(args.hours))
    except Exception as e:  # noqa: BLE001
        print(f"[error] open-meteo: {e}", file=sys.stderr)
    heights = level_heights_km(nwp) if nwp else {}

    # --- weighted AMV band statistics, blended with the NWP background ---
    amv_rows = amv_profile(observations, args.lat, args.lon, args.radius,
                           args.qmin, nwp=nwp, heights=heights)

    # --- trajectories: blended wind held steady, evolving+settling+diffusion ---
    traj, traj_fc, traj_cls, env = {}, {}, {}, {}
    for r in amv_rows:
        if not r.get("data"):
            continue
        bu, bv = r["blended_u_ms"], r["blended_v_ms"]
        traj[r["layer"]] = integrate(args.lat, args.lon,
                                     lambda t, u=bu: u, lambda t, v=bv: v,
                                     start, args.hours)
        if nwp:
            h0 = r.get("alt_km") or r.get("mean_altitude_km") or 1.5
            cls = trajectory_settling(args.lat, args.lon, h0, nwp, heights,
                                      start, hours=args.hours)
            traj_fc[r["layer"]] = cls["fine"]
            traj_cls[r["layer"]] = {k: v for k, v in cls.items() if k != "fine"}
            env[r["layer"]] = envelope_polygon(cls["fine"])

    exp = exposure(args.lat, args.lon, traj, start, skip_km=args.skip_km)
    firms = firms_hotspots(args.firms_key, args.lat, args.lon, args.pad, args.days) if args.firms_key else {}

    if args.json:
        print(json.dumps({
            "volcano": args.name, "lat": args.lat, "lon": args.lon,
            "analysis_utc": start.isoformat(), "amv_slot": slot,
            "observations": [o["meta"] for o in observations],
            "observed_wind_profile": amv_rows,
            "forecast_wind": {str(k): v[:12] for k, v in (nwp or {}).get("levels", {}).items()},
            "level_heights_km": {str(k): round(v, 2) for k, v in heights.items()},
            "ensemble_dirs": {str(k): v for k, v in (nwp or {}).get("ensemble", {}).items()},
            "trajectories_observed": traj, "trajectories_forecast": traj_fc,
            "trajectories_settling": traj_cls, "envelopes": env,
            "settling_classes_ms": SETTLE_CLASSES, "diffusivity_m2_s": K_DIFFUSIVITY,
            "downwind_exposure": exp, "firms": firms,
        }, ensure_ascii=False, indent=2, default=str))
    else:
        print(render(args, amv_rows, nwp or {"times": [], "levels": {}}, traj, start, exp, firms, traj_fc))

    if not args.no_svg and traj:
        try:
            svg_map(args.lat, args.lon, {k: v for k, v in traj.items() if "observed" not in k},
                    amv_rows, path=args.svg)
            if not args.json:
                print(f"\n[map written to {args.svg}]")
        except Exception as e:  # noqa: BLE001
            print(f"[warn] svg: {e}", file=sys.stderr)
    return 0


def _read_cached(path, tag):
    ds = netCDF4.Dataset(path); ds.set_auto_maskandscale(False)
    g = lambda n: ds[n][:] if n in ds.variables else None
    lat, lon, spd, wd = g("Latitude"), g("Longitude"), g("Wind_Speed"), g("Wind_Dir")
    u1, v1, u2, v2 = g("UComponent1"), g("VComponent1"), g("UComponent2"), g("VComponent2")
    prs, qi, bt, ee = g("MedianPress"), g("QI"), g("MedianBT"), g("ExpectedErr")
    ok = lambda a, f=-999.0: np.isfinite(a) & (a != f)
    u = (np.where(ok(u1), u1, np.nan) + np.where(ok(u2), u2, np.nan)) / 2
    v = (np.where(ok(v1), v1, np.nan) + np.where(ok(v2), v2, np.nan)) / 2
    m = ok(spd) & (spd > 0.3) & ok(lat) & ok(lon) & ok(prs) & ok(qi, -98) & ok(u) & ok(v)
    idx = np.where(m)[0]
    meta = {"tag": tag, "band": getattr(ds, "sensor_band_identifier", None),
            "wavelength": getattr(ds, "sensor_band_central_radiation_wavelength", None),
            "window_start": str(getattr(ds, "time_coverage_start", "")),
            "window_end": str(getattr(ds, "time_coverage_end", "")), "n_global": int(m.sum())}
    ds.close()
    return {"meta": meta, "lat": lat[idx].astype(float), "lon": lon[idx].astype(float),
            "u": u[idx].astype(float), "v": v[idx].astype(float), "spd": spd[idx].astype(float),
            "dir": (wd[idx] % 360).astype(float), "prs": prs[idx].astype(float),
            "qi": qi[idx].astype(float), "bt": bt[idx].astype(float), "ee": ee[idx].astype(float)}


if __name__ == "__main__":
    sys.exit(main())
