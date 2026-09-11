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

ENVELOPE PHYSICS (v2, mass-coupled)
  The detectable-ash envelope is no longer a purely kinematic sqrt(2Kt)+Gt
  curve. That form is monotonically increasing and cannot reproduce the
  rise-then-shrink behaviour seen in real VAAC polygons (Darwin advisories
  2026/174-195: cross-track width grew to ~1,900 km, then decayed as ash
  settled out). The new model couples the Gaussian spread to the AIRBORNE
  MASS and to the along-track dilution:

    sigma_y^2(t) = sigma0^2 + 2K t + (s_perp*sigma_h*t)^2   cross-track spread
    sigma_x^2(t) = sigma0^2 + 2K t + (s_par  *sigma_h*t)^2   along-track spread
    Phi(t)       = sum_c f_c S_c(t) W_c(t)                   airborne fraction
    Phi_det_eff(t) = PHI_DET * sigma_x sigma_y / sigma0^2     diluted threshold
    w(t) = sigma_y * sqrt(2 ln(Phi/Phi_det_eff))              detectable half-width

  where S_c(t) is the settling survival of class c (mass released uniformly
  over the layer depth, density-corrected Stokes velocities), W_c(t) the
  below-cloud wet scavenging factor, and s_par/s_perp the wind shear across
  the layer decomposed along/across the mean motion (from the NWP profile —
  this replaces the old ad-hoc linear growth constant G).

  CALIBRATION PROVENANCE (be honest, n is tiny):
    K, PHI_DET, SIGMA0_KM and SHEAR_DAMPING were calibrated on the four
    polygon widths of Darwin advisory 2026/209 (10 Sep 2026, SFC/FL050 layer)
    with cloud age t0 free — see calibrate_envelope.py. Fit RMS 1.4 km vs
    12.2 km for the best physically-admissible (G>=0) OLD-form fit, and the
    old form only reaches 4.4 km by driving its growth term NEGATIVE
    (G = -1.02 m/s), which is unphysical. Independent consistency check: the
    fitted t0 = 8.8 h at the 1040Z observation implies emission ~0210Z —
    exactly when MAGMA logged the 09:10 WIB eruption that morning. ONE event;
    treat the numbers as a defensible starting point, not universal truth,
    and re-fit when the backtest ledger (site/data/backtest.jsonl) grows.

  EMISSION HISTORY (v2.1)
    The calibration above treats the cloud age t0 as a free parameter, and
    its optimum "rediscovered" the real eruption time. Production runs used
    to ignore that entirely: every cloud started at t=0, i.e. assumed the
    ash was emitted AT the analysis time — silently discarding the emission
    history the OBS polygon carries. Now the age is DERIVED per run from the
    advisory's own OBS polygon: invert 2*w(t) = W_obs on the RISING branch
    (bisection) using the layer's measured shear and survival Phi, then
    shift the whole envelope time axis to the implied emission. The initial
    spread becomes emission-history-aware,
        sigma_eff^2(analysis) = sigma0^2 + 2K t_age + (s_perp t_age)^2,
    and Phi(analysis) < 1: mass that settled out before the analysis is
    gone. Caveats, stated plainly: a decaying cloud is age-ambiguous (the
    width curve rises then falls) so the younger, conservative reading is
    taken; rain before the analysis time is unknowable from forecast data,
    so W_c only covers the forecast window; one age is applied to every
    band (the OBS polygon describes the whole detected cloud). Wiring:
        --obs-polygon "lat,lon;lat,lon;..."  (Darwin advisory OBS vertices)
        --obs-mov-deg NW|315                (motion for the width projection)
        --obs-layer-km "0,1.52"             (OBS layer depth; default: lowest
                                              AMV band that has data)
        --emission-age-h 8.8                (manual override, e.g. MAGMA log)
    build_site.py passes the first three automatically from the fetched
    advisory; with none of them the model keeps the fresh-emission t=0.

  UNION MULTI-BAND (v2.1)
    Per-band envelopes answer "where is THIS layer's detectable ash". The
    polygons Darwin draws, however, are unions across layers — on the 4-6
    Sep 2026 paroxysm the SFC/FL500 union fan reached ~1,900 km while no
    single band was wider than ~900 km. envelope_union returns the convex
    hull of all band envelope polygons (conservative: hull >= true union),
    as a GeoJSON-ready lon/lat ring plus area, and the SVG map draws it.

Requires:  numpy, netCDF4      (pip install numpy netCDF4)
Optional:  NASA FIRMS hotspots (needs a free MAP_KEY, see --firms-key)

Usage:
    python3 ash_transport.py                       # profile + trajectories + SVG
    python3 ash_transport.py --radius 5 --hours 12
    python3 ash_transport.py --json
    python3 ash_transport.py --lat -6.102 --lon 105.423 --name "Anak Krakatau"
    python3 ash_transport.py --volcano Sinabung   # resolve from src/volcanoes.py

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

# netCDF4 is only needed when reading Himawari-9 AMV files (read_amv /
# _read_cached); the pure math helpers (envelope, widths, union) must stay
# importable without it — build_site.py relies on that for its width ledger.
try:
    import netCDF4
except ImportError:                             # pragma: no cover
    netCDF4 = None

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
    if netCDF4 is None:
        raise RuntimeError("netCDF4 not installed — cannot read AMV files "
                           "(pip install netCDF4)")
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
K_DIFFUSIVITY = 6.82e3    # m2/s cross-track eddy diffusivity. CALIBRATED n=1 on Darwin
                          # advisory 2026/209 (SFC/FL050, 10 Sep 2026) — see
                          # calibrate_envelope.py (fit RMS 1.4 km on 4 polygon widths).
                          # Notably the fit's free cloud-age parameter landed at
                          # t0 = 8.8 h at the 1040Z observation, i.e. emission at
                          # ~0210Z — exactly when MAGMA logged the 09:10 WIB eruption
                          # that morning. One event; re-fit as the ledger grows.
SETTLE_CLASSES = {"ultrafine": 0.002, "fine": 0.02, "medium": 0.12, "coarse": 0.60}
                          # m/s Stokes settling. "ultrafine" (~<4 um) is the class that
                          # satellites track for DAYS — it is what keeps the detectable
                          # envelope alive long after coarse/medium ash has landed.
CLASS_MASS_FRACTIONS = {"ultrafine": 0.35, "fine": 0.35, "medium": 0.20, "coarse": 0.10}
                          # order-level mass split for a vulcanian/strombolian andesitic
                          # plume; adjustable, sums to 1. Envelope is fairly insensitive
                          # to +/-0.10 shifts (checked in calibrate_envelope.py).
SIGMA0_KM = 2.0           # effective initial horizontal spread of the detectable
                          # cloud (eruption-column radius order; absorbs the pulsed
                          # emission history). CALIBRATED n=1; insensitive 1-3 km.
PHI_DET = 5.59e-4         # satellite detection threshold as fraction of the initial
                          # peak column concentration. CALIBRATED n=1 (see above) —
                          # IR detection of thin fine-ash clouds really is orders of
                          # magnitude below a dense fresh column, hence the small value.
N_SUBPARCELS = 12         # release heights sampled across the layer for S_c(t)
EMISSION_LAYER_FRACTION = 0.5
                          # eruption columns emplace fine ash near their top / neutral
                          # buoyancy level, not uniformly over the full advisory depth.
                          # Release (and shear) use the UPPER fraction of the layer.
                          # This matters: low-level directional shear can exceed
                          # 100 deg across the lowest 1.5 km (measured 10 Sep 2026),
                          # and full-depth uniform release would smear the cloud into
                          # an arc the satellite never sees.
SHEAR_DAMPING = 0.10      # effective-to-instantaneous shear ratio. CALIBRATED n=1
                          # at the LOWER bound: on this event the data preferred
                          # near-zero effective shear — the open-meteo low-level
                          # profile veers >100 deg across 1.4 km and disagrees with
                          # the VAAC-observed cloud motion (sublayer mean 278 deg vs
                          # VAAC 315 deg), so the instantaneous measurement carries
                          # analysis noise the cloud never experiences. Fit is
                          # insensitive across 0.10-0.25. For DEEP columns with
                          # well-analyzed upper-level shear this damping should be
                          # revisited (likely higher) when more events are ledgered.
MAX_EMISSION_AGE_H = 48.0 # cap for the OBS-polygon-derived cloud age. Beyond this
                          # the inverse problem saturates (the width curve has
                          # already peaked) and the honest answer is "older than
                          # the model can see", reported as the cap with a note.


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


def pressure_to_height(heights: dict, p_hpa: float) -> float:
    """Interpolate a pressure level onto the hypsometric height grid.
    Clamps to the outermost levels; falls back to the standard atmosphere.
    Pairing: ascending pressure <-> descending height, as np.interp needs."""
    if not heights or p_hpa is None or not np.isfinite(p_hpa):
        return float(pressure_to_km(p_hpa)) if p_hpa else 1.5
    ps = sorted(heights)                      # ascending pressure
    zs = [heights[p] for p in ps]             # descending height (correct pairing)
    return float(np.interp(p_hpa, ps, zs)) if ps else 1.5


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
        # within-band shear: wind at the band's edge pressures; the angle between
        # them is extra horizontal spread the mean vector cannot show
        shear = 0.0
        if nwp:
            lo_p = min(nwp["levels"], key=lambda p: abs(p - plo))
            hi_p = min(nwp["levels"], key=lambda p: abs(p - phi))
            rl, rh = nwp["levels"][lo_p], nwp["levels"][hi_p]
            if rl and rh:
                a0 = rl[0]["from_deg"]
                a1 = rh[0]["from_deg"]
                shear = abs((a0 - a1 + 180) % 360 - 180)
                unc = float(min(60.0, np.hypot(unc, shear)))
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
            "shear_spread_deg": round(shear, 1),
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


def rho_at(nwp, heights, h_km):
    """Air density at height h from the model's own p/T profile (kg/m3)."""
    pts = []
    for p, recs in (nwp or {}).get("levels", {}).items():
        if recs and recs[0].get("temp_c") is not None:
            t_k = recs[0]["temp_c"] + 273.15
            pts.append((heights[p], p * 100.0 / (287.05 * t_k)))
    if not pts:
        return 1.225 * np.exp(-h_km / 8.5)
    pts.sort()
    if h_km <= pts[0][0]:
        return pts[0][1]
    if h_km >= pts[-1][0]:
        return pts[-1][1]
    for (z0, r0), (z1, r1) in zip(pts, pts[1:]):
        if z0 <= h_km <= z1:
            w = (h_km - z0) / ((z1 - z0) or 1)
            return r0 * (1 - w) + r1 * w
    return pts[-1][1]


SCAV_COEF = 1e-4         # 1/s below-cloud scavenging per mm/h of rain (order-level)
RAIN_MIN_MM_H = 0.5


def precip_at(grid, lat, lon, t_idx):
    """Bilinear sample of the open-meteo precip grid at (lat, lon, time)."""
    if not grid:
        return 0.0
    lats, lons, cols = grid["lats"], grid["lons"], grid["series"]
    if not (min(lats) <= lat <= max(lats) and min(lons) <= lon <= max(lons)):
        return 0.0
    la = sorted(lats); lo = sorted(lons)
    i0 = max(i for i in range(len(la)) if la[i] <= lat) if lat >= la[0] else 0
    i1 = min(i0 + 1, len(la) - 1)
    j0 = max(j for j in range(len(lo)) if lo[j] <= lon) if lon >= lo[0] else 0
    j1 = min(j0 + 1, len(lo) - 1)
    wa = 0.0 if i1 == i0 else (lat - la[i0]) / (la[i1] - la[i0])
    wl = 0.0 if j1 == j0 else (lon - lo[j0]) / (lo[j1] - lo[j0])
    def val(i, j):
        ser = cols.get((la[i], lo[j])) or []
        return ser[t_idx] if t_idx < len(ser) else (ser[-1] if ser else 0.0)
    return ((val(i0, j0) * (1 - wa) + val(i1, j0) * wa) * (1 - wl) +
            (val(i0, j1) * (1 - wa) + val(i1, j1) * wa) * wl)


# ------------------------------------------------------- envelope v2 math
# Pure-math core of the mass-coupled envelope, kept free of I/O so the
# calibration script and the unit tests can exercise exactly what the
# production code runs.

def layer_shear_ms(nwp, heights, h_base_km, h_top_km):
    """Wind shear across the ash layer, decomposed along/across the mean wind.

    Returns (s_par_ms_per_km, s_perp_ms_per_km, mean_u, mean_v). Shear is the
    difference of the NWP wind at the layer top and base (analysis time). This
    is the data-driven replacement for the old constant G_PLUME_GROWTH: the
    advection-diffusion solution with linear shear gives an exact Gaussian
    whose along-track variance grows as (s_par*sigma_h*t)^2, so the linear
    growth the old code hacked in IS the shear term — but now it is measured,
    not guessed, and it dilutes the cloud instead of only widening it.
    """
    u_b, v_b = wind_at(nwp, heights, 0, max(h_base_km, 0.05))
    u_t, v_t = wind_at(nwp, heights, 0, max(h_top_km, 0.05))
    du, dv = u_t - u_b, v_t - v_b
    um, vm = (u_t + u_b) / 2.0, (v_t + v_b) / 2.0
    spd = float(np.hypot(um, vm))
    H = max(h_top_km - h_base_km, 0.1)
    if spd < 0.5:                      # calm: direction meaningless, use |shear| both ways
        s = float(np.hypot(du, dv)) / H
        return s, s, um, vm
    ux, uy = um / spd, vm / spd        # along-track unit vector
    s_par = float(du * ux + dv * uy) / H
    s_perp = float(-du * uy + dv * ux) / H
    return s_par, s_perp, um, vm


def _emission_sublayer(h_base_km, h_top_km):
    """Release-height bounds: the UPPER EMISSION_LAYER_FRACTION of the layer.
    Columns emplace fine ash near neutral buoyancy, near the cloud top."""
    h_base = max(h_base_km, 0.0)
    h_top = max(h_top_km, h_base + 0.1)
    h_lo = h_base + EMISSION_LAYER_FRACTION * (h_top - h_base)
    return h_lo, h_top


def effective_shear_ms(nwp, heights, h_base_km, h_top_km):
    """(s_par_eff, s_perp_eff) in m/s for envelope_width_series.

    Damped shear measured across the EMISSION SUBLAYER, times the std of the
    release-height distribution. Single code path shared by the production
    trajectory_settling, the calibrator and the tests."""
    e_lo, e_hi = _emission_sublayer(h_base_km, h_top_km)
    s_par, s_perp, _, _ = layer_shear_ms(nwp, heights, e_lo, e_hi)
    sigma_h_km = max(e_hi - e_lo, 0.05) / np.sqrt(12.0)
    return (s_par * SHEAR_DAMPING * sigma_h_km,
            s_perp * SHEAR_DAMPING * sigma_h_km)


def class_landing_times(v_settle, layer_base_km, layer_top_km, nwp=None,
                        heights=None, dt_min=10, t_max_h=72):
    """Landing time (hours) of each sub-parcel released uniformly over the
    EMISSION SUBLAYER (upper part of the given layer), for one settling class.
    Deterministic descent through the density-corrected Stokes profile
    v(h) = v0*sqrt(rho0/rho(h)).

    Returns a sorted ndarray of landing times (hours); parcels still aloft at
    t_max get t_max*10 (i.e. never land inside the horizon).
    """
    h_lo, h_hi = _emission_sublayer(layer_base_km, layer_top_km)
    rho0 = rho_at(nwp, heights, max((h_lo + h_hi) / 2.0, 0.05))
    h_rels = np.linspace(max(h_lo, 0.05), h_hi, N_SUBPARCELS)
    dt_h = dt_min / 60.0
    lands = []
    for h in h_rels:
        hh = float(h)
        t = 0.0
        for _ in range(int(t_max_h / dt_h)):
            rho_h = rho_at(nwp, heights, max(hh, 0.05))
            v_eff = v_settle * np.sqrt(rho0 / max(rho_h, 1e-6))
            hh -= v_eff * dt_h * 3600.0 / 1000.0
            t += dt_h
            if hh <= 0.05:
                break
        lands.append(t if hh <= 0.05 else t_max_h * 10.0)
    return np.sort(np.array(lands))


def survival_curve(t_hours, landing_times_h, n_sub=None):
    """S_c(t): fraction of the class still airborne. Piecewise-linear between
    sub-parcel landings (heights are uniform, so this approximates the true
    smooth survival well and stays monotone decreasing)."""
    n = n_sub or N_SUBPARCELS
    lt = np.asarray(landing_times_h, dtype=float)
    xs = np.concatenate(([0.0], lt))
    ys = np.concatenate(([1.0], [1.0 - (k + 1) / n for k in range(len(lt))]))
    ys = np.clip(ys, 0.0, 1.0)
    return np.interp(t_hours, xs, ys)


def airborne_fraction(t_hours, layer_base_km, layer_top_km,
                      wet_factor=None, nwp=None, heights=None):
    """Phi(t) — total airborne mass fraction = sum_c f_c S_c(t) W_c(t).

    wet_factor: optional array aligned with t_hours (per-step scavenging
    product). Pure math otherwise; used by trajectory_settling, the
    calibrator and the tests so all three agree by construction.
    """
    t = np.asarray(t_hours, dtype=float)
    phi = np.zeros_like(t)
    for cname, vs in SETTLE_CLASSES.items():
        lands = class_landing_times(vs, layer_base_km, layer_top_km,
                                    nwp=nwp, heights=heights)
        s = survival_curve(t, lands)
        w = wet_factor if wet_factor is not None else 1.0
        phi += CLASS_MASS_FRACTIONS.get(cname, 0.0) * s * w
    return np.clip(phi, 0.0, 1.0)


def envelope_width_series(t_hours, phi, s_par, s_perp,
                          k_m2s=K_DIFFUSIVITY, phi_det=PHI_DET,
                          sigma0_km=SIGMA0_KM):
    """Detectable cross-track half-width w(t) of the Gaussian cloud.

    sigma_y^2 = sigma0^2 + 2Kt + (s_perp sigma_h t)^2    cross-track spread
    sigma_x^2 = sigma0^2 + 2Kt + (s_par  sigma_h t)^2    along-track spread
    phi_det_eff = phi_det * sigma_x sigma_y / sigma0^2    diluted threshold
    w = sigma_y sqrt(2 ln(phi / phi_det_eff))  (0 once below threshold)

    sigma_h is the std of the release-height distribution across the layer;
    callers pass s_par/s_perp already multiplied by sigma_h (m/s), which keeps
    this function ignorant of layer geometry. Returns a dict of series for
    audit: sigma_x, sigma_y, phi_det_eff, width.
    """
    t = np.asarray(t_hours, dtype=float) * 3600.0        # s
    s0 = sigma0_km * 1000.0
    sy = np.sqrt(s0 ** 2 + 2.0 * k_m2s * t + (s_perp * t) ** 2) / 1000.0   # km
    sx = np.sqrt(s0 ** 2 + 2.0 * k_m2s * t + (s_par * t) ** 2) / 1000.0    # km
    phi = np.asarray(phi, dtype=float)
    det_eff = phi_det * (sx * sy) / (sigma0_km ** 2)
    ratio = phi / np.maximum(det_eff, 1e-12)
    ln = np.where(ratio > 1.0, 2.0 * np.log(np.maximum(ratio, 1.0 + 1e-12)), 0.0)
    w = sy * np.sqrt(ln)
    w = np.where(ratio > 1.0, w, 0.0)
    return {"sigma_x_km": sx, "sigma_y_km": sy,
            "phi_det_eff": det_eff, "width_km": w}


# ------------------------------------------------ emission-history inversion
# Pure math, shared by the production run, the calibrator and the tests.

def polygon_cross_track_width_km(polygon, motion_deg=None):
    """Full width (km) of a polygon perpendicular to the cloud motion.

    polygon: vertices as [lon, lat] pairs (GeoJSON order, what
    envelope_polygon emits) or {"lat","lon"} dicts (what darwin_vaac emits).
    motion_deg: direction the cloud travels TOWARD (0 = north, clockwise);
    when None the elongation axis is taken from the vertices' principal
    components instead. This is the same "width" the manual VAAC analysis
    used: the extent orthogonal to the track, which the mass-coupled model
    predicts as 2*w(t).
    """
    if not polygon or len(polygon) < 2:
        return 0.0
    lons, lats = [], []
    for p in polygon:
        if isinstance(p, dict):
            lons.append(float(p["lon"])); lats.append(float(p["lat"]))
        else:
            lons.append(float(p[0])); lats.append(float(p[1]))
    lons, lats = np.asarray(lons), np.asarray(lats)
    lat0 = float(np.mean(lats))
    # local equirectangular plane (km): good to sub-1% over VAAC-polygon sizes
    x = (lons - lons.mean()) * 111.32 * np.cos(np.radians(lat0))
    y = (lats - lats.mean()) * 111.32
    if motion_deg is not None:
        th = np.radians(float(motion_deg) % 360.0)
        # unit along-track vector in (east, north); project onto its normal
        ex, ny = np.sin(th), np.cos(th)
        s = x * ny - y * ex
        return float(s.max() - s.min())
    # PCA: the minor principal axis is the cross-track direction of an
    # elongated cloud even when no motion vector was parsed
    pts = np.column_stack([x, y])
    cov = np.cov(pts.T, bias=True) if len(pts) > 1 else np.zeros((2, 2))
    evals, evecs = np.linalg.eigh(cov)
    axis = evecs[:, int(np.argmin(evals))]          # minor axis
    s = pts @ axis
    return float(s.max() - s.min())


def implied_emission_age_h(obs_width_km, s_par, s_perp, layer_base_km,
                           layer_top_km, nwp=None, heights=None,
                           k_m2s=K_DIFFUSIVITY, phi_det=PHI_DET,
                           sigma0_km=SIGMA0_KM, t_max_h=MAX_EMISSION_AGE_H):
    """Cloud age (h) implied by the OBS polygon's cross-track width.

    Inverts the mass-coupled envelope: find t such that 2*w(t) = W_obs on
    the RISING branch (first crossing). Rising branch because a decaying
    cloud is age-ambiguous — the same width exists once while the envelope
    grows and once while it shrinks — and the younger reading is the
    conservative one (more mass still airborne). Phi uses the survival
    machinery only: rain BEFORE the analysis time is unknowable from
    forecast data, so no wet factor is applied on the pre-analysis segment.

    Returns (t_age_h, note). t_age = 0.0 when the width is already
    explainable by a fresh cloud; t_max_h with a note when the width exceeds
    anything the model produces inside the cap (older than the model can
    see, or wider than diffusion explains).
    """
    W = float(obs_width_km)
    if W <= 0.0:
        return 0.0, "no usable OBS width"
    t = np.linspace(0.0, t_max_h, 241)
    phi = airborne_fraction(t, layer_base_km, layer_top_km,
                            nwp=nwp, heights=heights)
    env = envelope_width_series(t, phi, s_par, s_perp,
                                k_m2s=k_m2s, phi_det=phi_det, sigma0_km=sigma0_km)
    full = 2.0 * env["width_km"]
    if W <= full[0] + 1e-9:
        return 0.0, "fresh cloud: width explainable at t~0"
    above = np.where(full >= W)[0]
    if not len(above):
        return float(t_max_h), (f"OBS width {W:.0f} km exceeds the model "
                                f"maximum {full.max():.0f} km within {t_max_h:.0f} h; "
                                "capped (older than the model can see)")
    i = int(above[0])
    lo, hi = t[i - 1], t[i]                        # bracket around the crossing
    for _ in range(40):                            # bisection refinement
        mid = 0.5 * (lo + hi)
        pm = airborne_fraction(np.array([mid]), layer_base_km, layer_top_km,
                               nwp=nwp, heights=heights)
        wm = envelope_width_series(np.array([mid]), pm, s_par, s_perp,
                                   k_m2s=k_m2s, phi_det=phi_det,
                                   sigma0_km=sigma0_km)["width_km"][0]
        if 2.0 * wm >= W:
            hi = mid
        else:
            lo = mid
    return float(hi), "first crossing of 2*w(t)=W_obs on the rising branch"


# ------------------------------------------------------ union multi-band

def convex_hull(points):
    """Andrew's monotone chain. points: iterable of (lon, lat). Returns the
    hull as a counter-clockwise [lon, lat] ring without repeating the start."""
    pts = sorted({(float(p[0]), float(p[1])) for p in points})
    if len(pts) <= 2:
        return [list(p) for p in pts]

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return [list(p) for p in lower[:-1] + upper[:-1]]


def _ring_area_km2(ring):
    """Shoelace area on a local equirectangular projection (km^2)."""
    if not ring or len(ring) < 3:
        return 0.0
    lats = [p[1] for p in ring]
    lat0 = float(np.mean(lats))
    kx = 111.32 * np.cos(np.radians(lat0))
    a = 0.0
    for (x0, y0), (x1, y1) in zip(ring, ring[1:] + [ring[0]]):
        a += (x0 * kx) * (y1 * 111.32) - (x1 * kx) * (y0 * 111.32)
    return abs(a) / 2.0


def _parse_obs_polygon(s):
    """CLI --obs-polygon value -> [[lon, lat], ...].

    Accepts 'lat,lon;lat,lon;...' (the natural order for VAAC/MAGMA coords)
    or a JSON array of [lat, lon] pairs / {"lat","lon"} dicts (what
    darwin_vaac.py emits). Returns [] when nothing usable parses.
    """
    if not s:
        return []
    s = s.strip()
    try:                       # JSON form first: dicts are unambiguous
        j = json.loads(s)
        if isinstance(j, list) and j:
            out = []
            for p in j:
                if isinstance(p, dict) and "lat" in p and "lon" in p:
                    out.append([float(p["lon"]), float(p["lat"])])
                elif isinstance(p, (list, tuple)) and len(p) >= 2:
                    out.append([float(p[1]), float(p[0])])   # [lat, lon]
            return out
    except (ValueError, TypeError):
        pass
    out = []
    for chunk in s.split(";"):
        parts = [x.strip() for x in chunk.split(",") if x.strip()]
        if len(parts) == 2:
            try:
                out.append([float(parts[1]), float(parts[0])])  # lat, lon
            except ValueError:
                continue
    return out


def _parse_mov(s):
    """--obs-mov-deg value: compass name (NW) or degrees (315) -> float deg."""
    if not s:
        return None
    s = str(s).strip()
    if s.upper() in COMPASS:
        return float(COMPASS.index(s.upper()) * 22.5)
    try:
        return float(s) % 360.0
    except ValueError:
        return None


def union_envelope(envelopes):
    """Combined polygon across bands: convex hull of every band envelope.

    envelopes: {layer_label: [[lon, lat], ...]} (per-band detectable
    envelopes). The hull is CONSERVATIVE — it contains the true union and
    fills concavities between separated bands — which is the right bias for
    a monitoring product, and is the model-side counterpart of the
    multi-layer polygons Darwin draws (e.g. the SFC/FL500 fan of 4-6 Sep
    2026). Returns None when no band has a usable polygon; otherwise a dict
    with the hull ring, band list, area and method provenance.
    """
    usable = {k: v for k, v in (envelopes or {}).items()
              if v and len(v) >= 3}
    if not usable:
        return None
    hull = convex_hull([p for v in usable.values() for p in v])
    if len(hull) < 3:
        return None
    return {
        "polygon": [[round(p[0], 4), round(p[1], 4)] for p in hull],
        "bands": sorted(usable.keys()),
        "n_bands": len(usable),
        "area_km2": round(_ring_area_km2(hull), 0),
        "method": "convex hull of per-band detectable envelopes "
                  "(conservative: contains the true union)",
    }


def trajectory_settling(lat0, lon0, h0_km, nwp, heights, start, hours=12,
                        dt_min=10, precip_grid=None, layer_km=None,
                        t_age_h=0.0):
    """Advect the settling classes through the evolving, height-interpolated
    NWP wind field, and build the MASS-COUPLED detectable-ash envelope.

    Physics (envelope v2 — see module docstring):
      * settling velocity corrected for air density: v(h) = v0*sqrt(rho0/rho(h));
      * per-class AIRBORNE mass: class mass is released uniformly over the
        layer depth [layer_km[0], layer_km[1]] (default h0 +/- 0.5 km), each
        sub-parcel lands when its density-corrected descent reaches the
        surface, so S_c(t) is a true survival curve — mass that has landed is
        no longer in the cloud, and the envelope shrinks with it;
      * below-cloud wet deposition where sampled rain >= 0.5 mm/h (unchanged);
      * cross-track spread sigma_y = sqrt(sigma0^2 + 2Kt + (s_perp sigma_h t)^2)
        and along-track dilution sigma_x = sqrt(sigma0^2 + 2Kt + (s_par sigma_h t)^2)
        with the shear measured from the NWP profile across the layer (this
        replaces the old ad-hoc G*t term);
      * detectable width w(t) = sigma_y * sqrt(2 ln(Phi/Phi_det_eff)) with
        Phi_det_eff = PHI_DET * sigma_x sigma_y / sigma0^2 — the threshold a
        satellite sees RISES as the cloud dilutes, which is what makes the
        envelope able to grow first and then shrink, matching real VAAC
        polygon behaviour (advisories 2026/174-195).

    EMISSION HISTORY (v2.1): t_age_h is the cloud age at the analysis time,
    derived from the advisory's OBS polygon by implied_emission_age_h (or
    set manually). The envelope time axis is shifted to the implied
    EMISSION: sigma, Phi and w are all evaluated at t + t_age_h, so the
    analysis point already carries the accumulated spread
    sigma0^2 + 2K*t_age (+ shear) and the mass fraction that settled out
    before the analysis is gone. Wet scavenging only covers the forecast
    window (rain before the analysis is unknowable from forecast data).

    Returns per class: pts (with sigma_km, width_km, phi), wet_points,
    mass_remaining (airborne fraction of that class) — plus "envelope"
    diagnostics for the fine class.
    """
    out = {}
    steps = int(hours * 60 / dt_min)
    rho0 = rho_at(nwp, heights, h0_km)
    h_base = (layer_km[0] if layer_km else h0_km - 0.5)
    h_top = (layer_km[1] if layer_km else h0_km + 0.5)
    h_base = max(min(h_base, h0_km), 0.0)
    h_top = max(h_top, h0_km, 0.1)

    # measured shear across the EMISSION SUBLAYER, damped for nonstationarity
    s_par, s_perp = effective_shear_ms(nwp, heights, h_base, h_top)
    e_lo, e_hi = _emission_sublayer(h_base, h_top)

    t_hours_arr = np.array([(i + 1) * dt_min / 60.0 for i in range(steps)])
    # emission-history-aware absolute cloud age: forecast offset + age at
    # analysis. sigma/Phi/w below are all functions of THIS axis.
    t_abs = t_hours_arr + float(t_age_h)
    # wet scavenging is shared geometry (the classes ride nearly the same track);
    # covers the FORECAST window only — pre-analysis rain is unknowable
    wet_series = np.ones(steps)

    for cname, vs in SETTLE_CLASSES.items():
        lat, lon, h = lat0, lon0, h0_km
        wet = []
        pts = [{"lat": round(lat, 4), "lon": round(lon, 4), "hours": 0.0,
                "alt_km": round(h, 2), "sigma_km": SIGMA0_KM,
                "width_km": SIGMA0_KM, "phi": 1.0}]   # patched to analysis values below
        lands = class_landing_times(vs, h_base, h_top, nwp=nwp, heights=heights)
        surv = survival_curve(t_abs, lands)      # absolute age: pre-analysis
                                                # settling is already gone
        for i in range(steps):
            # nwp["times"] is hourly; i is a dt_min-minute substep counter, so
            # convert elapsed minutes -> hour bucket instead of indexing by i
            # directly (that made t_idx race through 12h of data in ~2h, then
            # freeze at the last hourly value for the rest of the run).
            t_idx = min((i * dt_min) // 60, len(nwp["times"]) - 1)
            u, v = wind_at(nwp, heights, t_idx, max(h, 0.05))
            dt = dt_min * 60.0
            lat += (v * dt) / 111320.0
            lon += (u * dt) / (111320.0 * max(np.cos(np.radians(lat)), 1e-6))
            rho_h = rho_at(nwp, heights, max(h, 0.05))
            v_eff = vs * np.sqrt(rho0 / max(rho_h, 1e-6))
            h = max(0.05, h - v_eff * dt / 1000.0)
            rate = precip_at(precip_grid, lat, lon, t_idx) if precip_grid else 0.0
            if rate >= RAIN_MIN_MM_H:
                wet_series[i] *= np.exp(-SCAV_COEF * min(rate, 5.0) * dt)
                if cname == "fine":
                    wet.append({"hours": round((i + 1) * dt_min / 60.0, 2),
                                "lat": round(lat, 4), "lon": round(lon, 4),
                                "rate_mm_h": round(rate, 1)})
            pts.append({"lat": round(lat, 4), "lon": round(lon, 4),
                        "hours": round((i + 1) * dt_min / 60.0, 2),
                        "alt_km": round(h, 2)})
        out[cname] = {"pts": pts, "wet_points": wet,
                      "landing_times_h": [round(float(x), 2) for x in lands],
                      "survival_end": round(float(surv[-1]), 3)}

    # Phi(t): all classes at ABSOLUTE cloud age, shared wet factor along the
    # forecast track (settling before the analysis is already inside S_c)
    phi_series = np.zeros(steps)
    for cname in SETTLE_CLASSES:
        lands = np.array(out[cname]["landing_times_h"])
        s = survival_curve(t_abs, lands)
        phi_series += CLASS_MASS_FRACTIONS.get(cname, 0.0) * s * wet_series
    env = envelope_width_series(t_abs, phi_series, s_par, s_perp)

    # analysis-time state (cloud age t_age): the emission-history-aware
    # initial condition the OBS polygon actually saw
    phi0 = float(airborne_fraction(np.array([max(t_age_h, 0.0)]), h_base, h_top,
                                    nwp=nwp, heights=heights)[0])
    env0 = envelope_width_series(np.array([max(t_age_h, 0.0)]), np.array([phi0]),
                                 s_par, s_perp)

    # attach the envelope to the fine-class points (the tracked centerline)
    fine_pts = out["fine"]["pts"]
    fine_pts[0]["sigma_km"] = round(float(env0["sigma_y_km"][0]), 1)
    fine_pts[0]["width_km"] = round(float(env0["width_km"][0]), 1)
    fine_pts[0]["phi"] = round(phi0, 3)
    for i in range(steps):
        p = fine_pts[i + 1]
        p["sigma_km"] = round(float(env["sigma_y_km"][i]), 1)
        p["width_km"] = round(float(env["width_km"][i]), 1)
        p["phi"] = round(float(phi_series[i]), 3)
    out["fine"]["envelope"] = {
        "model": "mass-coupled gaussian v2",
        "k_m2_s": K_DIFFUSIVITY, "phi_det": PHI_DET, "sigma0_km": SIGMA0_KM,
        "shear_par_ms": round(float(s_par), 3), "shear_perp_ms": round(float(s_perp), 3),
        "layer_km": [round(h_base, 2), round(h_top, 2)],
        "emission_sublayer_km": [round(e_lo, 2), round(e_hi, 2)],
        "emission_age_h": round(float(t_age_h), 2),
        "analysis_width_km": round(float(env0["width_km"][0]), 1),
        "analysis_phi": round(phi0, 3),
        "class_mass_fractions": dict(CLASS_MASS_FRACTIONS),
        "width_end_km": round(float(env["width_km"][-1]), 1),
        "phi_end": round(float(phi_series[-1]), 3),
    }
    # airborne mass remaining per class (settling survival x wet), 0..1
    for cname in SETTLE_CLASSES:
        s_end = out[cname]["survival_end"]
        out[cname]["mass_remaining"] = round(
            float(np.clip(s_end * wet_series[-1], 0.0, 1.0)), 3)
    return out


def envelope_polygon(fine_pts):
    """Cross-track buffer polygon from the DETECTABLE width at each point.

    Uses "width_km" (mass-coupled envelope v2) when present, falling back to
    "sigma_km" so old cached trajectories still render. Widths below the
    detection threshold collapse to ~0 and the polygon degenerates toward the
    centerline — that is the intended behaviour: no detectable ash, no
    envelope."""
    left, right = [], []
    for i, pt in enumerate(fine_pts):
        nxt = fine_pts[min(i + 1, len(fine_pts) - 1)]
        prv = fine_pts[max(i - 1, 0)]
        dlon = nxt["lon"] - prv["lon"]
        dlat = nxt["lat"] - prv["lat"]
        norm = np.hypot(dlon, dlat) or 1e-9
        px, py = -dlat / norm, dlon / norm
        sg = pt.get("width_km", pt.get("sigma_km", 0.0))
        dlat_s = sg / 111.32
        dlon_s = sg / (111.32 * max(np.cos(np.radians(pt["lat"])), 1e-6))
        left.append([round(float(pt["lon"] + px * dlon_s), 4), round(float(pt["lat"] + py * dlat_s), 4)])
        right.append([round(float(pt["lon"] - px * dlon_s), 4), round(float(pt["lat"] - py * dlat_s), 4)])
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
def svg_map(lat, lon, traj_by_level, amv_rows, radius=6.0, path="ash_map.svg",
           union_poly=None):
    """Self-contained SVG: plume trajectories + observed wind barbs + the
    combined (union) detectable envelope across bands."""
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
    # combined detectable envelope (union across bands), behind everything
    # else: conservative hull, dashed outline, barely-there fill
    if union_poly and len(union_poly) >= 3:
        ring = [(X(p[0]), Y(p[1])) for p in union_poly
                if lo0 - 3 <= p[0] <= lo1 + 3 and la1 - 3 <= p[1] <= la0 + 3]
        if len(ring) >= 3:
            pts_u = " ".join(f"{x:.1f},{y:.1f}" for x, y in ring)
            p.append(f'<polygon points="{pts_u}" fill="#58a6ff" fill-opacity="0.05" '
                     f'stroke="#58a6ff" stroke-width="1.2" stroke-dasharray="6 4" '
                     f'stroke-opacity="0.55"/>')
            ux, uy = ring[0]
            p.append(f'<text x="{ux + 6:.1f}" y="{uy - 6:.1f}" fill="#58a6ff" '
                     f'font-size="10" opacity="0.8">combined detectable envelope '
                     f'(union of bands)</text>')
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


def render(args, amv_rows, om, traj, start, exposure_rows, firms, traj_fc=None,
           emission=None, union_env=None):
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

    if traj_fc:
        L.append("\n\n  ENVELOPE — detectable-ash width, mass-coupled (v2)")
        L.append("  (shrinks as mass settles out; 0 = below satellite detection)\n")
        for lab, tr in traj_fc.items():
            if len(tr) < 2:
                continue
            e = tr[-1]
            w = e.get("width_km"); ph = e.get("phi")
            if w is None:
                continue
            L.append(f"   {lab:24} width {w:6.1f} km at +{e['hours']:g}h   "
                     f"airborne mass {ph if ph is not None else float('nan'):.2f}")
        L.append("\n  width = cross-track extent of ash above the satellite detection")
        L.append("  threshold, NOT the old kinematic 2*sigma. Grows while the cloud")
        L.append("  spreads faster than it dilutes, then shrinks as classes settle.")
        L.append("  Calibrated n=1 (Darwin 2026/209). Re-fit as ledger grows.")
        if emission:
            L.append("")
            L.append(f"  emission history: OBS width {emission.get('obs_width_km', '—')} km"
                     f"{' at ' + str(emission['motion_deg']) + '°' if emission.get('motion_deg') is not None else ''}")
            L.append(f"  -> cloud age {emission.get('emission_age_h', 0.0):.1f} h at analysis "
                     f"({emission.get('source', '')})")
            L.append(f"  {emission.get('note', '')}")
        if union_env:
            L.append("")
            L.append(f"  combined union envelope: {union_env['n_bands']} band(s), "
                     f"area {union_env['area_km2']:,.0f} km2 "
                     f"(convex hull — conservative, contains the true union)")

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
    ap = argparse.ArgumentParser(description="Ash transport for a volcano "
                                             "(default: the registry's primary entry)")
    ap.add_argument("--volcano", default=None,
                    help="resolve lat/lon/name from src/volcanoes.py (e.g. 'Sinabung'); "
                         "unknown names fail loudly")
    ap.add_argument("--lat", type=float, default=None,
                    help="vent latitude (default: registry / Anak Krakatau)")
    ap.add_argument("--lon", type=float, default=None,
                    help="vent longitude (default: registry / Anak Krakatau)")
    ap.add_argument("--name", default=None,
                    help="display name (default: registry / Anak Krakatau)")
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
    ap.add_argument("--obs-polygon", default=None,
                    help="Darwin advisory OBS polygon: 'lat,lon;lat,lon;...' or a JSON "
                         "array of {lat,lon} dicts — derives the emission-history-aware "
                         "cloud age (v2.1). build_site.py passes this automatically")
    ap.add_argument("--obs-mov-deg", default=None,
                    help="cloud motion for the width projection: compass (NW) or degrees")
    ap.add_argument("--obs-layer-km", default=None,
                    help="OBS layer depth 'base,top' km (e.g. '0,1.52' for SFC/FL050); "
                         "default: the lowest AMV band that has data")
    ap.add_argument("--emission-age-h", type=float, default=None,
                    help="manual cloud age at analysis (h), e.g. from the MAGMA eruption "
                         "log — overrides the OBS-polygon derivation")
    args = ap.parse_args()

    # --- resolve the vent from the registry (src/volcanoes.py) --------------
    # Explicit --lat/--lon/--name win; otherwise --volcano (or the registry's
    # primary entry) fills them. The import is lazy so the pure-math helpers
    # above stay importable even when this file is vendored alone.
    if args.lat is None or args.lon is None or args.name is None:
        try:
            import volcanoes
            base = volcanoes.resolve(args.volcano) if args.volcano else volcanoes.primary()
        except ImportError:
            base = {"lat": -6.102, "lon": 105.423, "name": "Anak Krakatau"}
        if args.lat is None:
            args.lat = base["lat"]
        if args.lon is None:
            args.lon = base["lon"]
        if args.name is None:
            args.name = base["name"]

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

    precip = None
    if nwp:
        try:
            lats = [-10, -8, -6, -4, -2]
            lons = [100, 102, 104, 106, 108, 110]
            url = ("https://api.open-meteo.com/v1/forecast?latitude=" +
                   ",".join(str(x) for x in lats) + "&longitude=" +
                   ",".join(str(x) for x in lons) +
                   f"&timezone=GMT&forecast_hours={int(args.hours)}&hourly=precipitation")
            d = json.loads(http_get(url).decode("utf-8"))
            h = d["hourly"]
            n = len(h["time"])
            series = {}
            flat = h.get("precipitation")
            if isinstance(flat, list) and len(flat) == n * len(lats) * len(lons):
                k = 0
                for la in lats:
                    for lo in lons:
                        series[(la, lo)] = flat[k:k + n]
                        k += n
            precip = {"lats": lats, "lons": lons, "series": series}
        except Exception as e:  # noqa: BLE001
            print(f"[warn] precip grid: {str(e)[:80]}", file=sys.stderr)

    # --- weighted AMV band statistics, blended with the NWP background ---
    amv_rows = amv_profile(observations, args.lat, args.lon, args.radius,
                           args.qmin, nwp=nwp, heights=heights)

    # --- trajectories: blended wind held steady, evolving+settling+envelope ---
    traj, traj_fc, traj_cls, env = {}, {}, {}, {}

    def _layer_km_from_row(r):
        """Band pressure edges -> (base_km, top_km) via the hypsometric heights."""
        if not heights:
            return None
        plo, phi = r.get("pressure_range", (None, None))
        if plo is None or phi is None:
            return None
        return (pressure_to_height(heights, phi),      # top of band (lower hPa)
                pressure_to_height(heights, plo))       # base of band (higher hPa)

    # --- emission history (v2.1): how old was the cloud at analysis time? ---
    # The calibration's free t0 "rediscovered" the 0210Z eruption; deriving the
    # age per run from the advisory's own OBS polygon makes the initial spread
    # emission-history-aware instead of silently assuming a fresh emission.
    emission = None
    t_age = 0.0
    if args.emission_age_h is not None:
        t_age = float(np.clip(args.emission_age_h, 0.0, MAX_EMISSION_AGE_H))
        emission = {"source": "manual --emission-age-h",
                    "emission_age_h": round(t_age, 2),
                    "note": "operator-supplied age (e.g. from the MAGMA eruption log)"}
    elif args.obs_polygon:
        verts = _parse_obs_polygon(args.obs_polygon)
        mov = _parse_mov(args.obs_mov_deg)
        W = polygon_cross_track_width_km(verts, mov) if len(verts) >= 3 else 0.0
        if W <= 0.0:
            print("[warn] --obs-polygon unparseable; fresh-emission envelope",
                  file=sys.stderr)
        else:
            if args.obs_layer_km:
                try:
                    base, top = (float(x) for x in args.obs_layer_km.split(",")[:2])
                except ValueError:
                    base = top = None
            else:
                lk = None
                for r in amv_rows:
                    if r.get("data"):
                        lk = _layer_km_from_row(r)
                        break
                base, top = lk if lk else (None, None)
            if base is None or top is None or not nwp:
                emission = {"source": "OBS polygon (Darwin advisory)",
                            "obs_width_km": round(W, 1), "motion_deg": mov,
                            "emission_age_h": 0.0,
                            "note": "no NWP profile / layer depth: age inversion "
                                    "skipped, fresh-emission envelope"}
            else:
                base, top = max(min(base, top), 0.0), max(top, min(base, top) + 0.1)
                sp, sq = effective_shear_ms(nwp, heights, base, top)
                t_age, note = implied_emission_age_h(
                    W, sp, sq, base, top, nwp=nwp, heights=heights)
                emission = {"source": "OBS polygon (Darwin advisory)",
                            "obs_width_km": round(W, 1), "motion_deg": mov,
                            "layer_km": [round(base, 2), round(top, 2)],
                            "emission_age_h": round(t_age, 2), "note": note}
    if emission is None:
        emission = {"source": "default (fresh emission)", "emission_age_h": 0.0,
                    "note": "no OBS polygon / age given: cloud assumed emitted "
                            "at the analysis time"}

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
                                      start, hours=args.hours,
                                      precip_grid=precip,
                                      layer_km=_layer_km_from_row(r),
                                      t_age_h=t_age)
            traj_fc[r["layer"]] = cls["fine"]["pts"]
            traj_cls[r["layer"]] = {k: {"pts": v["pts"],
                                        "wet_points": v["wet_points"],
                                        "mass_remaining": v["mass_remaining"]}
                                    for k, v in cls.items() if k != "fine"}
            wet_fine = cls["fine"]["wet_points"]
            traj_cls[r["layer"]]["fine_wet"] = {"wet_points": wet_fine,
                                                "mass_remaining": cls["fine"]["mass_remaining"]}
            env[r["layer"]] = envelope_polygon(cls["fine"]["pts"])
            traj_cls[r["layer"]]["envelope"] = cls["fine"].get("envelope")

    for r in amv_rows:
        if r.get("data") or not nwp:
            continue
        p_mid = (r["pressure_range"][0] + r["pressure_range"][1]) / 2
        near_p = min(heights, key=lambda p: abs(p - p_mid)) if heights else None
        h0 = heights[near_p] if near_p else 1.5
        cls = trajectory_settling(args.lat, args.lon, h0, nwp, heights,
                                  start, hours=args.hours, precip_grid=precip,
                                  layer_km=_layer_km_from_row(r), t_age_h=t_age)
        traj_fc[r["layer"]] = cls["fine"]["pts"]
        traj_cls[r["layer"]] = {k: {"pts": v["pts"], "wet_points": v["wet_points"],
                                    "mass_remaining": v["mass_remaining"]}
                                for k, v in cls.items() if k != "fine"}
        traj_cls[r["layer"]]["fine_wet"] = {"wet_points": cls["fine"]["wet_points"],
                                            "mass_remaining": cls["fine"]["mass_remaining"]}
        traj_cls[r["layer"]]["envelope"] = cls["fine"].get("envelope")
        env[r["layer"]] = envelope_polygon(cls["fine"]["pts"])
        r["nwp_only_trajectory"] = True

    exp = exposure(args.lat, args.lon, traj_fc or traj, start, skip_km=args.skip_km)
    firms = firms_hotspots(args.firms_key, args.lat, args.lon, args.pad, args.days) if args.firms_key else {}

    # combined multi-band polygon: the model-side counterpart of the union
    # polygons Darwin draws across layers (conservative convex hull)
    env_union = union_envelope(env)

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
            "envelope_union": env_union,
            "envelope_emission": emission,
            "settling_classes_ms": SETTLE_CLASSES,
            "class_mass_fractions": CLASS_MASS_FRACTIONS,
            "envelope_model": {
                "name": "mass-coupled gaussian v2",
                "k_m2_s": K_DIFFUSIVITY, "phi_det": PHI_DET,
                "sigma0_km": SIGMA0_KM,
                "emission_age_h": round(t_age, 2),
                "emission_age_source": emission.get("source"),
                "calibrated": "n=1 event, Darwin advisory 2026/209 (10 Sep 2026)"
                              " — see calibrate_envelope.py; re-fit as backtest grows",
            },
            "downwind_exposure": exp, "firms": firms,
        }, ensure_ascii=False, indent=2, default=str))
    else:
        print(render(args, amv_rows, nwp or {"times": [], "levels": {}}, traj, start,
                     exp, firms, traj_fc, emission=emission, union_env=env_union))

    if not args.no_svg and traj:
        try:
            svg_map(args.lat, args.lon, {k: v for k, v in traj.items() if "observed" not in k},
                    amv_rows, path=args.svg,
                    union_poly=(env_union or {}).get("polygon"))
            if not args.json:
                print(f"\n[map written to {args.svg}]")
        except Exception as e:  # noqa: BLE001
            print(f"[warn] svg: {e}", file=sys.stderr)
    return 0


def _read_cached(path, tag):
    if netCDF4 is None:
        raise RuntimeError("netCDF4 not installed — cannot read cached AMV "
                           "files (pip install netCDF4)")
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
