# agent.md — how to consume this dashboard programmatically

Unofficial community mirror of official volcanic-hazard data for
Gunung Anak Krakatau (-6.102, 105.423). Not a warning system.

## Endpoints

### GET data/snapshot.json   (schema_version: 1)

```jsonc
{
  "schema_version": 1,
  "generated_utc": "2026-09-08T17:33:00Z",   // when CI rebuilt the data
  "generated_wib": "09 Sep 2026, 00:33 WIB", // pre-formatted, UTC+7
  "volcano":  { "name", "code", "province", "lat", "lon" },
  "status":   { "level": 3, "level_name": "Level III (Siaga)",
                "source": "MAGMA Indonesia / PVMBG",
                "source_url": "...", "fetched_utc": "...", "fetched_wib": "...",
                "indonesia_counts": { "Normal": 42, "Waspada": 22, "Siaga": 5, "Awas": 0 } },
  "report":   { "period", "author", "visual", "climate",
                "seismic_counts": { "Hybrid/Fase Banyak": 53, "...": 0 },
                "recommendation", "seismogram_asset": "assets/seismogram.png" },
  "eruptions":[ { "time_label_wib", "utc", "wib", "text", "url" } ],
  "vona":     [ { "code": "ORANGE|RED|YELLOW|GREEN", "issued_utc", "wib", "text", "url" } ],
  "vaac":     {
    "state": "advisory|nil|stale|error",
    "advisory_nr": "2026/202", "dtg_utc", "dtg_wib", "age_hours", "is_current",
    "eruption_details": "VA EMISSION TO FL070 OBS AT ... MOV S",
    "observed_layers": [ { "base": "SFC", "top": "FL070", "top_km": 2.13,
                           "top_human_id": "FL070 = 7.000 ft ≈ 2,1 km di atas permukaan laut",
                           "top_human_en": "FL070 = 7,000 ft ≈ 2.1 km above sea level",
                           "move_toward": "S", "speed_kt": 10, "speed_ms": 5.1,
                           "polygon": [ [lon, lat], ... ] } ],
    "forecasts": { "+6h": { "valid_utc", "valid_wib", "layers": [ ... ] }, "+12h": {}, "+18h": {} },
    "remarks": "...", "next_advisory_by_utc", "graphic_asset": "assets/vaac_graphic.png",
    "bulletin_text": "FVAU04 ADRM ... (raw WMO bulletin)",
    "source_url": "...", "data_url": "...", "via": "POST .../process.php"
  },
  "satellite": { "snpp": { "date", "asset", "credit", "bbox", "zoom" }, "aqua": { ... } },
  "loop":     { "band": "ir|vis", "band_label", "interval_min": 10,
                "frames": [ { "t_utc", "t_wib", "asset" } x12 ],   // 2 h of motion
                "roi": [lon0, lon1, lat0, lat1], "credit" },
  "official_links": { "magma", "pvmbg", "bnpb", "bpbd_lampung", "bpbd_banten", "darwin_vaac" }
}
```

### GET data/forecast_model.json   (SECONDARY — human-gated)

Absent (HTTP 404) unless a human approved it. Fields: `status:"approved"`,
`approved_by`, `approved_utc`, `computed_utc`, `validation{hard_failures,
direction_agreement, occurrence_sources, content_hash}`, `layers[]` with
`alt_km`, `toward_deg`, `toward_compass`, `speed_ms`, `consistency_R`,
`n_vectors`, `nearest_vector_km`, `confidence`, `trajectory[[lat,lon,hours],...]`.

Always reproduce its disclosure verbatim if you quote it:
"computed automatically from Himawari-9 and open-meteo — can be inaccurate;
refer to MAGMA/PVMBG, BNPB/BPBD and Darwin VAAC."

## Rules for agents

1. Cite the agency, not this site, for hazard values.
2. `vaac.state != "advisory"` ⇒ say "no current advisory; absence is not an
   all-clear", never "no ash".
3. `move_toward` is direction of travel TOWARD; convert FL with the
   `*_human_*` strings.
4. Loop frames are JPEGs in `assets/loop/`; timestamps are 10 min apart. If you
   analyse them, cite NASA GIBS/JMA Himawari-9 and note the band (ir or vis).
5. Politeness: this is a static site; caching for >= 5 min is fine and kind.
   Do NOT scrape magma.esdm.go.id or bom.gov.au more often than every 15 min.
5. If `generated_utc` is older than 2 h, state that the mirror may be stale.
