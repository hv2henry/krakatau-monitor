# Architecture & engineering notes

This file records *how* it works and *why*, including the
traps we hit—so future contributors (and forks) don't rediscover them.

## Pipeline

```
GitHub Actions (public repo, every 15 min, free unlimited minutes)
  └─ src/build_site.py               ← the only command CI runs
       ├─ volcanoes.py                 resolves the target volcano (registry:
       │                               coords, MAGMA code, VAAC name, framing)
       ├─ volcano_monitor.collect()    PRIMARY  MAGMA/PVMBG  (HTML, stdlib)
       ├─ darwin_vaac.fetch()          PRIMARY  Darwin VAAC  (JSON endpoint)
       ├─ ash_transport (subprocess)   SECONDARY Himawari-9 AMV (NOAA S3) + open-meteo
       ├─ validate.validate()          gates -> verdict (checks, corroboration)
       ├─ GIBS stitch: Aqua+Suomi true colour (daily), Himawari loop (10-min frames)
       ├─ site/data/volcanoes.json   ← the registry boot file for the frontend
       ├─ site/data/<slug>/snapshot.json          ← everything the website reads
       ├─ site/data/<slug>/forecast_candidate.json (never shown)
       ├─ site/data/<slug>/forecast_model.json    ONLY via human approval flag
       ├─ site/data/<slug>/backtest.jsonl         model-vs-VAAC width ledger
       └─ embed boot payload into site/index.html (offline/preview rendering)
  └─ actions/deploy-pages -> GitHub Pages (static, free)
```

The website never scrapes anything. It only reads local JSON/images,
bootstraps the active volcano from `data/volcanoes.json` (`?volcano=<slug>`
picks another entry), refreshes every 5 min, and renders with plain JS (no
framework: no build step between a non-developer maintainer and their own
site). Adding a volcano is a one-file edit in `src/volcanoes.py`—data,
archives and ledgers namespace themselves under `<slug>`.

## Source hierarchy (never blended)

| Tier | Source | For |
|---|---|---|
| PRIMARY | MAGMA/PVMBG (CVGHM) | alert level, 6-h reports, eruptions, VONA, seismicity |
| PRIMARY | Darwin VAAC (BoM, ICAO) | ash cloud extent/height/motion—**when published** |
| SECONDARY | Himawari-9 AMV + open-meteo | plume estimate; human-gated; always disclosed |
| context | NASA GIBS/FIRMS, Natural Earth | imagery, loops, coastlines |

**`vaac.state == "nil"` or `"stale"` ≠ no hazard.** A VAA is issued only when ash
is identifiable on satellite *and* relevant to aviation. On 2026-09-08 the page
said nil while MAGMA logged five eruptions the same day. Every consumer (site,
agents, future code) must render that distinction explicitly.

## Traps encountered (keep this list)

| Trap | Symptom | Fix in code |
|---|---|---|
| BoM advisory page is a **JS shell** | plain GET always shows "Nil current" fallback | POST `/aviation/php/process.php` (`page=volcanic-ash-darwin&javascript=1`) → JSON; HTML kept as degraded fallback that cannot prove absence; `via` recorded in provenance |
| open-meteo speeds are **km/h** | "31.7 m/s" storm-force nonsense | read `hourly_units`, convert |
| NOAA S3 listing silently empty | `?prefix=A/B/C` returns 0 keys | percent-encode slashes in `prefix` |
| AMV `Wind_Dir` convention | FROM/TOWARD inverted tables | FROM = atan2(−u,−v); TOWARD = FROM+180; validator checks antipodality ±25° |
| GIBS tile matrix sets differ per layer | HTTP 400 TILEMATRIXSET | read capabilities per layer (Level9 CR, Level7 vis, Level6 IR) and format (jpg/png) |
| AMV altitudes vs VAAC layers | "sources disagree" false alarms | ash rides at cloud-top: merge bands within 1 km before corroboration |
| 110m coastlines too coarse for a strait | blob map | Natural Earth 50m, clipped+simplified at build (20 KB) |
| VONA/letusan timestamp formats vary | parse crashes | `_parse_any()` tolerant parser; WIB strings precomputed server-side |
| Supabase free pauses after 1 wk idle | silent archive death | heartbeat row written every run |
| Supabase free egress 5 GB/mo | dashboard polling would blow it | serve readers from Pages; Supabase talks only to CI |

## Validation gates (`validate.py`)

Every publishable claim passes four gates; output is a routing decision:

- **A freshness/SLA** per source (MAGMA ≤7 h, VAAC ≤7 h + its own NXT ADVISORY,
  AMV ≤1.5 h, open-meteo ≤3 h). Over hard limit → quarantine.
- **B sanity/physicality** PSN within 0.6° of the registry vent
  (`src/volcanoes.py`), FL ≤ 600, speeds ≤ 60 m/s,
  polygons ≥ 3 vertices, no future timestamps, FROM/TOWARD antipodal.
- **C corroboration** direction per merged altitude band across VAAC/AMV/NWP
  (≤45° agree = corroborated; ≥90° = conflicting → review); occurrence counted
  across MAGMA+VONA+VAAC+FIRMS.
- **D provenance** url, endpoint, fetch time, `via`, parser version, hash.

Routing: `auto` = verbatim official text, all gates green. `review` = anything
derived (forever, even when corroborated). `quarantine` = any hard failure
(nothing public; operator ping).

Worked example (2026-09-08): VAAC said S; AMV surface said WNW, 1–2 km said W,
2–4 km said SSE. Spread 106° → `conflicting` → derived direction to review.
Machines flag; humans judge.

## Human-in-the-loop

- Web: model appears only if `forecast_model.json` has `status:"approved"` +
  `approved_by` + `approved_utc`. Published via workflow_dispatch flag or
  `build_site.py --approve-forecast --approver NAME`; build refuses on hard
  failures and commits the approved file back (needs `contents: write`).
- Telegram (phase 3, not yet wired): drafts to a private editor channel with
  [Publish/Hold/Kill] inline buttons; responder job polls `getUpdates`, writes
  `events.status` + `approved_by` in Supabase; publisher posts approved only.
  Review items never time out into auto-publish.
- Watchdog: separate workflow; publishes "NO NEWS IS NOT GOOD NEWS" if the
  heartbeat goes stale. Silence must never look like calm.

## Himawari-9 loop

- GIBS `Himawari_AHI_Band13_Clean_Infrared` (night, z6, ×2 LANCZOS upscale) or
  `Himawari_AHI_Band3_Red_Visible_1km` (daylight 06:30–17:30 WIB window, z7).
- 12 frames × 10 min, cropped to the strait, autocontrast, ~250 KB total.
- Client overlays coastline + vent via SVG (web-mercator linear) because raw IR
  at night has no visible geography; eruption-time ticks come from MAGMA feed.
- JMA portal unreachable from CI; BMKG serves only "latest" (no archive) →
  GIBS is the only universally reproducible source. Deliberate choice for an
  open project.

## Quiet state & scheduler control (2026-09-12 lesson)

The day Darwin VAAC ended the Krakatau episode with bulletin 2026/217
("ADVISORY TERMINATED", "NXT ADVISORY: NO FURTHER ADVISORIES"), the pipeline
happily kept rendering the memory of the eruption as the present: the
plume-top fallback quoted a VONA issued 19 days earlier (557 m, 24 Aug) as
the "official ash-cloud top today". Two rules now prevent that class of bug:

- **`src/activity_state.py`** decides `active` vs `quiet` from what the
  fetchers just produced: a current non-terminated advisory, a VONA ≤ 24 h,
  or an eruption row ≤ 6 h means ACTIVE; both sources reachable with nothing
  fresh means NORMAL (`quiet`), with `vaac_terminated` as the authoritative
  sub-reason when the VAAC says so in its own words (`darwin_vaac.episode_status`
  also recognises CANCEL wording). An *unreachable* source can never vote for
  quiet—the previous state is held (hysteresis; absence of data is not
  absence of ash, the 2026-09-08 lesson). The verdict rides in
  `snapshot.json` as `activity{state, reason_code, reason{id,en}, evidence,
  since_utc}` with `since_utc` carried across unchanged assessments.
- **Consequences of quiet**, all graceful: the VONA/AMV plume-top fallbacks
  are suppressed (no official top is *shown* as "today"—the VONA anchor is
  age-gated at 24 h even in active state); the secondary model, its 6-hourly
  auto-publish and the fresh-stamped model archive are skipped; the backtest
  ledger stops appending; the site shows a green "Aktivitas normal" banner
  with the reason, relabels the model card as an ARSIP of the ended episode,
  and badges a terminated bulletin in the VAAC card. The MAGMA alert level
  (Siaga III) is deliberately untouched—it keeps rendering exactly as
  issued while it is in force.
- **Scheduler**: GitHub's native `schedule:` triggers were removed from both
  workflows when the cadence moved to Supabase `pg_cron`.
  Pause/resume verdict lands in the committed `site/data/<slug>/snapshot.json`
  (`activity.state`); the pg_cron job every 5 min reads that file
  from raw.githubusercontent.com. `public.set_model_scheduler()` →
  `cron.alter_job` to pause/resume the model job, with `trigger_model_run()`
  re-checking the state flag before every dispatch. Idempotent and self-healing
  (the SQL no-ops when unchanged; a failed fetch raises and the state stays put;
  worst case one extra dispatch that hits the build-side quiet gate and does no work);
  the build-side gates above work even with no Supabase side configured at
  all—the pg_cron layer only stops the *dispatch*, the Python layer stops
  the *work*.

## Agent-friendly surface

`data/<slug>/snapshot.json` (schema v1, `<link rel=alternate>` + JSON-LD),
`data/<slug>/forecast_model.json`, `llms.txt`, `agent.md`, `robots.txt`.
Semantics agents must respect are documented there (nil≠safe, move_toward is
TOWARD, prefer `*_human_*` altitude strings, never quote the candidate file).

## Tests

- `node tests/domtest.js` (+ `--offline`, `--en`)—headless render of every
  dashboard section against real data; catches runtime errors `--check` can't.
- `python3 tests/test_model_math.py`—the envelope math + registry wiring.
- `python3 tests/test_quiet_state.py`—the activity state machine, the
  terminated-bulletin parsing (real 2026/217 text as fixture), the VONA
  age gate and the MAGMA timestamp format.
- `python3 darwin_vaac.py --fixture tests/fixture_advisory.html`—pins the
  WMO bulletin parser (positions, FL→km, motion, forecasts).
- `python3 publish.py --dry-run`—routing preview without sending.
PRs that change parsing must add/update a fixture.

## Self-hosting (alternative to GitHub Actions)

`optional/selfhost-linux/systemd-timer.txt` (preferred: journald logs,
`Persistent=true` catch-up, network ordering) or `crontab.txt` (only where
systemd is absent). The Telegram-phase workflows live in
`optional/phase3-telegram/` and get copied into `.github/workflows/` when wired.
See `optional/README.md` for the phase map.
