"""Quiet-state unit tests. Run: python3 tests/test_quiet_state.py

Pins the traps we were actually bitten by on 2026-09-12, the day Darwin VAAC
ended the Krakatau episode with bulletin 2026/217 ("ADVISORY TERMINATED",
"NXT ADVISORY: NO FURTHER ADVISORIES"):

  * a TERMINATION bulletin must parse as terminated — not as business as
    usual — and must reach the fetch() result top-level;
  * "no real-time data from MAGMA and VAAC" must mean NORMAL (quiet), never
    a silent fallback to 19-day-old data as "today";
  * the VONA ash-top anchor must be age-gated (the 24-Aug 557 m estimate was
    being rendered as the official top "today" on 12 Sep);
  * an unreachable source can never vote for quiet (hysteresis holds);
  * the quiet state must stop the model / auto-publish / fresh-stamp archive.

Covers src/activity_state.py and the terminated parsing in src/darwin_vaac.py.
No network: every VAAC input is a fixture payload passed through fetch().
"""
import os
import sys
import types
from datetime import datetime, timedelta, timezone

try:
    import netCDF4  # noqa: F401
except ImportError:
    sys.modules["netCDF4"] = types.ModuleType("netCDF4")

for _p in (os.path.join(os.path.dirname(__file__), "..", "src"),
           os.path.join(os.path.dirname(__file__), "..")):
    if os.path.isfile(os.path.join(_p, "ash_transport.py")):
        sys.path.insert(0, _p)
        break

import darwin_vaac as dv
import activity_state as ACT

FAILS = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILS.append(name)


NOW = datetime(2026, 9, 12, 14, 0, tzinfo=timezone.utc)

# --- fixtures ---------------------------------------------------------------
# Bulletin 2026/217, verbatim shape as served by the Darwin endpoint on
# 2026-09-12 (hard-wrapped, = terminator, NO FURTHER ADVISORIES).
TERM_BULLETIN = """FVAU04 ADRM 121040
VA ADVISORY
DTG: 20260912/1040Z
VAAC: DARWIN
VOLCANO: KRAKATAU 262000
PSN: S0606 E10525
AREA: INDONESIA
SOURCE ELEV: 155M AMSL
ADVISORY NR: 2026/217
INFO SOURCE: HIMAWARI-9, CVGHM
ERUPTION DETAILS: VA TO FL050 LAST OBS AT 11/0820Z MOV SW
EST VA DTG: 12/1020Z
EST VA CLD: VA NOT IDENTIFIABLE FM SATELLITE DATA WIND
SFC/FL050 070/20KT
FCST VA CLD +6 HR: 12/1620Z NO VA EXP
FCST VA CLD +12 HR: 12/2220Z NO VA EXP
FCST VA CLD +18 HR: 13/0420Z NO VA EXP
RMK: VA NOT IDENTIFIABLE ON RECENT SATELLITE  IMAGERY. NO
OTHER REPORTS INDICATE ONGOING ERUPTION. ADVISORY
TERMINATED.
NXT ADVISORY: NO FURTHER ADVISORIES="""

# An ordinary in-episode bulletin (2026/191 shape, abbreviated but complete).
# DTG today + ~2 h old so it counts as CURRENT against the test's NOW.
ACTIVE_BULLETIN = """FVAU01 ADRM 121200
VA ADVISORY
DTG: 20260912/1200Z
VAAC: DARWIN
VOLCANO: KRAKATAU 262000
PSN: S0606 E10525
AREA: INDONESIA
SOURCE ELEV: 155M AMSL
ADVISORY NR: 2026/218
INFO SOURCE: HIMAWARI-9
ERUPTION DETAILS: ERUPTION AT 12/1100Z
OBS VA DTG: 12/1200Z
OBS VA CLD: SFC/FL050 OBS S0630 E10530 S0630 E10600 S0600 E10600 S0600 E10530
MOV W 05KT
FCST VA CLD +6 HR: 12/1800Z SFC/FL050 S0640 E10400 S0640 E10500 S0610 E10500 S0610 E10400
FCST VA CLD +12 HR: 13/0000Z NO VA EXP
FCST VA CLD +18 HR: 13/0600Z NO VA EXP
RMK: VA IDENTIFIABLE ON SAT IMAGERY.
NXT ADVISORY: 20260912/1800Z="""

# The 2026-09-12 VONA list shape, VERBATIM MAGMA timestamps ("... UTC",
# not ISO — a fresh VONA in this format must still flip the state to active):
# newest ORANGE has no ash-top; the newest WITH an ash-top is the 24 Aug
# 557 m one — 19 days stale.
VONA_STALE = [
    {"code": "ORANGE", "issued_utc": "2026-09-08 04:34:00 UTC"},
    {"code": "RED", "issued_utc": "2026-09-05 02:00:00 UTC"},
    {"code": "ORANGE", "issued_utc": "2026-08-24 08:24:00 UTC", "ash_top_m": 557},
    {"code": "ORANGE", "issued_utc": "2026-08-22 13:07:00 UTC", "ash_top_m": 557},
]


def payload(bulletin, name="KRAKATAU"):
    return {"total": 1, "advisories": {"0": {"name": name, "text": bulletin, "graphic": None}}}


# ---------------------------------------------------------------- terminated
print("1) terminated parsing")
p = dv.parse_advisory(TERM_BULLETIN)
check("bulletin 2026/217 parses as terminated", p.get("terminated") is True
      and p.get("episode_status") == "terminated", str(p.get("episode_status")))
check("advisory_nr intact", p.get("advisory_nr") == "2026/217")

pa = dv.parse_advisory(ACTIVE_BULLETIN)
check("ordinary bulletin stays active", pa.get("terminated") is False
      and pa.get("episode_status") == "active", str(pa.get("episode_status")))
check("active bulletin keeps its OBS layers", len(pa.get("observed_layers") or []) >= 1)

r = dv.fetch("Anak Krakatau", payload=payload(TERM_BULLETIN))
check("fetch() mirrors terminated top-level", r.get("state") == "advisory"
      and r.get("terminated") is True and r.get("advisory_status") == "terminated")
check("terminated warning is present", "TERMINATES" in (r.get("warning") or ""))

ra = dv.fetch("Anak Krakatau", payload=payload(ACTIVE_BULLETIN))
check("fetch() active advisory: not terminated", ra.get("terminated") is False)

for txt, want in (
    ("RMK: ADVISORY TERMINATED.", "terminated"),
    ("NXT ADVISORY: NO FURTHER ADVISORIES=", "terminated"),
    ("RMK: THIS ADVISORY IS CANCELLED", "cancelled"),
    ("RMK: VA NOT IDENTIFIABLE. CANCELS PREVIOUS.", "cancelled"),
    ("RMK: VA IDENTIFIABLE ON SAT IMAGERY.", "active"),
    ("NXT ADVISORY: 20260912/1600Z", "active"),
):
    got = dv.episode_status(txt)
    check(f"episode_status({txt[:34]}...) == {want}", got == want, f"got {got}")

# ---------------------------------------------------------------- state machine
print("\n2) activity state machine — the 2026-09-12 scenario")
a = ACT.assess_activity(r, VONA_STALE, [], now=NOW, magma_ok=True)
check("terminated + no fresh VONA => quiet", a["state"] == "quiet")
check("reason: vaac_terminated", a["reason_code"] == "vaac_terminated")
check("evidence carries vona age (MAGMA format parsed)",
      a["evidence"].get("vona_age_hours") is not None
      and a["evidence"]["vona_age_hours"] > 24,
      str(a["evidence"].get("vona_age_hours")))

a2 = ACT.assess_activity({"state": "nil"}, VONA_STALE, [], now=NOW, magma_ok=True)
check("nil VAAC + 19-day VONA => quiet (normal, not fail)",
      a2["state"] == "quiet" and a2["reason_code"] == "no_realtime_data")

a3 = ACT.assess_activity({"state": "stale"}, VONA_STALE, [], now=NOW, magma_ok=True)
check("stale VAAC (other volcanoes) + no fresh => quiet",
      a3["state"] == "quiet" and a3["reason_code"] == "no_realtime_data")

a4 = ACT.assess_activity(ra, VONA_STALE, [], now=NOW, magma_ok=True)
check("current non-terminated advisory => active", a4["state"] == "active")

vona_fresh = [{"code": "ORANGE",
               "issued_utc": "2026-09-12 12:00:00 UTC",
               "ash_top_m": 800}]
a5 = ACT.assess_activity({"state": "nil"}, vona_fresh, [], now=NOW, magma_ok=True)
check("fresh VONA overrides nil VAAC => active",
      a5["state"] == "active" and a5["reason_code"] == "fresh_vona")

erup_fresh = [{"utc": (NOW - timedelta(hours=3)).isoformat(),
               "text": "erupsi"}]
a6 = ACT.assess_activity({"state": "nil"}, VONA_STALE, erup_fresh, now=NOW, magma_ok=True)
check("fresh eruption row => active", a6["state"] == "active"
      and a6["reason_code"] == "fresh_eruption")

print("\n3) hysteresis — absence of data is not absence of ash")
h1 = ACT.assess_activity({"state": "error"}, VONA_STALE, [], now=NOW,
                         previous=a, magma_ok=True)
check("VAAC unreachable holds previous quiet", h1["state"] == "quiet"
      and h1["reason_code"] == "held_vaac_unreachable")
check("since_utc carried while held", h1["since_utc"] == a["since_utc"])

h2 = ACT.assess_activity({"state": "nil"}, VONA_STALE, [], now=NOW,
                         previous={"state": "active", "since_utc": "2026-09-05T02:00Z"},
                         magma_ok=False)
check("MAGMA unreachable holds previous active", h2["state"] == "active"
      and h2["reason_code"] == "held_magma_unreachable")

h3 = ACT.assess_activity(r, VONA_STALE, [], now=NOW,
                         previous={"state": "active", "since_utc": "2026-09-05T02:00Z"},
                         magma_ok=True)
check("transition resets since_utc", h3["since_utc"] != "2026-09-05T02:00Z")
check("no previous + ambiguous => default active (keep watching)",
      ACT.assess_activity({"state": "error"}, VONA_STALE, [], now=NOW,
                          magma_ok=False)["state"] == "active")

print("\n4) plume-top age gate (the 0.6 km 'today' bug)")
check("19-day-old VONA never anchors", ACT.vona_plume_top(VONA_STALE, now=NOW) is None)
got = ACT.vona_plume_top(vona_fresh, now=NOW)
check("2-hour-old VONA anchors", got is not None and got["ash_top_m"] == 800)
mixed = [{"issued_utc": "2026-08-24T08:24:00+00:00", "ash_top_m": 557}] + vona_fresh
gm = ACT.vona_plume_top(mixed, now=NOW)
check("mixed list picks the FRESH entry, not the first with ash_top",
      gm is not None and gm["ash_top_m"] == 800)
check("unparseable timestamps are ignored, not fatal",
      ACT.vona_plume_top([{"issued_utc": "garbage", "ash_top_m": 900}], now=NOW) is None)

print("\n5b) timestamp formats the pipeline actually meets")
from datetime import timedelta as _td
p = ACT._parse_iso
check("MAGMA 'YYYY-MM-DD HH:MM:SS UTC' parses",
      p("2026-09-12 12:00:00 UTC") == datetime(2026, 9, 12, 12, tzinfo=timezone.utc))
check("ISO Z parses", p("2026-09-12T12:00:00Z") == datetime(2026, 9, 12, 12, tzinfo=timezone.utc))
check("ISO +00:00 parses", p("2026-09-12T12:00:00+00:00") == datetime(2026, 9, 12, 12, tzinfo=timezone.utc))
check("ISO with +07:00 offset keeps its offset",
      p("2026-09-12T19:00:00+07:00") == datetime(2026, 9, 12, 12, tzinfo=timezone.utc))
check("naive ISO treated as UTC", p("2026-09-12T12:00:00") == datetime(2026, 9, 12, 12, tzinfo=timezone.utc))
check("garbage is None", p("garbage") is None and p("") is None and p(None) is None)
check("age math on MAGMA format (2 h)",
      abs(ACT._age_h("2026-09-12 12:00:00 UTC", NOW) - 2.0) < 1e-6)
check("mixed-format freshness: MAGMA-format fresh VONA flips state",
      ACT.assess_activity({"state": "nil"}, vona_fresh, [], now=NOW,
                         magma_ok=True)["state"] == "active")

print("\n6) reason strings are site-ready")
for k in ("id", "en"):
    check(f"quiet reason has {k} sentence",
          bool(a["reason"].get(k)) and len(a["reason"][k]) > 20)

# ---------------------------------------------------------------- summary
print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> " + ", ".join(FAILS))
    sys.exit(1)
print("quiet-state: all checks passed")
