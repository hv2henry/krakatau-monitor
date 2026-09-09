# Architecture & engineering notes

This file records *how* it works and *why*, including the
traps we hit — so future contributors (and forks) don't rediscover them.

## Pipeline

```
GitHub Actions (public repo, every 15 min, free unlimited minutes)
  └─ src/build_site.py               ← the only command CI runs
       ├─ volcano_monitor.collect()    PRIMARY  MAGMA/PVMBG  (HTML, stdlib)
       ├─ darwin_vaac.fetch()          PRIMARY  Darwin VAAC  (JSON endpoint)
       ├─ ash_transport (subprocess)   SECONDARY Himawari-9 AMV (NOAA S3) + open-meteo
       ├─ validate.validate()          gates -> verdict (checks, corroboration)
       ├─ GIBS stitch: Aqua+Suomi true colour (daily), Himawari loop (10-min frames)
       ├─ site/data/snapshot.json      ← everything the website reads
       ├─ site/data/forecast_candidate.json   (never shown)
       ├─ site/data/forecast_model.json       ONLY via human approval flag
       └─ embed boot payload into site/index.html (offline/preview rendering)
  └─ actions/deploy-pages -> GitHub Pages (static, free)
```

The website never scrapes anything. It reads local JSON/images only, refreshes
every 5 min, and renders with plain JS (no framework: no build step between a
non-developer maintainer and their own site).

## Source hierarchy (never blended)

| Tier | Source | For |
|---|---|---|
| PRIMARY | MAGMA / PVMBG (CVGHM) | alert level, 6-h reports, eruptions, VONA, seismicity |
| PRIMARY | Darwin VAAC (BoM, ICAO) | ash cloud extent/height/motion — **when published** |
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
- **B sanity/physicality** PSN within 0.6° of vent, FL ≤ 600, speeds ≤ 60 m/s,
  polygons ≥3 vertices, no future timestamps, FROM/TOWARD antipodal.
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

## Agent-friendly surface

`data/snapshot.json` (schema v1, `<link rel=alternate>` + JSON-LD),
`data/forecast_model.json`, `llms.txt`, `agent.md`, `robots.txt`.
Semantics agents must respect are documented there (nil≠safe, move_toward is
TOWARD, prefer `*_human_*` altitude strings, never quote the candidate file).

## Tests

- `node tests/domtest.js` (+ `--offline`, `--en`) — headless render of every
  dashboard section against real data; catches runtime errors `--check` can't.
- `python3 darwin_vaac.py --fixture tests/fixture_advisory.html` — pins the
  WMO bulletin parser (positions, FL→km, motion, forecasts).
- `python3 publish.py --dry-run` — routing preview without sending.
PRs that change parsing must add/update a fixture.

## Self-hosting (alternative to GitHub Actions)

`optional/selfhost-linux/systemd-timer.txt` (preferred: journald logs,
`Persistent=true` catch-up, network ordering) or `crontab.txt` (only where
systemd is absent). The Telegram-phase workflows live in
`optional/phase3-telegram/` and get copied into `.github/workflows/` when wired.
See `optional/README.md` for the phase map.
