#!/usr/bin/env python3
"""
operate.py — one command that runs the whole pipeline.

    python3 operate.py                 # collect -> validate -> route -> publish
    python3 operate.py --dry-run       # show what WOULD happen, send nothing
    python3 operate.py --no-ash        # skip the heavy satellite step
    python3 operate.py --respond       # apply human approve/hold/kill taps
    python3 operate.py --dashboard     # also rebuild dashboard.html + ash_map.svg

Cron / GitHub Actions only ever needs THIS script. Everything else is a library.

Pipeline:
   1. PRIMARY   MAGMA/PVMBG      volcano_monitor.collect()
   2. PRIMARY   Darwin VAAC      darwin_vaac.fetch()        (JSON endpoint)
   3. SECONDARY Himawari+open-m  ash_transport.py --json    (subprocess)
   4. OPTIONAL  FIRMS hotspots   publish.firms via ash_transport (needs key)
   5. VALIDATE                   validate.validate()        -> verdict
   6. ROUTE                      publish.run_workflow()     -> auto / draft / quarantine
   7. HEARTBEAT                  publish.Supabase.heartbeat()

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

import volcano_monitor
import darwin_vaac
import validate as V
import publish as P


def run_ash(args) -> dict | None:
    cmd = [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "ash_transport.py"), "--json", "--no-svg",
           "--slots", str(args.slots), "--hours", str(args.hours)]
    if args.offline_ash:
        cmd.append("--offline")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=args.ash_timeout)
        if r.returncode != 0:
            print(f"[ash] exit {r.returncode}: {r.stderr.strip()[:200]}", file=sys.stderr)
            return None
        return json.loads(r.stdout)
    except subprocess.TimeoutExpired:
        print("[ash] timed out; continuing without secondary estimate", file=sys.stderr)
        return None
    except Exception as e:  # noqa: BLE001
        print(f"[ash] {type(e).__name__}: {e}", file=sys.stderr)
        return None


def build_events(mon: dict, vaac: dict, ash: dict | None) -> list[dict]:
    evs = P.events_from_monitor(mon, vaac)

    # Attach the secondary estimate as its own DERIVED event, so it can only
    # ever travel the review path. It never inherits the auto routing that
    # verbatim official text gets.
    if ash and ash.get("observed_wind_profile"):
        rows = [r for r in ash["observed_wind_profile"] if r.get("data")]
        if rows:
            best = max(rows, key=lambda r: r.get("consistency_R", 0))
            corr = (ash.get("downwind_exposure") or [])
            evs.append({
                "volcano": mon.get("volcano"),
                "kind": "ASH_DIRECTION",
                "severity": "info",
                "title": f"Perkiraan arah abu (sekunder) — {best['toward_compass']} "
                         f"{best['toward_deg']:.0f}° di {best['layer'].split('ASH')[0].strip()}",
                "body": "\n".join(
                    f"{r['layer']:22} -> {r['toward_compass']:4s} {r['toward_deg']:5.1f}° "
                    f"@{r['speed_ms']:.1f} m/s  R={r['consistency_R']:.2f} n={r['n']} "
                    f"nearest={r.get('nearest_vector_km')}km"
                    for r in rows),
                "url": os.environ.get("DASHBOARD_URL"),
                "attributed_to": "Secondary estimate: Himawari-9 AMV + open-meteo "
                                 "(our calculation, NOT confirmed by PVMBG/VAAC)",
                "occurred_at": ash.get("analysis_utc"),
                "payload": {"best": best, "exposure": corr[:4]},
            })
    return evs


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the whole Krakatau pipeline")
    ap.add_argument("--volcano", default="Anak Krakatau")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-ash", action="store_true", help="skip satellite secondary step")
    ap.add_argument("--offline-ash", action="store_true", help="reuse cached AMVs")
    ap.add_argument("--slots", type=int, default=3)
    ap.add_argument("--hours", type=int, default=12)
    ap.add_argument("--ash-timeout", type=int, default=600)
    ap.add_argument("--dashboard", action="store_true")
    ap.add_argument("--respond", action="store_true", help="apply human decisions, then exit")
    ap.add_argument("--telegram", action="store_true")
    ap.add_argument("--ntfy-auto", action="store_true")
    ap.add_argument("--supabase", action="store_true")
    ap.add_argument("--state", default="state.json")
    ap.add_argument("--log", default="history.jsonl")
    ap.add_argument("--out-dir", default=".")
    args = ap.parse_args()

    t0 = time.time()
    tg, ntfy, sb = P.Telegram(), P.Ntfy(), P.Supabase()
    print(f"[operate] {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%SZ}  "
          f"telegram={'on' if tg.enabled else 'off'} supabase={'on' if sb.enabled else 'off'} "
          f"ntfy={'on' if ntfy.enabled else 'off'} dry_run={args.dry_run}")

    if args.respond:
        return P.run_responder(tg, sb, args)

    # 1-2. primaries ---------------------------------------------------------
    mon = volcano_monitor.collect(args.volcano, None, with_report=True)
    mon["alerts"] = volcano_monitor.diff(volcano_monitor.load_state(args.state), mon)
    vaac = darwin_vaac.fetch(args.volcano)
    print(f"[operate] MAGMA: level={mon.get('level_name')} eruptions_today="
          f"{mon.get('eruptions_today')} | VAAC: state={vaac.get('state')} "
          f"nr={(vaac.get('advisory') or {}).get('advisory_nr')}")

    # 3. secondary -----------------------------------------------------------
    ash = None if args.no_ash else run_ash(args)
    if ash:
        rows = [r for r in ash.get("observed_wind_profile", []) if r.get("data")]
        print(f"[operate] secondary: {len(rows)} wind layers, "
              f"agreement={(ash.get('observed_wind_profile') and 'see verdict')}")

    # 5. validate ------------------------------------------------------------
    verdict = V.validate(mon, vaac,
                         (ash or {}).get("observed_wind_profile"),
                         {"levels": {int(k): x for k, x in (ash or {}).get("forecast_wind", {}).items()},
                          "times": []} if ash else None,
                         volcano=args.volcano)
    nf = len(verdict["hard_failures"])
    print(f"[operate] validate: hard_failures={nf} "
          f"dir_agreement={verdict['direction_corroboration'].get('agreement')} "
          f"occurrence={verdict['occurrence_corroboration']['sources']}")
    for c in verdict["hard_failures"]:
        print(f"   FAIL {c['gate']}:{c['name']} {c['detail']}")

    # 6. route + publish -----------------------------------------------------
    events = build_events(mon, vaac, ash)
    if events:
        rep = P.run_workflow(events, verdict, tg, ntfy, sb, args)
        print(f"[operate] workflow: {rep}")
    else:
        print("[operate] no publishable events this cycle")

    # state + heartbeat ------------------------------------------------------
    if args.state:
        volcano_monitor.save_state(args.state, mon) if hasattr(volcano_monitor, "save_state") \
            else json.dump(mon, open(args.state, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if args.log:
        with open(args.log, "a", encoding="utf-8") as f:
            f.write(json.dumps({**mon, "vaac_state": vaac.get("state"),
                                "verdict_hash": verdict["content_hash"]},
                               ensure_ascii=False) + "\n")
    if sb.enabled:
        sb.heartbeat("ok" if nf == 0 else "degraded",
                     f"vaac={vaac.get('state')} fails={nf} in {time.time()-t0:.0f}s")

    if args.dashboard:
        try:
            html = volcano_monitor.render_html(mon, mon.get("alerts", []))
            open(os.path.join(args.out_dir, "dashboard.html"), "w", encoding="utf-8").write(html)
            print("[operate] dashboard.html written")
        except Exception as e:  # noqa: BLE001
            print(f"[operate] dashboard: {e}", file=sys.stderr)

    print(f"[operate] done in {time.time()-t0:.0f}s")
    return 2 if nf else 0


if __name__ == "__main__":
    sys.exit(main())
