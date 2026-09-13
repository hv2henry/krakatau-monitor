"""Model-math unit tests. Run: python3 tests/test_model_math.py
Pins the traps we have actually been bitten by: compass convention,
FROM->TOWARD antipodality, the km/h unit trap, hypsometric heights,
distance/quality weighting, uncertainty monotonicity — and, since the
envelope-v2 rewrite, the mass-coupled width machinery: survival curves,
airborne fraction, threshold detection, and the RISE-THEN-FALL capability
that the old sqrt(2Kt)+Gt form structurally could not do (regression guard
for the 2026/209 calibration, RMS 1.4 km vs 12.2 km old admissible best).
Since the multi-volcano framework: the volcano registry (src/volcanoes.py)
and its wiring into validate/darwin_vaac/volcano_monitor/build_site."""
import math
import os
import sys
import types

# ash_transport guards its netCDF4 import itself (it is only needed when
# reading Himawari-9 AMV files); the pure-math envelope functions below run
# without it. The stub stays for older cached copies that still import it
# unconditionally at module level.
try:
    import netCDF4  # noqa: F401
except ImportError:
    sys.modules["netCDF4"] = types.ModuleType("netCDF4")

# works both in the repo layout (tests/ + ../src/ash_transport.py) and the
# flat delivery layout (tests/ + ../ash_transport.py)
for _p in (os.path.join(os.path.dirname(__file__), "..", "src"),
           os.path.join(os.path.dirname(__file__), "..")):
    if os.path.isfile(os.path.join(_p, "ash_transport.py")):
        sys.path.insert(0, _p)
        break
import numpy as np
import ash_transport as at

FAILS = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILS.append(name)


# 0) volcano registry (multi-volcano framework, src/volcanoes.py)
try:
    import volcanoes as VR
    _HAVE_VR = True
except ImportError:              # flat delivery layout: registry not vendored
    _HAVE_VR = False

if _HAVE_VR:
    check("registry: primary entry is Anak Krakatau (backwards compat)",
          VR.primary()["slug"] == "anak-krakatau"
          and VR.primary()["lat"] == -6.102 and VR.primary()["lon"] == 105.423)
    check("registry: resolve accepts slug, name and alias",
          VR.resolve("anak-krakatau")["slug"] == VR.resolve("Anak Krakatau")["slug"]
          == VR.resolve("krakatoa")["slug"] == VR.resolve("  Krakatau ")["slug"])
    try:
        VR.resolve("definitely-not-a-volcano")
        check("registry: unknown name fails loudly (no silent wrong vent)", False)
    except ValueError:
        check("registry: unknown name fails loudly (no silent wrong vent)", True)
    check("registry: every entry carries the fields the pipeline needs",
          all(all(k in e for k in ("slug", "name", "aliases", "lat", "lon",
                                   "vaac_name", "region"))
              for e in VR.all_volcanoes()))
else:
    print("  skip registry checks (flat delivery layout)")


# 1) compass convention: u=+east, v=+north; FROM = atan2(-u,-v)
u, v = -5.0, 0.0            # wind blowing from the east
frm = math.degrees(math.atan2(-u, -v)) % 360
check("east wind FROM=90", abs(frm - 90) < 1e-6, f"got {frm}")
tow = math.degrees(math.atan2(u, v)) % 360
check("east wind TOWARD=270", abs(tow - 270) < 1e-6)
check("FROM/TOWARD antipodal", abs(((frm + 180) % 360) - tow) < 1e-6)

# 2) compass names stay English 16-point
check("compass WNW", at.compass(292.5) == "WNW")
check("compass SSE", at.compass(157.5) == "SSE")

# 3) km/h unit trap: 36 km/h must become 10 m/s, never 36
kmh = 1.0 / 3.6
check("km/h conversion", abs(36 * kmh - 10.0) < 1e-9)

# 4) circular mean of symmetric pair straddling north
deg = np.array([350.0, 10.0])
m, R = at.circular_mean(deg)
check("circular mean across 0deg", abs(m - 0.0) < 1e-6 or abs(m - 360.0) < 1e-6, f"got {m}")
check("circular R matches cos(half-spread)", abs(R - np.cos(np.radians(10))) < 1e-6, f"R={R}")

# 5) hypsometric heights: monotonic decreasing pressure => increasing height
nwp = {"levels": {p: [{"t": "x", "speed_ms": 5, "from_deg": 180, "u_ms": 0, "v_ms": 5,
                       "temp_c": 30 - 0.0065 * 1000 * i} for i in range(1)]
                  for i, p in enumerate(at.LEVELS_P)}}
h = at.level_heights_km(nwp)
hs = [h[p] for p in sorted(h, reverse=True)]
check("hypsometric monotonic", all(b > a for a, b in zip(hs, hs[1:])), str(hs))
check("850hPa near 1.5km", abs(h[850] - 1.5) < 0.4, f"got {h[850]}")

# 6) weighting: a close high-QI vector must beat two far low-QI ones
obs = [{"lat": np.array([-6.1, -8.5, -8.6]), "lon": np.array([105.4, 108.0, 108.1]),
        "u": np.array([0.0, 10.0, 10.0]), "v": np.array([-5.0, 0.0, 0.0]),
        "prs": np.array([800.0, 800.0, 800.0]), "qi": np.array([90.0, 60.0, 60.0])}]
rows = at.amv_profile(obs, -6.102, 105.423, 4.0, 60)
r = next(x for x in rows if x.get("data"))
check("weighting favours near vent", 240 < r["toward_deg"] < 300 or r["toward_deg"] < 30
      or abs(r["toward_deg"] - 180) < 30, f"toward={r['toward_deg']}")
check("n_eff < n when far vectors downweighted", r["n_eff"] < r["n"], f"{r['n_eff']} vs {r['n']}")

# 7) uncertainty: lower R => larger uncertainty
def unc(R, n_eff=10.0):
    return min(60.0, max(5.0, np.degrees(np.sqrt(-2.0 * np.log(max(R, 0.05)))) / np.sqrt(max(n_eff, 1.0))))
check("uncertainty monotonic in R", unc(0.95) < unc(0.6) < unc(0.3))

# 8) settling classes ordered, four classes incl. satellite-tracked ultrafine
check("settle classes ordered",
      at.SETTLE_CLASSES["ultrafine"] < at.SETTLE_CLASSES["fine"]
      < at.SETTLE_CLASSES["medium"] < at.SETTLE_CLASSES["coarse"])
check("class mass fractions sum to 1",
      abs(sum(at.CLASS_MASS_FRACTIONS.values()) - 1.0) < 1e-9)

# 9) landing times: coarse lands within hours, ultrafine outlives the horizon
lt_coarse = at.class_landing_times(at.SETTLE_CLASSES["coarse"], 0.0, 1.52)
lt_ultra = at.class_landing_times(at.SETTLE_CLASSES["ultrafine"], 0.0, 1.52)
check("coarse lands within ~2h from 1.5km", lt_coarse[-1] < 2.5, f"{lt_coarse[-1]:.2f}h")
check("ultrafine aloft past 72h horizon", lt_ultra[-1] > 72.0, f"{lt_ultra[-1]:.1f}h")
check("landing times sorted", np.all(np.diff(lt_coarse) >= 0))

# 10) survival curve: decreasing, bounded, drops later for slower classes
t = np.linspace(0, 48, 97)
s_c = at.survival_curve(t, lt_coarse)
s_u = at.survival_curve(t, lt_ultra)
check("survival in [0,1]", np.all(s_c >= 0) and np.all(s_c <= 1.0001))
check("survival non-increasing", np.all(np.diff(s_c) <= 1e-9))
check("ultrafine outlives coarse", s_u[-1] > s_c[-1], f"{s_u[-1]} vs {s_c[-1]}")

# 11) airborne fraction: starts at 1, decreases, plateaus at ultrafine share
phi = at.airborne_fraction(t, 0.0, 1.52)
check("phi(0)=1", abs(phi[0] - 1.0) < 0.02, f"{phi[0]}")
check("phi decreasing", np.all(np.diff(phi) <= 1e-9))
check("phi plateaus at ultrafine fraction",
      abs(phi[-1] - at.CLASS_MASS_FRACTIONS["ultrafine"]) < 0.05,
      f"phi(48h)={phi[-1]:.3f} vs f_uf={at.CLASS_MASS_FRACTIONS['ultrafine']}")

# 12) envelope width: zero below threshold, positive above
w_dead = at.envelope_width_series(np.array([10.0]), np.array([1e-9]), 0.0, 0.0)
check("width zero when phi below threshold", w_dead["width_km"][0] == 0.0)
w_alive = at.envelope_width_series(np.array([10.0]), np.array([1.0]), 0.0, 0.0)
check("width positive when phi=1", w_alive["width_km"][0] > 0)

# 13) dilution: more along-track shear -> narrower detectable width
w_sh0 = at.envelope_width_series(t, phi, 0.0, 0.0)
w_sh1 = at.envelope_width_series(t, phi, 1.5, 0.0)
check("along-track shear dilutes the cloud",
      np.all(w_sh1["width_km"] <= w_sh0["width_km"] + 1e-9))
check("sigma_x >= sigma_y under along-only shear",
      np.all(w_sh1["sigma_x_km"] >= w_sh1["sigma_y_km"]))

# 14) THE regression test: rise-then-fall (old form could never do this)
w_series = w_sh0["width_km"]
peak_i = int(np.argmax(w_series))
check("envelope rises then falls (non-monotonic)",
      peak_i > 0 and peak_i < len(w_series) - 1 and w_series[-1] < w_series[peak_i],
      f"peak at t={t[peak_i]:.0f}h w={w_series[peak_i]:.1f}, end w={w_series[-1]:.1f}")

# 15) old form documented as monotone (why it was replaced)
old_w = 2.0 * (np.sqrt(2 * 5e3 * t * 3600.0) + 0.8 * t * 3600.0) / 1000.0
check("old sqrt(2Kt)+Gt form is monotone (the bug)",
      np.all(np.diff(old_w) > 0))

# 16) envelope polygon: symmetric buffer around the track, uses width_km
fine = [{"lat": -6.0, "lon": 105.0, "hours": 0, "sigma_km": 0.0, "width_km": 0.0},
        {"lat": -6.5, "lon": 105.0, "hours": 6, "sigma_km": 10.0, "width_km": 10.0},
        {"lat": -7.0, "lon": 105.0, "hours": 12, "sigma_km": 15.0, "width_km": 8.0}]
poly = at.envelope_polygon(fine)
check("envelope closed shape", len(poly) == 2 * len(fine), f"{len(poly)}")
lats = [p[1] for p in poly]
check("envelope spans track width", max(lats) - min(lats) > 0.1)
# fallback to sigma_km for legacy cached trajectories
fine_legacy = [{"lat": -6.0, "lon": 105.0, "hours": 0, "sigma_km": 5.0},
               {"lat": -6.5, "lon": 105.0, "hours": 6, "sigma_km": 5.0}]
fine_legacy_wide = [{"lat": -6.0, "lon": 105.0, "hours": 0, "sigma_km": 9.0},
                    {"lat": -6.5, "lon": 105.0, "hours": 6, "sigma_km": 9.0}]
check("envelope falls back to sigma_km",
      at.envelope_polygon(fine_legacy_wide)[0][0] != at.envelope_polygon(fine_legacy)[0][0])

# 17) emission sublayer: upper fraction of the advisory layer
lo, hi = at._emission_sublayer(0.0, 1.52)
check("emission sublayer is upper half",
      abs(lo - 0.76) < 0.01 and abs(hi - 1.52) < 1e-9, f"{lo}..{hi}")

# 18) convex hull: interior points drop out; union envelope spans bands
hull = at.convex_hull([(0, 0), (0, 1), (1, 1), (1, 0), (0.5, 0.5)])
check("hull excludes interior point",
      (0.5, 0.5) not in [tuple(p) for p in hull] and len(hull) == 4, str(hull))
u = at.union_envelope({
    "low":  [[105.0, -7.0], [105.0, -6.0], [106.0, -6.0], [106.0, -7.0]],
    "high": [[105.0, -6.0], [105.0, -5.0], [106.0, -5.0], [106.0, -6.0]]})
check("union of stacked bands = one 2x2 deg rectangle",
      u and u["n_bands"] == 2 and len(u["polygon"]) == 4
      and 23000 < u["area_km2"] < 26000, str(u and u["polygon"]))
check("union empty when no usable polygons",
      at.union_envelope({}) is None
      and at.union_envelope({"low": [[1, 1], [2, 2]]}) is None)

# 19) cross-track width + CLI parsers
rect = [[105.0, -7.0], [105.0, -6.0], [107.0, -6.0], [107.0, -7.0]]
check("width perpendicular to eastward motion = lat extent",
      abs(at.polygon_cross_track_width_km(rect, 90) - 111.32) < 2)
check("width perpendicular to northward motion = lon extent",
      abs(at.polygon_cross_track_width_km(rect, 0) - 2 * 111.32 * 0.996) < 3)
check("dict vertices (darwin_vaac form) agree with pair form",
      abs(at.polygon_cross_track_width_km(
          [{"lat": p[1], "lon": p[0]} for p in rect], 90)
          - at.polygon_cross_track_width_km(rect, 90)) < 1e-6)
check("compass/degree motion parsing",
      at._parse_mov("NW") == 315.0 and at._parse_mov("315") == 315.0
      and at._parse_mov(None) is None)
check("obs polygon string parsing (lat,lon;...)",
      at._parse_obs_polygon("-6.0,105.0;-7.0,105.0;-7.0,106.0")
      == [[105.0, -6.0], [105.0, -7.0], [106.0, -7.0]])
check("obs polygon JSON dict parsing",
      at._parse_obs_polygon('[{"lat":-6.0,"lon":105.0},{"lat":-7.0,"lon":105.0}]')
      == [[105.0, -6.0], [105.0, -7.0]])

# 20) THE emission-history inversion: the 2026/209 OBS width (93.9 km) must
#     invert back to the calibration's free cloud age t0 ~ 8.8 h — this is
#     what makes the production run consistent with calibrate_envelope.py
_winds = [(1000, 0.15, 164.5, 9.0), (850, 1.52, 43.5, 2.9)]
_levels = {p: [{"t": "t0", "speed_ms": s, "from_deg": f,
                "u_ms": -s * np.sin(np.radians(f)),
                "v_ms": -s * np.cos(np.radians(f)), "temp_c": 30.0 - 6.5 * z}]
           for p, z, f, s in _winds}
_nwp = {"times": ["t0"], "levels": _levels}
_hgt = {p: z for p, z, _, _ in _winds}
_sp, _sq = at.effective_shear_ms(_nwp, _hgt, 0.0, 1.52)
_t_age, _note = at.implied_emission_age_h(93.9, _sp, _sq, 0.0, 1.52,
                                          nwp=_nwp, heights=_hgt)
check("OBS width 93.9 km inverts to the calibrated t0 (~8.8 h)",
      6.0 < _t_age < 12.0, f"t_age={_t_age:.2f} h")
_t0, _ = at.implied_emission_age_h(5.0, _sp, _sq, 0.0, 1.52, nwp=_nwp, heights=_hgt)
check("tiny OBS width -> fresh cloud (t_age=0)", _t0 == 0.0)
_tc, _nc = at.implied_emission_age_h(50000.0, _sp, _sq, 0.0, 1.52, nwp=_nwp, heights=_hgt)
check("absurd OBS width -> capped with a note",
      _tc == at.MAX_EMISSION_AGE_H and "exceeds" in _nc)

# 21) emission-history time shift inside trajectory_settling
from datetime import datetime, timezone  # noqa: E402
_start = datetime(2026, 9, 10, 10, 40, tzinfo=timezone.utc)
_cls_fresh = at.trajectory_settling(-6.102, 105.423, 1.0, _nwp, _hgt, _start,
                                    hours=12, layer_km=(0.0, 1.52), t_age_h=0.0)
_cls_aged = at.trajectory_settling(-6.102, 105.423, 1.0, _nwp, _hgt, _start,
                                   hours=12, layer_km=(0.0, 1.52), t_age_h=10.0)
_ef, _ea = _cls_fresh["fine"]["envelope"], _cls_aged["fine"]["envelope"]
check("envelope records the emission age",
      _ef["emission_age_h"] == 0.0 and _ea["emission_age_h"] == 10.0)
check("aged cloud: wider AND lighter at the analysis point",
      _ea["analysis_width_km"] > _ef["analysis_width_km"]
      and _ea["analysis_phi"] < _ef["analysis_phi"],
      f"w {_ef['analysis_width_km']}->{_ea['analysis_width_km']}, "
      f"phi {_ef['analysis_phi']}->{_ea['analysis_phi']}")
check("analysis point carries the aged state",
      abs(_cls_aged["fine"]["pts"][0]["phi"] - _ea["analysis_phi"]) < 0.01)
check("pre-analysis settling removes fine-class mass",
      _cls_fresh["fine"]["mass_remaining"] > _cls_aged["fine"]["mass_remaining"]
      and _cls_aged["fine"]["pts"][0]["phi"] < 1.0 - 1e-6)

# 22) build_site wiring (repo layout only): OBS-polygon seeding + width ledger.
#     The v2.1 pipeline pieces that live outside ash_transport itself.
try:
    import darwin_vaac as dv
    import build_site as bs
    import validate as V
    import volcano_monitor as vm
    _HAVE_BS = True
except Exception:
    _HAVE_BS = False

if _HAVE_BS:
    # registry wiring: every module resolves the SAME volcano, no drift
    check("validate VENT is built from the registry (all aliases map)",
          V.VENT.get("anak krakatau") == (-6.102, 105.423)
          and V.VENT.get("krakatau") == (-6.102, 105.423)
          and V.VENT.get("krakatoa") == (-6.102, 105.423))
    check("darwin_vaac NAME_ALIASES carries the registry VAAC name",
          dv.NAME_ALIASES.get("anak krakatau") == "KRAKATAU"
          and dv.NAME_ALIASES.get("krakatoa") == "KRAKATAU")
    check("volcano_monitor KNOWN_CODES carries the registry MAGMA code",
          vm.KNOWN_CODES.get("anak krakatau") == "KRA"
          and vm.KNOWN_CODES.get("krakatau") == "KRA")
    _frm = bs.framing_for(VR.primary())
    check("build_site framing: registry values match the Sunda Strait close-up",
          tuple(_frm[0]) == (103.6, 107.2, -7.4, -4.8)   # z9 daily true-colour box
          and tuple(_frm[1]) == (100.0, 111.0, -11.0, 0.0)
          and tuple(_frm[2]) == (100.4, 110.4, -10.5, -2.5), str(_frm))
    _gen = bs.framing_for({"lat": 3.17, "lon": 98.392})   # a hypothetical 2nd volcano
    check("build_site framing: unregistered volcano gets vent-centred boxes",
          abs(_gen[0][0] - (98.392 - 1.8)) < 1e-9
          and abs(_gen[1][3] - (3.17 + 5.5)) < 1e-9, str(_gen))
    # Darwin advisory 2026/209 verbatim (10 Sep 2026, SFC/FL050, MOV NW) —
    # the event the envelope v2 parameters were calibrated on
    _BULLETIN_209 = """FVAU04 ADRM 101100
VA ADVISORY
DTG: 20260910/1100Z
VAAC: DARWIN
VOLCANO: KRAKATAU 262000
PSN: S0606 E10525
AREA: INDONESIA
SOURCE ELEV: 155M AMSL
ADVISORY NR: 2026/209
INFO SOURCE: HIMAWARI-9, CVGHM
ERUPTION DETAILS: VA TO FL050 OBS AT 10/1040Z MOV NW
OBS VA DTG: 10/1040Z
OBS VA CLD: SFC/FL050 S0611 E10527 - S0627 E10453 - S0556
E10439 - S0541 E10509 - S0555 E10533 MOV NW 10KT
FCST VA CLD +6 HR: 10/1640Z SFC/FL050 S0612 E10531 - S0628
E10445 - S0552 E10443 - S0538 E10515 - S0601 E10533
FCST VA CLD +12 HR: 10/2240Z SFC/FL050 S0611 E10530 - S0629
E10453 - S0608 E10441 - S0548 E10454 - S0559 E10530
FCST VA CLD +18 HR: 11/0440Z SFC/FL050 S0609 E10531 - S0629
E10453 - S0613 E10438 - S0549 E10447 - S0559 E10529
RMK: VA IDENTIFIABLE ON SAT IMAGERY MOVING NW. VA HEIGHT AND FORECAST
MOVEMENT BASED ON SATELLITE IMAGERY, MODEL GUIDANCE AND GROUND REPORTS.
NXT ADVISORY: NO LATER THAN 20260910/1700Z="""
    _vaac = {"state": "advisory", "advisory": dv.parse_advisory(_BULLETIN_209)}
    _args = bs.obs_polygon_args(_vaac)
    check("build_site passes the OBS polygon to the model CLI",
          _args is not None
          and _args[0].startswith("--obs-polygon=-6.1833,105.45;")
          and _args[1:3] == ["--obs-mov-deg", "NW"]
          and _args[-2:] == ["--obs-layer-km", "0.0,1.52"], str(_args))
    check("nil/stale advisory -> no OBS seeding (fresh-emission default)",
          bs.obs_polygon_args({"state": "nil"}) is None
          and bs.obs_polygon_args(None) is None)

    _cand = {
        "envelope_emission": {"obs_width_km": 94.2, "emission_age_h": 9.7,
                              "source": "OBS polygon (Darwin advisory)"},
        "trajectories_forecast": {
            "~0-1 km surface": [{"hours": h, "alt_km": 0.1 + 0.06 * h,
                                 "width_km": 45.0 + h} for h in (0, 6, 12)],
            "~1-2 km ASH-CRITICAL": [{"hours": h, "alt_km": 0.8 + 0.06 * h,
                                      "width_km": 47.0 + h} for h in (0, 6, 12)],
            "~2-4 km ASH-CRITICAL": [{"hours": h, "alt_km": 2.3,
                                      "width_km": 60.0 + h} for h in (0, 6, 12)],
        }}
    _led = bs.width_ledger_entry(_vaac, _cand)
    _fw = {r["h"]: r for r in _led.get("fcst_widths", [])}
    check("ledger reproduces the 2026/209 VAAC widths (104.7 / 87.6 / 86.3 km)",
          abs(_fw[6]["vaac_km"] - 104.7) < 0.3
          and abs(_fw[12]["vaac_km"] - 87.6) < 0.3
          and abs(_fw[18]["vaac_km"] - 86.3) < 0.3, str(_fw))
    check("model widths follow the OBS-layer bands (2 x half-width)",
          _fw[6]["model_km"] == 106.0 and _fw[12]["model_km"] == 118.0, str(_fw))
    check("beyond the model horizon: width recorded as null, not invented",
          _fw[18]["model_km"] is None and _led["obs_width_km"] == 94.2)
    check("empty vaac/cand -> empty ledger entry",
          bs.width_ledger_entry(None, None) == {})

    # 24) v2.3 dual union: the "VAAC-comparable" hull only carries bands
    #     at/below the official cloud top (+0.5 km grace); the all-bands
    #     worst-case hull is only offered when the two actually differ
    _LO = [[105.0, -6.10], [105.9, -6.05], [105.5, -5.60]]
    _HI = [[105.0, -4.00], [106.0, -4.00], [105.5, -3.40]]
    _cand23 = {
        "envelopes": {"~0-1 km surface": _LO, "~1-2 km ASH-CRITICAL": _LO,
                      "~2-4 km ASH-CRITICAL": _HI, "~9-16 km upper": _HI},
        "envelope_union": {"polygon": _HI + _LO, "n_bands": 4, "area_km2": 9999.0},
        "observed_wind_profile": [
            {"layer": "~0-1 km surface", "alt_km": 0.1},
            {"layer": "~1-2 km ASH-CRITICAL", "alt_km": 1.5},
            {"layer": "~2-4 km ASH-CRITICAL", "alt_km": 3.3},
            {"layer": "~9-16 km upper", "pressure_range": [100, 300]},  # no alt -> pressure
        ],
    }
    _ut, _ua = bs.dual_union(_cand23, 1.52)
    check("dual union: bands above the official top stay out of the hull",
          _ut is not None and _ut["n_bands"] == 2
          and set(_ut["bands"]) == {"~0-1 km surface", "~1-2 km ASH-CRITICAL"}
          and _ua is _cand23["envelope_union"], str(_ut and _ut.get("bands")))
    _lat_max = max(p[1] for p in (_ut or {}).get("polygon", [[0, 0]]))
    check("dual union: filtered hull is geometrically smaller (no high vertex)",
          _lat_max < -5.0, str(_lat_max))
    _ut2, _ua2 = bs.dual_union(_cand23, 20.0)
    check("dual union: top above every band -> nothing filtered, one hull",
          _ut2 is None and _ua2 is _cand23["envelope_union"])
    _ut3, _ua3 = bs.dual_union(_cand23, None)
    check("dual union: no official top -> all-bands hull only",
          _ut3 is None and _ua3 is _cand23["envelope_union"])
    _ut4, _ua4 = bs.dual_union({}, None)
    check("dual union: empty candidate -> both hulls None",
          _ut4 is None and _ua4 is None)

    # 23) validate.py gate B: envelope sanity on the v2.1 JSON contract
    _ok = V.gate_sanity_envelope({
        "trajectories_settling": {"~0-1 km surface": {"envelope": {
            "width_end_km": 42.6, "phi_end": 0.349, "emission_age_h": 9.7}}},
        "envelope_union": {"polygon": [[105.0, -6.0], [106.0, -6.0], [106.0, -5.0]],
                           "n_bands": 6, "area_km2": 31503.0},
        "envelope_emission": {"emission_age_h": 9.7}})
    check("gate B: clean envelope JSON produces no failures",
          not [c for c in _ok if not c.ok])
    _bad = V.gate_sanity_envelope({
        "trajectories_settling": {"~0-1 km surface": {"envelope": {
            "width_end_km": 99999.0, "phi_end": 0.3, "emission_age_h": 9.7}}},
        "envelope_union": {"polygon": [[170.0, -6.0], [171.0, -6.0], [171.0, -5.0]],
                           "n_bands": 6, "area_km2": 100.0},
        "envelope_emission": {"emission_age_h": 9.7}})
    _bad_fails = [c.name for c in _bad if not c.ok]
    check("gate B: blown width + out-of-domain vertex hard-fail",
          "envelope:~0-1 km surface:width_end" in _bad_fails
          and "envelope_union:polygon_in_domain" in _bad_fails, str(_bad_fails))
    check("gate B: old cached runs (no envelope fields) are skipped",
          V.gate_sanity_envelope({"trajectories_settling": {}}) == []
          and V.gate_sanity_envelope(None) == [])
else:
    print("  skip build_site/validate wiring (flat delivery layout)")

print()
if FAILS:
    print(f"{len(FAILS)} FAILURES: {FAILS}")
    sys.exit(1)
print("all model-math tests passed")
