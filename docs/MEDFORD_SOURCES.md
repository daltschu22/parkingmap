# Medford Sources

Official Medford parking sources are tracked in `data/source_manifest.json` and can be fetched with:

```bash
python3 scripts/fetch_public_sources.py --municipality medford
```

## Current Raw Sources

- Parking Department page: `data/raw/medford/parking_department.html`
- Resident Permit Parking Streets PDF: `data/raw/medford/resident-permit-parking-streets.pdf`
- Public roads and private ways PDF: `data/raw/medford/public-roads-private-ways.pdf`
- Commercial overnight parking rule image: `data/raw/medford/commercial-overnight-parking-prohibited.png`
- Green Line Zone page: `data/raw/medford/green-line-zone.html`
- Green Line Zone map: `data/raw/medford/green-line-zone-map.png`
- Snow policy page: `data/raw/medford/snow-policies.html`
- Private-way parking page: `data/raw/medford/parking-on-private-ways.html`
- Business, municipal, and commuter parking maps: `data/raw/medford/maps/`
- MassGIS/MassDOT Roads FeatureServer extract: `data/processed/medford/streets.geojson`

## Derived Data

`build_medford_rules_seed.py` currently emits:

- `data/processed/medford/resident_permit_parking_text.txt`
- `data/processed/medford/resident_permit_parking_rules.json`

`build_medford_streets.py` emits:

- `data/processed/medford/streets.geojson`

Street ownership in this output is only an explicit MassDOT attribute inference. Each segment carries `OWNERSHIP_SOURCE` and `OWNERSHIP_CONFIDENCE`; unresolved attributes remain `Unknown`. The official public/private ways PDF has not been georegistered and is not silently promoted to segment ownership.

The JSON output preserves resident-permit rows and marks rows as:

- `likely_full_street`
- `street_level_time_window`
- `partial_or_segment_specific`

Rows marked `partial_or_segment_specific` must not be painted as whole-street parking status.

## Next Medford Work

- Parse public/private ways into a street ownership dataset.
- Review Traffic Commission decisions as a dated rule-change log.
- Georeference or digitize the G Zone map.
- Extract pay-to-park and business-permit spans from the Medford parking maps.
- Match resident-permit rows to curb spans using address ranges, cross streets, side-of-street phrases, and distance text.
