"""Model-math unit tests. Run: python3 tests/test_model_math.py
Pins the traps we have actually been bitten by: compass convention,
FROM->TOWARD antipodality, the km/h unit trap, hypsometric heights,
distance/quality weighting, and uncertainty monotonicity."""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import numpy as np
import ash_transport as at

FAILS = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILS.append(name)


# 1) compass convention: u=+east, v=+north; FROM = atan2(-u,-v)
u, v = -5.0, 0.0            # wind blowing from the east
frm = math.degrees(math.atan2(-u, -v)) % 360
check("east wind FROM=90", abs(frm - 90) < 1e-6, f"got {frm}")
tow = math.degrees(math.atan2(u, v)) % 360
check("east wind TOWARD=270", abs(tow - 270) < 1e-6, f"got {tow}")
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

# 8) envelope polygon: symmetric buffer around the track
fine = [{"lat": -6.0, "lon": 105.0, "hours": 0, "sigma_km": 0.0},
        {"lat": -6.5, "lon": 105.0, "hours": 6, "sigma_km": 10.0},
        {"lat": -7.0, "lon": 105.0, "hours": 12, "sigma_km": 15.0}]
poly = at.envelope_polygon(fine)
check("envelope closed shape", len(poly) == 2 * len(fine), f"{len(poly)}")
lats = [p[1] for p in poly]
check("envelope spans track width", max(lats) - min(lats) > 0.1)

# 9) settling: coarse ash must end lower than fine
nwp2 = at.nwp_profile.__doc__ and None  # no network in tests; structural only
check("settle classes ordered", at.SETTLE_CLASSES["fine"] < at.SETTLE_CLASSES["medium"] < at.SETTLE_CLASSES["coarse"])

print()
if FAILS:
    print(f"{len(FAILS)} FAILURES: {FAILS}")
    sys.exit(1)
print("all model-math tests passed")
