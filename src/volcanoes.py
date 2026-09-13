"""
volcanoes.py — the single registry of volcanoes this dashboard monitors.

Every module that used to hard-code "Anak Krakatau", its coordinates, its
MAGMA code or its Darwin VAAC name now resolves it here. Adding a second
volcano is therefore a ONE-FILE change plus a pipeline flag, not a rewrite:

    1. Add an entry to VOLCANOES below (slug, name, aliases, lat/lon,
       magma_code, vaac_name, region).
       - magma_code: the 3-letter code in MAGMA's VONA page URL
         (https://magma.esdm.go.id/v1/vona — ?code=XXX). If you are unsure,
         leave it as None: volcano_monitor auto-discovers the real code from
         the VONA page's volcano list at runtime.
       - vaac_name: the name Darwin VAAC uses in its bulletins (usually the
         uppercase short name, e.g. SINABUNG — see darwin_vaac.NAME_ALIASES).
    2. That is all the pipeline needs: build_site.py namespaces
       site/data/<slug>/ and archive/<slug>/ automatically, app.js boots from
       site/data/volcanoes.json, and the 15-min/6-h workflows pick the
       primary volcano by default.
    3. Run the tests: python3 tests/test_model_math.py
    4. Calibration honesty: the envelope constants in ash_transport.py
       (K, PHI_DET, SIGMA0_KM, SHEAR_DAMPING) were fitted on ONE Krakatau
       event. A new volcano starts with an EMPTY backtest ledger
       (site/data/<slug>/backtest.jsonl) — treat the model output as
       uncalibrated until that ledger grows, and re-fit before trusting
       widths. Direction (wind advection) is far more transferable than
       magnitude.

Example entry (Sinabung — do not enable until you actually want to run it):

    "sinabung": {
        "slug": "sinabung",
        "name": "Sinabung",
        "aliases": ["gunung sinabung"],
        "lat": 3.17, "lon": 98.392,
        "magma_code": None,          # auto-discovered from the VONA page
        "vaac_name": "SINABUNG",
        "region": "Karo, North Sumatra",
        "tz": "Asia/Jakarta",
    },

Optionally, per-volcano imagery framing (otherwise derived from lat/lon):
    "sat_box":  (lon0, lon1, lat0, lat1),   # daily true-colour tile box
    "loop_box": (lon0, lon1, lat0, lat1),   # Himawari tile fetch box
    "loop_crop": (lon0, lon1, lat0, lat1),  # pixel crop after stitching
and per-volcano regional links (merged into official_links):
    "links": {"bpbd_lampung": "...", "bpbd_banten": "..."}

The FIRST entry is the primary volcano — the default for every --volcano
flag and for the scheduled workflows. Keep Anak Krakatau first unless the
project's focus really moves.
"""

from __future__ import annotations

# ---------------------------------------------------------------- registry
VOLCANOES = {
    "anak-krakatau": {
        "slug": "anak-krakatau",
        "name": "Anak Krakatau",
        "aliases": ["krakatau", "krakatoa", "gunung krakatau"],
        "lat": -6.102, "lon": 105.423,
        "magma_code": "KRA",
        "vaac_name": "KRAKATAU",
        "region": "Sunda Strait (Lampung/Banten)",
        "tz": "Asia/Jakarta",
        # regional official links merged into the site's official_links
        "links": {
            "bpbd_lampung": "https://bpbd.lampungprov.go.id",
            "bpbd_banten": "https://bpbd.bantenprov.go.id",
        },
        # daily true-colour framing: Sunda Strait close-up (3.6 x 2.6 deg,
        # vent centred) — the old 12 x 11 deg regional box rendered the
        # volcano as a few pixels; regional context stays on the Himawari
        # loop and the VAAC map
        "sat_box": (103.6, 107.2, -7.4, -4.8),
        "loop_box": (100.0, 111.0, -11.0, 0.0),
        "loop_crop": (100.4, 110.4, -10.5, -2.5),
    },
}


# ---------------------------------------------------------------- lookup
def all_volcanoes() -> list[dict]:
    """All registry entries, in declaration order (first = primary)."""
    return list(VOLCANOES.values())


def primary() -> dict:
    """The primary volcano: default target of every --volcano flag."""
    return next(iter(VOLCANOES.values()))


def resolve(ident: str) -> dict:
    """Resolve a slug, display name or alias to its registry entry.

    Case-insensitive, surrounding whitespace tolerated. Raises ValueError
    (listing the known volcanoes) for anything unknown — a typo on a CLI
    should fail loudly, never silently fall back to the wrong vent.
    """
    key = str(ident).strip().lower()
    for entry in VOLCANOES.values():
        if key == entry["slug"] or key == entry["name"].lower():
            return entry
        if key in (a.lower() for a in entry.get("aliases", [])):
            return entry
    known = ", ".join(e["slug"] for e in VOLCANOES.values())
    raise ValueError(f"unknown volcano {ident!r} — known: {known}")


def data_dir(site_dir: str, slug: str) -> str:
    """site/data/<slug>/ — namespaced per-volcano website data."""
    import os
    return os.path.join(site_dir, "data", slug)


def archive_dir(repo_dir: str, slug: str) -> str:
    """archive/<slug>/ — namespaced per-volcano 6-hourly archive."""
    import os
    return os.path.join(repo_dir, "archive", slug)
