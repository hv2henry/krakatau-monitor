# The secondary ash-transport model (envelope v2/v2.1)

This document explains the experimental secondary model: why its envelope
was rewritten, the math it now uses, how it is wired into the pipeline, its
calibration provenance and limits, and the agreed direction for the
provisional (t=0) mode. It is written for contributors; the visitor-facing
story lives in the repo `README.md`, and the whole pipeline's design in
`docs/ARCHITECTURE.md`.

Everything below was developed and verified against the Darwin advisory set
of 8–10 Sep 2026 (advisories 2026/174–209), including the four-polygon
calibration event 2026/209.

---

## 1. Why the envelope model was rewritten

The old cross-track spread was

    sigma(t) = sqrt(2*K0*t) + G*t          (monotonically increasing)

A quantitative validation against Darwin advisory 2026/209 (10 Sep 2026,
SFC/FL050 layer) showed the old model overshooting the VAAC cross-track
widths badly (VAAC itself: 94.2 → 104.7 → 87.6 → 86.3 km across
OBS/+6/+12/+18 h):

| horizon | VAAC width | old model width | overshoot |
|---------|------------|-----------------|-----------|
| +6 h    | 104.7 km   | 158.2 km        | +51 %     |
| +12 h   | 87.6 km    | 204.9 km        | +134 %    |
| +18 h   | 86.3 km    | 248.8 km        | +188 %    |

A least-squares refit of the old form across all four widths returns
**G = −0.48 m/s (negative)** — i.e. the only way the old formula can match
reality is to *shrink* over time, which is unphysical for a diffusion term.
The real VAAC polygons **grow, then decay** (94.2 → 104.7 → 87.6 → 86.3 km),
because "detectable ash" is not a fixed Gaussian: as mass settles out and
the cloud dilutes, the part that is still detectable shrinks again.

So the envelope was rebuilt around the **airborne mass**, not around a
monotonic spread law.

## 2. The v2 model

Per settling class (ultrafine/fine/medium/coarse), per trajectory point:

    sigma_y^2(t) = sigma0^2 + 2*K*t + (s_perp * sigma_h * t)^2   cross-track
    sigma_x^2(t) = sigma0^2 + 2*K*t + (s_par  * sigma_h * t)^2   along-track

    Phi(t)       = sum_c f_c * S_c(t) * W_c(t)                   airborne fraction
    Phi_det_eff  = PHI_DET * sigma_x * sigma_y / sigma0^2        diluted threshold
    w(t)         = sigma_y * sqrt(2 * ln(Phi / Phi_det_eff))     detectable half-width

- `S_c(t)` — settling survival of class c (mass released uniformly over the
  layer depth; density-corrected Stokes velocities).
- `W_c(t)` — below-cloud wet scavenging when the trajectory crosses rain
  (`exp(-SCAV_COEF * rain * dt)`, rain ≥ 0.5 mm/h).
- `s_par/s_perp` — the measured NWP shear across the layer, decomposed
  along/across the mean motion (replaces the ad-hoc constant `G`; the old
  `G_PLUME_GROWTH` is gone).
- Deposition to the surface **removes** mass: `Phi` only decreases.

`w(t)` is non-monotonic by construction: it grows while spread dominates,
then collapses as `Phi` decays below the (dilution-adjusted) detection
threshold. That is the rise-then-fall the VAAC polygons actually show.

**Calibration provenance (be honest, n is tiny):** `K`, `PHI_DET`,
`SIGMA0_KM`, `SHEAR_DAMPING` were fitted to the four widths of advisory
2026/209 with cloud age `t0` free (see `tools/calibrate_envelope.py`):

    K = 6820 m²/s   PHI_DET = 5.59e-4   sigma0 = 2.0 km   SHEAR_DAMPING = 0.10
    t0 = 8.8 h  at the 1040Z observation  →  emission ≈ 0210Z
    = exactly the 09:10 WIB eruption MAGMA logged that morning.

Fit RMS 1.4 km, versus 12.2 km for the best physically-admissible
(`G ≥ 0`) refit of the old form. One event, one volcano (Anak Krakatau):
treat the numbers as a defensible starting point, and remember that a NEW
volcano registered in `src/volcanoes.py` starts with an EMPTY width ledger —
re-fit before trusting magnitudes there.

## 3. v2.1 — emission-history-aware σ0 and the multi-band union

### 3.1 Emission history from the OBS polygon

The calibration's free `t0` "rediscovered" the real eruption time — but
production runs used to start every cloud at `t = 0`, silently assuming the
ash was emitted *at the analysis time*. v2.1 derives the age from the
advisory's own OBS polygon: invert `2*w(t) = W_obs` on the rising branch
(bisection) using the layer's measured shear and survival `Phi`, then shift
the whole envelope time axis to the implied emission. The initial spread
becomes emission-history-aware,

    sigma_eff^2(analysis) = sigma0^2 + 2*K*t_age + (s_perp * t_age)^2

and `Phi(analysis) < 1`: mass that settled out before the analysis is gone.

Stated caveats (they are in the code docstring too): a decaying cloud is
age-ambiguous, so the younger, conservative reading is taken; pre-analysis
rain is unknowable from forecast data, so `W_c` only covers the forecast
window; one age is applied to every band (the OBS polygon describes the
whole detected cloud).

Model CLI:

    --obs-polygon "lat,lon;lat,lon;..."   Darwin advisory OBS vertices
    --obs-mov-deg NW|315                  motion for the width projection
    --obs-layer-km "0,1.52"               OBS layer depth
    --emission-age-h 8.8                  manual override (e.g. MAGMA log)

With none of them the model keeps the fresh-emission `t = 0` default —
old behaviour stays available for nil-advisory days. `build_site.py`
(`obs_polygon_args()`) passes the first three automatically whenever the
current advisory carries an OBS polygon.

### 3.2 Union envelope across bands

Per-band envelopes answer "where is *this* layer's detectable ash". The
polygons Darwin draws are unions across layers — on the 4–6 Sep 2026
paroxysm the SFC/FL500 union fan reached ~1,900 km while no single band
was wider than ~900 km. `union_envelope()` returns the convex hull of all
band envelope polygons (conservative: hull ≥ true union) as a GeoJSON-ready
lon/lat ring plus area, and the SVG map draws it. It is also shipped in the
site JSON as `envelope_union`.

## 4. Where things live (pipeline wiring)

| file | role |
|------|------|
| `src/ash_transport.py` | the whole v2/v2.1 model: class fractions, survival curves, `airborne_fraction`, `envelope_width_series`, `effective_shear_ms`, `implied_emission_age_h`, `union_envelope`, `convex_hull`, `polygon_cross_track_width_km`, OBS-polygon CLI, rewritten `trajectory_settling` (per-point `phi`/`width_km`), `envelope_polygon` on detectable width, JSON blocks `envelope_model` / `envelope_emission` / `envelope_union`. `netCDF4` import guarded — pure-math helpers import without it. |
| `src/validate.py` | `gate_sanity_envelope()` (gate B extension): per-band `width_end`/`phi_end`/`emission_age` bounds, polygon vertex domain box, union area bounds. `validate(..., ash_model=...)` runs it; old cached runs without envelope fields are skipped. `PARSER_VERSION` → `validate/1.1`. |
| `src/build_site.py` | `obs_polygon_args(vaac)` derives the three CLI args from the fetched advisory (first observed layer with a polygon; SFC → base 0 km; `--obs-polygon=` "=" form so negative latitudes survive argparse). Seeds the model subprocess, validates with `ash_model=`, and ships `envelope_model`, `envelope_emission`, `envelope_union` in the site JSON. `width_ledger_entry(vaac, cand)` appends to each `backtest.jsonl` row: `obs_width_km`, `emission_age_h`, `emission_source`, and `fcst_widths` [{h, vaac_km, model_km}] — VAAC FCST polygon widths vs the model's full widths (2 × half-width, max over the bands intersecting the OBS layer; `null` beyond the 12 h model horizon, recorded honestly). |
| `site/app.js` | per-band envelope polygons are `[lon, lat]` (GeoJSON order) and get swapped to `[lat, lon]` for Leaflet (this fixed a real mirroring bug). The union hull is drawn (dashed dark polygon, tooltip with band count + area). The `emission_line` stamp shows the OBS-derived cloud age. |
| `tools/calibrate_envelope.py`, `tools/validate_eventB.py` | research scripts: 5-parameter fit to the 2026/209 widths; independent cross-check on the 4–6 Sep paroxysm with full data provenance. |

Data locations (per-volcano, `<slug>` from `src/volcanoes.py`):
`site/data/<slug>/forecast_model.json` carries the published model,
`site/data/<slug>/backtest.jsonl` is the width ledger future re-fits are
driven from, and `archive/<slug>/models/` keeps the 6-hourly snapshots.

## 5. Status matrix and the provisional (t=0) mode — agreed direction

The model's physics (wind advection from Himawari-9 AMV + open-meteo NWP)
is independent of any bulletin; the v2.1 *wiring* currently anchors on the
advisory's OBS polygon. The agreed target state for the site is this
matrix (maintainer-approved 2026-09-11):

| MAGMA activity | Darwin advisory | Site behaviour |
|---|---|---|
| normal | none | normal — absence of an advisory is NOT a failure |
| erupting | none (yet) | **provisional** secondary estimate, labelled "indicative, awaiting official confirmation" |
| erupting | active | anchored model — bulletin seeds emission history |
| decreasing | active | normal (anchored, decaying) |
| quiet per MAGMA | advisory exists | **bulletin wins for the ash cloud**; shown beside the MAGMA status (different sources ≠ conflict) |
| erupting | stale (> X h old) | degrade from anchored to **provisional + staleness warning** |

Implementation notes agreed for the provisional mode (not yet built):

1. Inputs: eruption time from the MAGMA log/seismicity, column height from
   Himawari-9 IR brightness-temperature inversion (the same satellite the
   AMVs come from), σ₀/mass from the theoretical defaults.
2. Honesty plumbing: a distinct `envelope_mode: "provisional"` field in the
   site JSON, visually distinct rendering, and NO ledger entry until the
   first bulletin arrives — then the run is redone in anchored mode.
3. When that first bulletin lands, record how far the provisional guess was
   off into the ledger, so the t=0 mode earns its own public track record.
4. Transparency: model output should carry a `wind_source` field
   (AMV/blend/NWP-only) so silent degradation is visible on the page.

## 6. Verification status

- `tests/test_model_math.py` — 68 checks pass (model math + registry
  wiring + gate B + ledger reproduction; the flat delivery layout skips
  the build_site/validate section by design).
- Offline E2E through the real `main()` with the 2026/209 OBS polygon:
  6 bands, per-band `emission_age_h` propagation, `envelope_union`
  (6 bands, 31,503 km² hull), `phi` non-increasing along every track.
- SVG smoke: the union ring is drawn and labelled.
- Width ledger: reproduces the canonical VAAC series 94.2/104.7/87.6/
  86.3 km from the bulletin text via the real parser.
- Event B cross-check (4–6 Sep paroxysm, `tools/validate_eventB.py`):
  FL500 direction vs 150 hPa mean 20.9° (max 35°, inside the 45° gate) —
  pass; low-layer direction vs 500 hPa mean 96–108° — **fail, reported
  honestly** (operational mitigation: AMV blend + gate C). The old model's
  monotonic width growth (333 → 568 km) vs the observed collapse
  (896 → 282 km) is now order-correct (325–378 km flat); matching the
  decay *rate* needs a continuous-emission integral — see §7.

## 7. Known limitations & next steps

1. **n = 1 calibration, one volcano.** The width ledger exists precisely
   so the next deep-column event can be re-fitted from data the pipeline
   already stores. A second volcano starts from zero evidence.
2. **Model horizon is 12 h** while VAAC forecasts run to +18 h; the ledger
   records the +18 h VAAC width with `model_km: null`. Extending `--hours`
   in `build_site.py` is a one-line change if wanted.
3. **Decaying clouds are age-ambiguous** (the width curve rises then falls);
   the younger reading is taken — conservative, but worth revisiting with
   the MAGMA eruption log as a prior.
4. **Continuous paroxysms** (25 h of near-continuous eruption on 4–6 Sep)
   need an emission-rate integral rather than a single pulse; the width
   collapse rate will not be right until then.
5. **Low-layer direction** on deep-column days disagrees with 500 hPa flow;
   keep the AMV blend and the gate C checks until that is understood.
6. **Provisional (t=0) mode** is specified (§5) but not yet implemented.
