#!/usr/bin/env python3
"""
calibrate_envelope.py — fit the mass-coupled envelope (v2) to real VAAC widths.

WHAT THIS DOES
  Least-squares fit of (K, PHI_DET, t0, sigma0) in the envelope model
  implemented by ash_transport.py — the exact same code path the production
  run uses — to the four cross-track widths of ONE Darwin VAAC advisory:

      Darwin advisory 2026/209, issued 20260910/1100Z, layer SFC/FL050,
      OBS 10/1040Z moving NW 10 kt:
          OBS   +0 h : 93.9 km      (shoelace area 4957 km2)
          FCST  +6 h : 104.3 km     (5515 km2)
          FCST  +12h : 87.4 km      (4154 km2)
          FCST  +18h : 86.1 km      (4324 km2)
      Widths are the full polygon extent perpendicular to the motion vector,
      recomputed from the archived bulletin text (GDACS gts.aspx mirror,
      scripts/fvau_all.json). Regenerate with scripts/extract_eventB.py.

  Model width is 2*w(t) with t = t0 + offset, where t0 (cloud age at the OBS
  time) is a free parameter: the advisory does not state when the cloud was
  emitted. No rain was reported along the track for this event, so the wet
  scavenging factor is 1 (documented approximation).

  Shear for the SFC/FL050 emission sublayer comes from the open-meteo
  historical forecast at the vent, 2026-09-10 ~10:40Z (interpolated between
  the 10Z and 11Z hours):
      1000 hPa (0.15 km): FROM 164.5 deg at 9.0 m/s
       850 hPa (1.52 km): FROM  43.5 deg at 2.9 m/s
  Hardcoded below so the fit is offline-reproducible.

  For comparison, the OLD envelope form  2*(sqrt(2*K0*t) + G*t)  is also
  fitted with (G, t0) free at K0=5000 m2/s — the experiment that motivated
  this rewrite (best fit drives G negative, i.e. the form cannot produce
  rise-then-shrink) — and with (K0, t0) free at G=0.

HONEST CAVEATS (read before trusting the numbers)
  * n = 1 event. The fitted constants are a defensible starting point, not
    universal truth. Re-run this script as site/data/backtest.jsonl grows.
  * The +6/+12/+18 polygons are VAAC FORECASTS, not observations; they are
    the official best estimate of the cloud evolution and the right target
    for a forecast model, but they carry the VAAC's own model error.
  * The cloud age t0 is degenerate with K in the sqrt(2Kt) term; the fit
    trades them off within the ridge. Treat t0 as "effective age".
  * Continuous-emission events (like 2026/174-195) need an emission-rate
    integral this model does not have; do not calibrate on those.

USAGE
    python3 calibrate_envelope.py            # fit + residual table + JSON
    python3 calibrate_envelope.py --plot out.png
    python3 calibrate_envelope.py --sensitivity
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import types

import numpy as np

try:
    from scipy.optimize import differential_evolution, minimize
    _HAVE_SCIPY = True
except ImportError:
    _HAVE_SCIPY = False

# ash_transport imports netCDF4 at module level; the calibrator only uses the
# pure-math envelope functions, so stub the import when the lib is absent.
try:
    import netCDF4  # noqa: F401
except ImportError:
    sys.modules["netCDF4"] = types.ModuleType("netCDF4")

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.join(_HERE, "..", "src"), _HERE):
    if os.path.isfile(os.path.join(_p, "ash_transport.py")):
        sys.path.insert(0, _p)
        break
import ash_transport as at  # noqa: E402

# ------------------------------------------------------------------ data
EVENT = {
    "advisory": "2026/209",
    "dtg": "20260910/1100Z",
    "layer": "SFC/FL050",
    "layer_km": (0.0, 1.52),
    "t_offsets_h": np.array([0.0, 6.0, 12.0, 18.0]),
    "width_km": np.array([93.9, 104.3, 87.4, 86.1]),
    "area_km2": [4957.0, 5515.0, 4154.0, 4324.0],
}

# open-meteo historical forecast, vent location, 2026-09-10T10:40Z (10Z/11Z interp)
WINDS_LOW = [
    (1000, 0.15, 164.5, 9.0),    # (hPa, ~km, FROM deg, speed m/s)
    (850, 1.52, 43.5, 2.9),
]


def _shear_nwp():
    """Minimal nwp/heights dicts carrying the measured low-level profile,
    so at.layer_shear_ms walks the production code path."""
    levels = {}
    for p, z, frm, spd in WINDS_LOW:
        rec = {"t": "t0", "speed_ms": spd, "from_deg": frm,
               "u_ms": -spd * np.sin(np.radians(frm)),
               "v_ms": -spd * np.cos(np.radians(frm)),
               "temp_c": 30.0 - 6.5 * z}
        levels[p] = [rec]
    return {"times": ["t0"], "levels": levels}, {p: z for p, z, _, _ in WINDS_LOW}


NWP, HEIGHTS = _shear_nwp()
S_PAR, S_PERP = at.effective_shear_ms(NWP, HEIGHTS, *EVENT["layer_km"])

# Phi(t) does not depend on any fit parameter (no rain in this event), so
# compute the survival machinery ONCE on a dense grid and interpolate inside
# the optimizer — a ~1000x speedup of the differential-evolution run.
_T_GRID = np.linspace(0.0, 80.0, 1601)
_PHI_GRID = at.airborne_fraction(_T_GRID, *EVENT["layer_km"], nwp=NWP, heights=HEIGHTS)


def _phi_at(t):
    return np.interp(t, _T_GRID, _PHI_GRID)

# ------------------------------------------------------------------ models
def new_model_widths(k_m2s, phi_det, t0_h, t_off=None, sigma0_km=None,
                     s_par=None, s_perp=None):
    """2*w(t) of the mass-coupled envelope at ages t0 + offsets."""
    t = t0_h + (EVENT["t_offsets_h"] if t_off is None else np.asarray(t_off, float))
    phi = _phi_at(t)
    env = at.envelope_width_series(
        t, phi, S_PAR if s_par is None else s_par, S_PERP if s_perp is None else s_perp,
        k_m2s=k_m2s, phi_det=phi_det,
        sigma0_km=at.SIGMA0_KM if sigma0_km is None else sigma0_km)
    return 2.0 * env["width_km"]


def _shear_at_beta(beta):
    """Rescale the measured damped shear to a different damping ratio."""
    sp = S_PAR / at.SHEAR_DAMPING * beta
    sq = S_PERP / at.SHEAR_DAMPING * beta
    return sp, sq


def old_model_widths(g_ms, t0_h, k0=5e3, t_off=None):
    """2*sigma(t) of the OLD form: sqrt(2 K0 t) + G t  (G=0 allowed)."""
    t = (t0_h + (EVENT["t_offsets_h"] if t_off is None else np.asarray(t_off, float))) * 3600.0
    return 2.0 * (np.sqrt(2.0 * k0 * t) + g_ms * t) / 1000.0

def rms(res):
    return float(np.sqrt(np.mean(np.asarray(res) ** 2)))


# ------------------------------------------------------------------ fitting
def _pattern_search(f, x0, steps, shrink=0.5, iters=40):
    """Deterministic coordinate pattern search on f(x) -> min f."""
    x = np.array(x0, float)
    fx = f(x)
    for _ in range(iters):
        improved = False
        for i in range(len(x)):
            for s in (+1, -1):
                xt = x.copy()
                xt[i] += s * steps[i]
                try:
                    fxt = f(xt)
                except Exception:
                    continue
                if np.isfinite(fxt) and fxt < fx:
                    x, fx, improved = xt, fxt, True
        if not improved:
            steps = steps * shrink
            if np.all(steps < 1e-7):
                break
    return x, fx


def _global_min(obj, bounds, seed=7):
    """Differential evolution when scipy is present (this landscape is
    rugged), with numpy-only grid+pattern-search fallback."""
    if _HAVE_SCIPY:
        r = differential_evolution(obj, bounds, seed=seed, tol=1e-8,
                                   maxiter=300, popsize=20, polish=True)
        return np.asarray(r.x), float(r.fun)
    best, bestf = None, np.inf
    n = len(bounds)
    grid = [np.linspace(b[0], b[1], 7) for b in bounds]
    for combo in np.array(np.meshgrid(*grid)).reshape(n, -1).T:
        f = obj(combo)
        if f < bestf:
            bestf, best = f, combo
    steps = np.array([(b[1] - b[0]) / 12.0 for b in bounds])
    return _pattern_search(obj, best, steps)


def fit_new_model():
    """5 free params: K, PHI_DET, t0, sigma0, beta(shear damping).

    beta is the effective-to-instantaneous shear ratio: the measured profile
    overstates what the cloud experiences over hours (directional
    nonstationarity rotates the shear vector, and the low-level analysis
    itself disagrees with the VAAC-observed motion — sublayer mean 278 deg vs
    VAAC 315 deg on 10 Sep). Fitting beta instead of guessing it is the honest
    way to absorb that, and it stays inside physically sensible [0.1, 1].

    n=4 points, 5 params: this is a CALIBRATION (defensible starting values
    with physical interpretation), not a statistical fit. The structural
    claim — the old monotone form cannot follow rise-then-fall — does not
    depend on these values."""
    def unpack(p):
        return 10 ** p[0], 10 ** p[1], p[2], p[3], p[4]

    def obj(p):
        K, det, t0, s0, beta = unpack(p)
        sp, sq = _shear_at_beta(beta)
        w = new_model_widths(K, det, t0, sigma0_km=s0, s_par=sp, s_perp=sq)
        if not np.all(np.isfinite(w)):
            return 1e9
        if np.all(w <= 0.0):            # degenerate dead-cloud solution
            return 1e6 + float(np.sum((w - EVENT["width_km"]) ** 2))
        return float(np.sum((w - EVENT["width_km"]) ** 2))

    bounds = [(2.5, 5.5), (-4.0, -0.5), (1.0, 40.0), (1.0, 40.0), (0.1, 1.0)]
    p, fval = _global_min(obj, bounds)
    K, det, t0, s0, beta = unpack(p)
    sp, sq = _shear_at_beta(beta)
    w = new_model_widths(K, det, t0, sigma0_km=s0, s_par=sp, s_perp=sq)
    return {"k_m2_s": float(K), "phi_det": float(det), "t0_h": float(t0),
            "sigma0_km": float(s0), "shear_beta": float(beta),
            "sse": float(fval), "rms_km": rms(w - EVENT["width_km"])}


def fit_old_model(free=("g", "t0"), k0=5e3):
    """Best achievable OLD-form fits. free options:
       ('g','t0')    G and t0 free, K0=5000 (the motivating experiment)
       ('k','t0')    K0 and t0 free, G=0 (pure diffusion best)
       ('k','g','t0') all three free — the structurally best the old form can do
    """
    def _old_obj(g, t0, k0v=5e3):
        if not (-2.0 <= g <= 3.0 and 0.5 <= t0 <= 40.0):
            return 1e9
        r = old_model_widths(g, t0, k0=k0v) - EVENT["width_km"]
        return float(np.sum(r ** 2)) if np.all(np.isfinite(r)) else 1e9

    def grid3():
        best, bestf = None, np.inf
        for logk in np.arange(2.5, 5.6, 0.5):
            for g in np.arange(-1.0, 1.51, 0.25):
                for t0 in (3.0, 8.0, 15.0, 25.0, 37.0):
                    f = _old_obj(g, t0, 10 ** logk)
                    if f < bestf:
                        bestf, best = f, np.array([logk, g, t0])
        return best

    if free == ("k", "g", "t0"):
        def obj(p):
            return _old_obj(p[1], p[2], 10 ** p[0])
        p, _ = _global_min(obj, [(2.5, 6.0), (-2.0, 2.0), (0.5, 40.0)])
        k0v, g, t0 = 10 ** p[0], p[1], p[2]
        w = old_model_widths(g, t0, k0=k0v)
        return {"g_ms": float(g), "k0": float(k0v), "t0_h": float(t0),
                "rms_km": rms(w - EVENT["width_km"])}
    if free == ("k", "g", "t0", "gpos"):
        # physically admissible only: G >= 0 (a negative linear growth rate
        # is exactly the unphysical signal that motivated this rewrite)
        def obj(p):
            return _old_obj(p[1], p[2], 10 ** p[0])
        p, _ = _global_min(obj, [(2.5, 6.0), (0.0, 1.5), (0.5, 40.0)])
        k0v, g, t0 = 10 ** p[0], p[1], p[2]
        w = old_model_widths(g, t0, k0=k0v)
        return {"g_ms": float(g), "k0": float(k0v), "t0_h": float(t0),
                "rms_km": rms(w - EVENT["width_km"])}
    if free == ("g", "t0"):
        best, bestf = None, np.inf
        for g in np.arange(-1.5, 2.01, 0.25):
            for t0 in (3.0, 8.0, 15.0, 25.0, 37.0):
                f = _old_obj(g, t0)
                if f < bestf:
                    bestf, best = f, np.array([g, t0])
        p, _ = _pattern_search(lambda q: _old_obj(q[0], q[1]), best,
                               np.array([0.05, 0.5]))
        g, t0 = p
        w = old_model_widths(g, t0, k0=k0)
        return {"g_ms": float(g), "t0_h": float(t0), "k0": k0,
                "rms_km": rms(w - EVENT["width_km"])}
    # ('k','t0') with G=0
    def objk(p):
        return _old_obj(0.0, p[1], 10 ** p[0])
    best, bestf = None, np.inf
    for logk in np.arange(2.5, 6.0, 0.5):
        for t0 in (3.0, 8.0, 15.0, 25.0, 37.0):
            f = _old_obj(0.0, t0, 10 ** logk)
            if f < bestf:
                bestf, best = f, np.array([logk, t0])
    p, _ = _pattern_search(objk, best, np.array([0.2, 0.5]))
    k0v, t0 = 10 ** p[0], p[1]
    w = old_model_widths(0.0, t0, k0=k0v)
    return {"g_ms": 0.0, "k0": float(k0v), "t0_h": float(t0),
            "rms_km": rms(w - EVENT["width_km"])}


# ------------------------------------------------------------------ output
def residual_table(fit):
    sp, sq = _shear_at_beta(fit["shear_beta"])
    w = new_model_widths(fit["k_m2_s"], fit["phi_det"], fit["t0_h"],
                         sigma0_km=fit["sigma0_km"], s_par=sp, s_perp=sq)
    old3 = fit_old_model(("k", "g", "t0"))
    wo = old_model_widths(old3["g_ms"], old3["t0_h"], k0=old3["k0"])
    lines = []
    lines.append(f"  event: Darwin {EVENT['advisory']}  {EVENT['layer']}  "
                 f"OBS {EVENT['dtg']}")
    lines.append(f"  shear (emission sublayer, damped): s_par={S_PAR:.3f}  "
                 f"s_perp={S_PERP:.3f} m/s")
    lines.append("")
    lines.append("  t+   VAAC    new-v2   resid     old(3 par)   resid")
    for i, t in enumerate(EVENT["t_offsets_h"]):
        lines.append(f"  +{int(t):2d}h {EVENT['width_km'][i]:6.1f}  {w[i]:7.1f} "
                     f"{w[i]-EVENT['width_km'][i]:+7.1f}    {wo[i]:8.1f}      "
                     f"{wo[i]-EVENT['width_km'][i]:+7.1f}")
    lines.append("")
    lines.append(f"  RMS  new-v2 (5 par: K, PHI_det, t0, sigma0, beta) : {fit['rms_km']:.1f} km")
    lines.append(f"  RMS  old (3 par free: K0, G, t0)           : {old3['rms_km']:.1f} km"
                 f"   [K0*={old3['k0']:.0f}, G*={old3['g_ms']:+.2f}, t0={old3['t0_h']:.1f}]")
    oldg = fit_old_model(("g", "t0"))
    lines.append(f"  RMS  old (G,t0 free, K0=5000)              : {oldg['rms_km']:.1f} km"
                 f"   [G*={oldg['g_ms']:+.2f} m/s, t0={oldg['t0_h']:.1f} h]")
    oldk = fit_old_model(("k", "t0"))
    lines.append(f"  RMS  old (K0,t0 free, G=0)                 : {oldk['rms_km']:.1f} km"
                 f"   [K0*={oldk['k0']:.0f} m2/s, t0={oldk['t0_h']:.1f} h]")
    oldpos = fit_old_model(("k", "g", "t0", "gpos"))
    lines.append(f"  RMS  old (3 par, G>=0 enforced)            : {oldpos['rms_km']:.1f} km"
                 f"   [K0*={oldpos['k0']:.0f}, G*={oldpos['g_ms']:+.2f}, t0={oldpos['t0_h']:.1f}]")
    lines.append("")
    lines.append("  NOTE the unconstrained old fit drives G NEGATIVE — the same")
    lines.append("  unphysical signal that motivated this rewrite. With G>=0 the")
    lines.append("  old form is MONOTONE in t and cannot reproduce the")
    lines.append("  94 -> 104 -> 87 -> 86 rise-then-fall; its RMS is a structural")
    lines.append("  floor, not a tuning failure.")
    return "\n".join(lines), oldg, oldk, old3


def plot(path, fit):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.font_manager as fm
    for fp in ("/usr/share/fonts/truetype/chinese/NotoSansSC-Regular.ttf",
               "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if os.path.isfile(fp):
            fm.fontManager.addfont(fp)
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Noto Sans SC", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    t = np.linspace(0.2, 60, 260)
    phi = _phi_at(t)
    sp, sq = _shear_at_beta(fit["shear_beta"])
    env = at.envelope_width_series(t, phi, sp, sq,
                                   k_m2s=fit["k_m2_s"], phi_det=fit["phi_det"],
                                   sigma0_km=fit["sigma0_km"])
    old3 = fit_old_model(("k", "g", "t0"))
    t0 = fit["t0_h"]

    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(9, 8), sharex=True,
                                  constrained_layout=True)
    ax.plot(t, 2 * env["width_km"], lw=2.2, color="#1f77b4",
            label=f"new mass-coupled v2  (K={fit['k_m2_s']:.0f}, "
                  f"Φ_det={fit['phi_det']:.2g}, σ₀={fit['sigma0_km']:.0f} km, "
                  f"β={fit['shear_beta']:.2f}, t₀={t0:.0f} h)")
    ax.plot(t, 2 * old_model_widths(old3["g_ms"], 0.0, k0=old3["k0"], t_off=t),
            lw=1.6, ls="--", color="#d62728",
            label=f"old sqrt(2Kt)+Gt  best-3-par (K₀={old3['k0']:.0f}, G={old3['g_ms']:+.2f})")
    ax.plot(t0 + EVENT["t_offsets_h"], EVENT["width_km"], "ko", ms=9,
            label="Darwin VAAC 2026/209 (SFC/FL050)")
    for xx, yy in zip(t0 + EVENT["t_offsets_h"], EVENT["width_km"]):
        ax.annotate(f"{yy:.0f}", (xx, yy), textcoords="offset points",
                    xytext=(0, 10), ha="center", fontsize=9)
    ax.set_ylabel("cross-track width (km)")
    ax.set_title("Krakatau ash envelope — old kinematic vs new mass-coupled, "
                 "vs Darwin VAAC advisory 2026/209")
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(alpha=0.3)

    ax2.plot(t, phi, lw=2.0, color="#2ca02c", label="Φ(t) airborne mass fraction")
    ax2.plot(t, env["phi_det_eff"], lw=1.6, ls=":", color="#ff7f0e",
             label="Φ_det_eff(t) diluted detection threshold")
    ax2.plot(t, 2 * env["sigma_y_km"], lw=1.4, ls="--", color="#1f77b4",
             label="2σ_y cross-track spread")
    ax2.plot(t, 2 * env["sigma_x_km"], lw=1.4, ls="-.", color="#9467bd",
             label="2σ_x along-track spread (dilution)")
    ax2.axvline(t0, color="k", lw=0.8, ls=":", alpha=0.7)
    ax2.annotate("OBS 10/1040Z (t₀)", (t0, 0.9), fontsize=9, rotation=90,
                 textcoords="offset points", xytext=(4, 0), va="top")
    ax2.set_xlabel("cloud age since emission (h)")
    ax2.set_ylabel("fraction / km")
    ax2.set_ylim(0, None)
    ax2.legend(loc="center right", fontsize=9)
    ax2.grid(alpha=0.3)
    fig.savefig(path, dpi=150)
    print(f"[plot written to {path}]")


def sensitivity(fit):
    print("\nSENSITIVITY (refit with one knob moved, others free):")
    base = fit["rms_km"]
    rows = []
    spf, sqf = _shear_at_beta(fit["shear_beta"])
    for s0 in (fit["sigma0_km"] * 0.5, fit["sigma0_km"], fit["sigma0_km"] * 1.5):
        def obj(p):
            r = new_model_widths(10 ** p[0], 10 ** p[1], p[2], sigma0_km=s0,
                                 s_par=spf, s_perp=sqf) - EVENT["width_km"]
            if not np.all(np.isfinite(r)) or np.all(r <= -EVENT["width_km"]):
                return 1e9
            return float(np.sum(r ** 2))
        p, _ = _global_min(obj, [(2.5, 5.5), (-4.0, -0.5), (1.0, 40.0)])
        rows.append((f"sigma0={s0:.1f} km (refit)", rms(new_model_widths(
            10 ** p[0], 10 ** p[1], p[2], sigma0_km=s0, s_par=spf, s_perp=sqf)
            - EVENT["width_km"])))
    for beta in (0.25, 0.5, 0.75):
        sp, sq = _shear_at_beta(beta)
        def obj(p):
            r = new_model_widths(10 ** p[0], 10 ** p[1], p[2],
                                 s_par=sp, s_perp=sq) - EVENT["width_km"]
            if not np.all(np.isfinite(r)) or np.all(r <= -EVENT["width_km"]):
                return 1e9
            return float(np.sum(r ** 2))
        p, _ = _global_min(obj, [(2.5, 5.5), (-4.0, -0.5), (1.0, 40.0)])
        rows.append((f"shear damping={beta:.2f}", rms(new_model_widths(
            10 ** p[0], 10 ** p[1], p[2], s_par=sp, s_perp=sq) - EVENT["width_km"])))
    print(f"  base RMS = {base:.1f} km")
    for name, r in rows:
        print(f"  {name:24s} -> RMS {r:6.1f} km")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--plot", default=None, help="write comparison PNG here")
    ap.add_argument("--sensitivity", action="store_true")
    ap.add_argument("--json", default=None, help="write fit result JSON here")
    args = ap.parse_args()

    print("fitting mass-coupled envelope v2 to Darwin 2026/209 ...")
    fit = fit_new_model()
    table, oldg, oldk, old3 = residual_table(fit)
    print(table)
    print("\nFITTED CONSTANTS (paste into ash_transport.py):")
    print(f"  K_DIFFUSIVITY = {fit['k_m2_s']:.3g}")
    print(f"  PHI_DET       = {fit['phi_det']:.3g}")
    print(f"  SIGMA0_KM     = {fit['sigma0_km']:.1f}   # effective initial spread:")
    print("                                # absorbs the pulsed/continuous emission")
    print("                                # history of the observed cloud")
    print(f"  SHEAR_DAMPING = {fit['shear_beta']:.2f}  # effective-to-instantaneous shear")
    print(f"  (t0_h         = {fit['t0_h']:.1f}  # event age at OBS, NOT a constant)")

    if args.json:
        json.dump({"event": {k: (v.tolist() if isinstance(v, np.ndarray) else v)
                             for k, v in EVENT.items()},
                   "shear": {"s_par_ms": S_PAR, "s_perp_ms": S_PERP},
                   "fit_new": fit, "fit_old_g_t0": oldg, "fit_old_k_t0": oldk,
                   "fit_old_3par": old3},
                  open(args.json, "w"), indent=1)
        print(f"[json written to {args.json}]")
    if args.plot:
        plot(args.plot, fit)
    if args.sensitivity:
        sensitivity(fit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
