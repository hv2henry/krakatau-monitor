# krakatau-monitor

A community-run dashboard that watches **Anak Krakatau** (the active
volcano in the Sunda Strait, Indonesia) and, when it erupts, follows the
**volcanic ash cloud**—in both Indonesian and English.

> **This is an unofficial, hobbyist project.** It is run by the community,
> for the community. It is **not** an official product of any government
> agency, and it is **not an early-warning system**—it must never be your
> reference for eruption or ash-hazard warnings. For anything that matters
> to safety, use the official channels listed below.

**Live site:** <https://hv2henry.github.io/krakatau-monitor/>

---

## 1. What this project is

The dashboard collects what the official sources publish about Anak
Krakatau, keeps it in one bilingual page, and—as a clearly-labelled
*secondary*, experimental layer—computes its own ash-transport model
from open satellite and weather data. Scheduling no longer lives inside
the workflows: a Supabase-hosted `pg_cron` layer (**phase 2, live**—see
[`supabase/`](supabase/)) fires the 15-minute site build and the 6-hourly
model run, records their heartbeats, and pings Discord when the pipeline
goes quiet. When the volcano itself goes quiet, the model schedule pauses
itself (graceful stop) and the page labels the last computation as an
archive instead of passing it off as current. Every model run is archived
so anyone can check, after the fact, how well it did against the official
advisories. Nothing here is hidden: every derived number ships with its
caveats, and the model's historical error statistics are printed on the
page itself.

**Official channels first—always:**

- **MAGMA Indonesia/PVMBG** (activity level, eruption reports, VONA):
  <https://magma.esdm.go.id>
- **Darwin VAAC** (aviation ash advisories—the authority on ash
  clouds): <https://www.bom.gov.au/aviation/volcanic-ash/darwin-va-advisory.shtml>
- **BMKG** (weather, tsunami): <https://www.bmkg.go.id>
- **BNPB/BPBD** (disaster management): <https://www.bnpb.go.id>

## 2. What's on the site, and where it comes from

| Section | Source |
|---------|--------|
| Activity status (Level I–IV) & observation reports | MAGMA Indonesia/PVMBG (Badan Geologi, ESDM) |
| Eruption log & VONA (aviation colour codes) | MAGMA Indonesia/PVMBG |
| Volcanic ash advisories, polygons & the original WMO bulletin text | Darwin VAAC (Bureau of Meteorology, Australia)—ICAO-designated authority for this region |
| Infrared animation (10-min cadence) | Himawari-9 AHI Band 13 via NASA GIBS (JMA/NOAA open data) |
| Daily true-colour imagery | Suomi NPP VIIRS & Aqua/Terra MODIS via NASA GIBS/Earthdata |
| Schematic map (VAAC polygons, model tracks, envelopes) | rendered locally; coastlines © Natural Earth |

**The differentiator—the experimental secondary model.** Where other
volcano dashboards stop at republishing official data, this project also
computes its own ash-transport estimate from open data (Himawari-9
atmospheric motion vectors from NOAA's S3 bucket, open-meteo NWP
pressure-level winds) and puts it **next to** the official advisory, fully
labelled as secondary. Highlights of the current model version:

- **Mass-coupled envelope:** the detectable-ash width is tied to the
  airborne mass (settling of four particle classes, wet scavenging in
  rain, wind-shear spreading), so the cloud can *grow and then shrink* —
  the behaviour real VAAC polygons show—instead of growing forever.
- **Emission-history-aware start:** the model reads the age of the cloud
  from the observed polygon width instead of assuming the ash was emitted
  at the analysis time.
- **Dual union hull:** a combined envelope across height bands at/below the
  official cloud top—the shape comparable to Darwin's multi-layer
  polygons—plus an opt-in all-bands worst-case hull on the map, where
  tracks, per-layer envelopes and hulls are separate toggles.
- **Honesty plumbing:** every model run passes sanity gates before it can
  be published, auto-published runs must have zero hard failures, caveats
  travel with the data onto the page, and a public backtest ledger tracks
  the model's direction and width errors against Darwin VAAC over time.

It is a research toy with guardrails—nothing more. Aviation decisions
belong to VAAC and the airlines' own procedures; ground-safety decisions
belong to PVMBG, BMKG, BNPB/BPBD.

## 3. Repository map

```
site/            the static dashboard (index.html, app.js, assets/, data/)
  └─ data/       volcanoes.json (registry boot file) + one folder per volcano:
                 <slug>/snapshot.json, forecast_model.json, backtest.jsonl, …
src/             the pipeline that builds the site
  ├─ volcanoes.py         the volcano registry—adding a volcano starts HERE
  ├─ volcano_monitor.py   MAGMA/PVMBG collector
  ├─ darwin_vaac.py       Darwin VAAC advisory fetcher + WMO bulletin parser
  ├─ validate.py          validation gates (freshness/sanity/corroboration)
  ├─ ash_transport.py     the secondary ash-transport model (trajectories,
  │                       settling, mass-coupled envelope, union hull)
  ├─ build_site.py        orchestrator: collect → model → validate → publish
  ├─ operate.py           one-command pipeline for cron/self-hosting
  └─ publish.py           optional Telegram/ntfy/Supabase publishing + watchdog
supabase/         phase 2 (live): pg_cron scheduler + watchdog—fires the
                 workflows, syncs heartbeats, pings Discord on silence, and
                 auto-pauses/resumes the 6-hourly model job from activity.state
tools/           calibrate_envelope.py, validate_eventB.py (research scripts)
tests/           model-math + pipeline-wiring tests, DOM tests
archive/         per-volcano 6-hourly archive: <slug>/{models,vaac,index.json}
docs/            ARCHITECTURE.md, ENVELOPE_MODEL.md—how it all fits together
optional/        self-hosting notes, an early phase-2 schema sketch, phase-3 extras
```

Data, archives and the backtest ledger are namespaced per volcano
(`<slug>` from `src/volcanoes.py`), so adding a second volcano—Sinabung,
Semeru, any of the ~70 active Indonesian volcanoes—is a one-file
registry edit; the recipe sits at the top of `src/volcanoes.py`.
`CONTRIBUTING.md` explains how to help; `LICENSE` is MIT.

## 4. Credits & data licences

- **MAGMA Indonesia/PVMBG (Badan Geologi, KESDM)**—activity levels,
  observation reports, eruption logs, VONA. Government open-data portal.
- **Darwin VAAC, Bureau of Meteorology (Australia)**—volcanic ash
  advisories, text and graphics. ICAO-designated VAAC for this region.
- **JMA/NOAA/NASA GIBS & Earthdata**—Himawari-9 AHI imagery and
  Level-2 wind products (NOAA S3 open data); VIIRS/MODIS true colour.
- **open-meteo.com**—multi-model NWP pressure-level winds (ECMWF IFS,
  GFS, ICON), free for open-data use.
- **OpenStreetMap contributors** and **Natural Earth**—map tiles and
  coastline data.

The dashboard code itself is released under the MIT License (see
`LICENSE`). All trademarks and data remain the property of their
respective agencies; this project only republishes their public feeds
with attribution, for situational awareness.

## 5. Safety principles we hold ourselves to

1. **Official sources are the only authority.** We republish and
   cross-check them; we never replace them. The site says so on every
   model section.
2. **No silent failures.** If a source is down, the page shows the outage
   instead of quietly showing old numbers. If a model run fails
   validation, it is not published.
3. **Derived data is labelled as derived.** The secondary model is
   published with its caveats, its validation verdict, its approval
   stamp (human or clearly-marked auto), and its backtest record.
4. **Everything is auditable.** Every 6-hourly model snapshot and VAAC
   state is archived in this repository; the ledger of model-vs-VAAC
   performance is public.
5. **Not for navigation or life-safety decisions.** The map is
   schematic; the model is experimental. Aviation must follow VAAC and
   their operator's procedures; communities must follow PVMBG/BMKG/BNPB.

## 6. Contact

Questions, corrections, or source suggestions are welcome:

<https://henrybeton.my.id/contact>

If you are reporting a possible safety matter, please direct it to the
official agencies above—not to this project.
