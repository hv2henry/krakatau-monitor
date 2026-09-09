# Contributing

Thanks for wanting to improve this. It is a public-safety-adjacent project, so
a few rules are non-negotiable — they exist because a wrong or unattributed
message during an eruption can hurt real people.

## Hard rules for forks and derivatives

1. **Keep the disclosures.** The "not an official warning system" banner, the
   secondary-model disclosure (`I18N.disclosure` in `site/app.js`), and the
   "nil advisory ≠ no hazard" wording must survive in any deployed fork.
2. **Keep attribution.** Every displayed value must keep its agency source
   (PVMBG/MAGMA, Darwin VAAC/BoM, NASA, JMA/NOAA). Do not present mirrored or
   derived data as your own observation.
3. **Be polite to the sources.** These are government services under load
   during eruptions. Defaults are ≥15 min for MAGMA/BoM and ≥10 min for
   satellite products. Do not ship tighter defaults; do not add retry storms.
4. **Never publish derived model output as official.** The model section is
   human-gated by design (`status:"approved"` + `approved_by`). Removing that
   gate in a fork is fine for research, but a public deployment must keep a
   human (or an equally explicit automated-validation disclosure) in the loop.

## Improving the ash model — welcome, with evidence

The model is intentionally simple and auditable (stdlib + numpy). If you change
its physics or sources:

- add or update a check in `validate.py` (freshness/sanity/corroboration/provenance). A model change without a validation change will not merge.
- show corroboration against at least one independent source
  (Darwin VAAC motion vector, MAGMA report, FIRMS hotspot, imagery).
- keep every output field's provenance (`via`, `source_url`, fetch time).
- document known failure modes in the README's caveats table.

## Reporting problems

- **Data wrong on the site** → check `data/snapshot.json` freshness first; then
  open an issue with the UTC timestamp and the section.
- **Official data itself wrong** → that is the agency's channel, not this repo.
  PVMBG/BPBD for activity levels; Darwin VAAC for ash advisories.
- **Security issue** → email the maintainer (see repo profile) before public
  disclosure; give a reasonable window.

## Practicalities

- No build step. Plain Python (stdlib; `numpy`+`netCDF4` for the ash module,
  `pillow` for imagery) and plain JS. `node tests/domtest.js` smoke-tests the
  frontend; `tests/fixture_advisory.html` pins the VAAC parser.
- One command regenerates everything: `python3 build_site.py`.
- PRs that change parsing must include or update a fixture test.
