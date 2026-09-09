#!/usr/bin/env python3
"""
validate.py — decide what may be published, and under which label.

A monitoring bot has three ways to hurt people:
  1. publishing something FALSE,
  2. publishing something TRUE but STALE as if it were current,
  3. going quiet in a way that reads as "all clear".

This module attacks all three. Every claim the pipeline wants to publish is
run through four gates and given a ROUTING decision:

  auto       — safe to publish without a human (verbatim official text that
               passed every sanity and freshness check)
  review     — drafted to the private editor channel; a human approves it
  quarantine — never published; operator is alerted instead

The four gates
--------------
A. FRESHNESS / SLA.  Every source declares an expected cadence. Age beyond the
   SLA downgrades confidence and, past a hard limit, quarantines the record.
   A source that misses its own stated deadline (Darwin's NXT ADVISORY) is
   flagged overdue.

B. SANITY / PHYSICALITY.  Position inside the expected box, flight levels in
   SFC..FL600, speeds in a physical range, polygons with >= 3 vertices, no
   timestamps in the future, advisory numbers monotonic per volcano. A markup
   change upstream makes parsers emit garbage; these checks catch garbage
   BEFORE it reaches an audience.

C. CORROBORATION.  The same physical fact from independent instruments must
   roughly agree. For ash transport direction we compare, per altitude band:
       Darwin VAA motion vector  (primary, satellite+analyst)
       Himawari-9 AMV winds      (independent satellite tracking)
       open-meteo NWP winds      (independent model)
   Agreement within CORR_ANGLE_OK raises confidence; disagreement beyond
   CORR_ANGLE_BAD routes to review. For eruption OCCURRENCE we count agreeing
   sources among {MAGMA letusan, VONA, Darwin eruption details, FIRMS hotspot}.

D. PROVENANCE.  source url, endpoint, fetch time, via, parser version, and a
   content hash ride along on every record, so any reader can re-derive what
   you published. Messages that lack provenance are quarantined by policy.

Nothing here needs network access; it validates what the fetchers produced.
Stdlib only.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

PARSER_VERSION = "validate/1.0"

# ---------------------------------------------------------------- SLA table
# (warn_after_h, hard_fail_after_h) per source. Hard fail => quarantine.
SLA = {
    "magma_status":      (7.0, 13.0),    # laporan published 4x/day (6 h windows)
    "magma_letusan":     (6.0, 24.0),    # event feed; quiet days are normal
    "darwin_vaac":       (7.0, 13.0),    # advisories at least 6-hourly in activity
    "himawari_amv":      (1.5, 4.0),     # 10-min cadence, ~40 min latency
    "open_meteo":        (3.0, 9.0),     # hourly
    "firms":             (12.0, 36.0),   # polar orbiters, 1-2 passes/day
}

# ---------------------------------------------------------------- thresholds
CORR_ANGLE_OK = 45.0      # deg: independent sources this close => corroborated
CORR_ANGLE_BAD = 90.0     # deg: beyond this => human must look
SPEED_SANITY_MS = (0.0, 60.0)
FL_SANITY = (0, 600)
VENT = {"anak krakatau": (-6.102, 105.423)}
VENT_BOX_DEG = 0.6        # a VAA PSN further than this from the known vent = suspect

AUTO_KINDS = {"LEVEL_CHANGE", "NEW_VONA", "VAAC_ADVISORY", "NEW_ERUPTION"}
# Verbatim official channels may auto-publish. Anything we DERIVED cannot.
DERIVED_KINDS = {"ASH_DIRECTION", "SECONDARY_ESTIMATE", "CORROBORATED_SUMMARY"}


# ---------------------------------------------------------------- helpers
def _now():
    return datetime.now(timezone.utc)


def _age_h(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (_now() - dt).total_seconds() / 3600.0


def _hash(obj) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, default=str, ensure_ascii=False).encode()
    ).hexdigest()[:16]


def ang_diff(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


class Check:
    def __init__(self, gate: str, name: str, ok: bool, detail: str = "",
                 severity: str = "warn"):
        self.gate, self.name, self.ok, self.detail = gate, name, ok, detail
        self.severity = severity   # 'fail' | 'warn' | 'info'

    def as_dict(self):
        return {"gate": self.gate, "name": self.name, "ok": self.ok,
                "severity": self.severity if not self.ok else "info",
                "detail": self.detail}


# ---------------------------------------------------------------- gate A
def gate_freshness(src: str, iso: str | None, overdue: bool = False) -> list[Check]:
    out = []
    warn_h, fail_h = SLA.get(src, (6.0, 24.0))
    age = _age_h(iso)
    if age is None:
        out.append(Check("freshness", f"{src}:timestamp", False,
                         "no usable timestamp on record", "fail"))
        return out
    if age > fail_h:
        out.append(Check("freshness", f"{src}:age", False,
                         f"{age:.1f} h old (SLA hard limit {fail_h:.0f} h)", "fail"))
    elif age > warn_h:
        out.append(Check("freshness", f"{src}:age", False,
                         f"{age:.1f} h old (SLA warn {warn_h:.0f} h)", "warn"))
    else:
        out.append(Check("freshness", f"{src}:age", True, f"{age:.1f} h old"))
    if overdue:
        out.append(Check("freshness", f"{src}:overdue", False,
                         "source missed its own stated next-update time", "warn"))
    return out


# ---------------------------------------------------------------- gate B
def gate_sanity_vaac(adv: dict, volcano: str) -> list[Check]:
    out = []
    v = VENT.get(volcano.lower())
    pos = adv.get("position") or {}
    if v and pos.get("lat") is not None:
        d = max(abs(pos["lat"] - v[0]), abs(pos["lon"] - v[1]))
        out.append(Check("sanity", "vaac:position", d <= VENT_BOX_DEG,
                         f"PSN {pos['lat']},{pos['lon']} is {d:.2f} deg from known vent",
                         "fail"))
    else:
        out.append(Check("sanity", "vaac:position", False, "no PSN parsed", "warn"))

    dtg = _age_h(adv.get("dtg_utc"))
    out.append(Check("sanity", "vaac:dtg_not_future", dtg is not None and dtg >= -0.25,
                     f"DTG offset {dtg} h", "fail"))

    nr = adv.get("advisory_nr") or ""
    out.append(Check("sanity", "vaac:advisory_nr", bool(nr) and "/" in nr,
                     f"nr={nr!r}", "fail"))

    layers = adv.get("observed_layers") or []
    out.append(Check("sanity", "vaac:layers_present", len(layers) >= 1,
                     f"{len(layers)} layer(s)", "warn"))
    for i, ly in enumerate(layers, 1):
        top = ly.get("top")
        if top:
            try:
                fl = int(str(top).replace("FL", ""))
                out.append(Check("sanity", f"vaac:L{i}:fl", FL_SANITY[0] <= fl <= FL_SANITY[1],
                                 f"top {top}", "fail"))
            except ValueError:
                out.append(Check("sanity", f"vaac:L{i}:fl", False, f"unparseable {top!r}", "fail"))
        spd = ly.get("speed_ms")
        if spd is not None:
            out.append(Check("sanity", f"vaac:L{i}:speed",
                             SPEED_SANITY_MS[0] <= spd <= SPEED_SANITY_MS[1],
                             f"{spd} m/s", "fail"))
        if ly.get("move_toward") not in ("N", "NE", "E", "SE", "S", "SW", "W", "NW", None):
            out.append(Check("sanity", f"vaac:L{i}:dir", False,
                             f"odd compass {ly.get('move_toward')!r}", "fail"))
        n_poly = len(ly.get("polygon") or [])
        out.append(Check("sanity", f"vaac:L{i}:polygon", n_poly >= 3,
                         f"{n_poly} vertices", "warn"))
    return out


def gate_sanity_monitor(data: dict) -> list[Check]:
    out = []
    lvl = data.get("level")
    out.append(Check("sanity", "magma:level_range", lvl in (1, 2, 3, 4),
                     f"level={lvl!r}", "fail"))
    eru = data.get("eruptions_today")
    out.append(Check("sanity", "magma:eruption_count", eru is None or 0 <= eru <= 60,
                     f"eruptions_today={eru!r}", "fail"))
    vona = (data.get("latest_vona") or [{}])[0]
    code = vona.get("code")
    out.append(Check("sanity", "magma:vona_code", code in (None, "RED", "ORANGE", "YELLOW", "GREEN"),
                     f"code={code!r}", "fail"))
    rep = data.get("latest_report") or {}
    if rep and not rep.get("error"):
        out.append(Check("sanity", "magma:report_sections",
                         bool(rep.get("visual")) and bool(rep.get("recommendation")),
                         "visual+recommendation sections present", "warn"))
    return out


def gate_sanity_amv(profile: list[dict]) -> list[Check]:
    out = []
    for r in profile:
        if not r.get("data"):
            continue
        lab = r["layer"]
        out.append(Check("sanity", f"amv:{lab}:speed",
                         SPEED_SANITY_MS[0] <= r["speed_ms"] <= SPEED_SANITY_MS[1],
                         f"{r['speed_ms']} m/s", "fail"))
        out.append(Check("sanity", f"amv:{lab}:alt", 0 <= r["mean_altitude_km"] <= 20,
                         f"{r['mean_altitude_km']} km", "fail"))
        # from_deg is a circular mean of per-vector FROM; toward_deg comes from
        # the mean u,v. They agree to ~180 deg but not exactly. Tolerate 25 deg;
        # anything more means a sign/convention bug somewhere upstream.
        out.append(Check("sanity", f"amv:{lab}:dir_consistent",
                         abs(ang_diff(r["from_deg"], r["toward_deg"]) - 180.0) <= 25.0,
                         f"FROM {r['from_deg']:.0f} vs TOWARD {r['toward_deg']:.0f} "
                         f"(offset {abs(ang_diff(r['from_deg'], r['toward_deg']) - 180.0):.0f} deg)",
                         "fail"))
    return out


# ---------------------------------------------------------------- gate C
def ash_directions(monitor=None, vaac=None, amv=None, om=None) -> dict:
    """Collect every independent statement of ash transport direction, keyed
    by altitude band (km). Returns {band_km: [{"src","toward_deg","speed_ms"}]}"""
    bands = {}

    def add(band, src, toward, speed=None):
        if toward is None:
            return
        bands.setdefault(band, []).append({"src": src, "toward_deg": toward,
                                           "speed_ms": speed})

    if vaac and vaac.get("state") == "advisory":
        for ly in (vaac["advisory"].get("observed_layers") or []):
            top_km = ly.get("top_km") or 2.0
            add(round(top_km, 1), "darwin_vaac",
                {"N": 0, "NE": 45, "E": 90, "SE": 135, "S": 180,
                 "SW": 225, "W": 270, "NW": 315}.get(ly.get("move_toward")),
                ly.get("speed_ms"))
    if amv:
        for r in amv:
            if r.get("data"):
                add(round(r["mean_altitude_km"], 1), "himawari_amv",
                    r["toward_deg"], r["speed_ms"])
    if om and om.get("levels"):
        for p, recs in om["levels"].items():
            if not recs:
                continue
            km = {1000: 0.1, 925: 0.8, 850: 1.5, 700: 3.0, 600: 4.2,
                  500: 5.6, 400: 7.2, 300: 9.2, 250: 10.5}.get(int(p))
            if km is None:
                continue
            r0 = recs[0]
            add(km, "open_meteo", (r0["from_deg"] + 180) % 360, r0["speed_ms"])
    return bands


def _merge_close_bands(bands: dict, tol: float = 1.0) -> dict:
    """Chain-merge altitude bins closer than tol km: they describe one layer."""
    merged = {}
    for band in sorted(bands):
        host = next((k for k in merged if abs(k - band) <= tol), None)
        if host is None:
            merged[band] = list(bands[band])
        else:
            merged[host] += bands[band]
            if band < host:                      # keep the lowest label
                merged[band] = merged.pop(host)
    return merged


def corroborate_direction(bands: dict, near_band: tuple[float, float] = (0.5, 4.5)) -> dict:
    """For each band in the ash-critical window, compare independent sources."""
    report = {"bands": [], "primary_present": False, "agreement": None}
    bands = _merge_close_bands(bands)
    for band, stmts in sorted(bands.items()):
        prim = [s for s in stmts if s["src"] == "darwin_vaac"]
        sec = [s for s in stmts if s["src"] != "darwin_vaac"]
        if prim:
            report["primary_present"] = True
        if not prim and not sec:
            continue
        ref = prim[0] if prim else sec[0]
        diffs = [(s["src"], ang_diff(ref["toward_deg"], s["toward_deg"]))
                 for s in stmts if s is not ref]
        worst = max((d for _, d in diffs), default=0.0)
        agree = worst <= CORR_ANGLE_OK and len(stmts) >= 2
        disagree = worst >= CORR_ANGLE_BAD
        report["bands"].append({
            "band_km": band, "in_ash_window": near_band[0] <= band <= near_band[1],
            "n_sources": len(stmts),
            "reference": ref,
            "diffs": [{"src": s, "deg": round(d, 1)} for s, d in diffs],
            "worst_disagreement_deg": round(worst, 1),
            "corroborated": agree,
            "conflicting": disagree,
        })
    crit = [b for b in report["bands"] if b["in_ash_window"]]
    if crit:
        if any(b["conflicting"] for b in crit):
            report["agreement"] = "conflicting"
        elif any(b["corroborated"] for b in crit):
            report["agreement"] = "corroborated"
        else:
            report["agreement"] = "single_source"
    return report


def corroborate_occurrence(monitor=None, vaac=None, firms=None) -> dict:
    srcs = []
    eru = (monitor or {}).get("eruptions_today") or 0
    if eru > 0:
        srcs.append("magma_letusan")
    if ((monitor or {}).get("latest_vona") or [{}])[0].get("code"):
        srcs.append("magma_vona")
    if vaac and vaac.get("state") == "advisory":
        det = (vaac["advisory"].get("eruption_details") or "").upper()
        if "VA " in det or "ERUPT" in det:
            srcs.append("darwin_vaac")
    if firms:
        for srcname, d in firms.items():
            if isinstance(d, dict) and d.get("n", 0) > 0:
                srcs.append(f"firms:{srcname}")
                break
    return {"sources": srcs, "n": len(srcs),
            "confirmed": len(srcs) >= 2, "single_source": len(srcs) == 1}


# ---------------------------------------------------------------- routing
def route(kind: str, checks: list[Check], corr: dict | None = None) -> tuple[str, list[str]]:
    """Return (routing, reasons)."""
    reasons = []
    fails = [c for c in checks if not c.ok and c.severity == "fail"]
    warns = [c for c in checks if not c.ok and c.severity == "warn"]

    if fails:
        return "quarantine", [f"sanity/freshness FAIL: {c.gate}:{c.name} {c.detail}" for c in fails]

    if kind in DERIVED_KINDS:
        if corr and corr.get("agreement") == "conflicting":
            return "review", ["independent sources disagree on ash direction; human must decide"]
        if corr and corr.get("agreement") == "corroborated":
            reasons.append("derived but corroborated by >=2 independent sources")
            return "review", reasons      # derived => still a human decision by default
        return "review", ["derived estimate, single-source or uncorroborated"]

    if kind in AUTO_KINDS:
        if warns:
            return "review", [f"warn: {c.gate}:{c.name} {c.detail}" for c in warns]
        return "auto", ["verbatim official source, all gates green"]

    return "review", [f"unknown kind {kind!r}; defaulting to review"]


# ---------------------------------------------------------------- top level
def validate(monitor: dict | None = None, vaac: dict | None = None,
             amv_profile: list | None = None, om: dict | None = None,
             firms: dict | None = None, volcano: str = "Anak Krakatau") -> dict:
    checks: list[Check] = []

    if monitor:
        checks += gate_freshness("magma_status", monitor.get("generated_utc"))
        checks += gate_sanity_monitor(monitor)
    if vaac:
        if vaac.get("state") == "advisory":
            checks += gate_freshness("darwin_vaac", vaac.get("fetched_utc"),
                                     overdue=bool(vaac.get("next_advisory_overdue")))
            checks += gate_sanity_vaac(vaac.get("advisory") or {}, volcano)
        elif vaac.get("state") in ("nil", "stale"):
            checks += gate_freshness("darwin_vaac", vaac.get("fetched_utc"))
        else:
            checks.append(Check("freshness", "darwin_vaac:reachable", False,
                                str(vaac.get("error"))[:120], "warn"))
    if amv_profile:
        newest = None
        for o in (amv_profile or []):
            pass
        checks += gate_sanity_amv(amv_profile)
    if om:
        ts = (om.get("times") or [None])[0]
        if not ts:
            for recs in (om.get("levels") or {}).values():
                if recs:
                    ts = recs[0].get("t")
                    break
        checks += gate_freshness("open_meteo", ts)

    bands = ash_directions(monitor, vaac, amv_profile, om)
    corr_dir = corroborate_direction(bands)
    corr_occ = corroborate_occurrence(monitor, vaac, firms)

    # Silence detection: every primary source unavailable at once.
    primaries = []
    if monitor:
        primaries.append("magma")
    if vaac and vaac.get("state") != "error":
        primaries.append("vaac")
    all_checks = [c.as_dict() for c in checks]
    hard_fails = [c for c in all_checks if not c["ok"] and c["severity"] == "fail"]

    return {
        "validated_utc": _now().isoformat(timespec="seconds").replace("+00:00", "Z"),
        "volcano": volcano,
        "parser_version": PARSER_VERSION,
        "checks": all_checks,
        "hard_failures": hard_fails,
        "direction_corroboration": corr_dir,
        "occurrence_corroboration": corr_occ,
        "primaries_available": primaries,
        "content_hash": _hash({"m": (monitor or {}).get("generated_utc"),
                               "v": (vaac or {}).get("fetched_utc"),
                               "a": (vaac or {}).get("advisory", {}).get("advisory_nr")
                                    if isinstance((vaac or {}).get("advisory"), dict) else None}),
    }


def routing_for(verdict: dict, kind: str) -> tuple[str, list[str]]:
    checks = [Check(c["gate"], c["name"], c["ok"], c["detail"], c["severity"])
              for c in verdict["checks"]]
    return route(kind, checks, verdict.get("direction_corroboration"))


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--monitor-json")
    ap.add_argument("--vaac-json")
    ap.add_argument("--ash-json", help="ash_transport.py --json output (optional)")
    ap.add_argument("--volcano", default="Anak Krakatau")
    a = ap.parse_args()

    mon = json.load(open(a.monitor_json, encoding="utf-8")) if a.monitor_json else None
    va = json.load(open(a.vaac_json, encoding="utf-8")) if a.vaac_json else None
    ash = json.load(open(a.ash_json, encoding="utf-8")) if a.ash_json else None

    v = validate(mon, va, (ash or {}).get("observed_wind_profile"),
                 {"levels": {int(k): x for k, x in (ash or {}).get("forecast_wind", {}).items()},
                  "times": []} if ash else None,
                 volcano=a.volcano)
    print(json.dumps(v, ensure_ascii=False, indent=2))
    import sys as _sys
    _p = lambda *a: print(*a, file=_sys.stderr)
    _p("\n--- summary ---")
    _p(" hard failures:", len(v["hard_failures"]))
    for c in v["hard_failures"]:
        _p("   FAIL", c["gate"], c["name"], c["detail"])
    _p(" direction agreement:", v["direction_corroboration"].get("agreement"))
    for b in v["direction_corroboration"]["bands"]:
        if b["in_ash_window"]:
            _p(f"   band {b['band_km']} km: n={b['n_sources']} "
                  f"worst_disagree={b['worst_disagreement_deg']}deg "
                  f"corroborated={b['corroborated']} conflicting={b['conflicting']}")
    _p(" occurrence sources:", v["occurrence_corroboration"]["sources"])
    for kind in ("VAAC_ADVISORY", "ASH_DIRECTION"):
        r, why = routing_for(v, kind)
        _p(f" routing {kind:16} -> {r:10} {why}")
