# Cambridge Sources

This inventory tracks the first Cambridge public sources wired into the app.

## Implemented Sources

- Street centerlines: Cambridge GIS `TRANS_Centerlines.geojson`
- Metered parking spaces: Cambridge GIS `TRAFFIC_MeteredParkingSpaces.geojson`
- Public accessible parking spaces: Cambridge GIS `TRAFFIC_PublicHandicapParkingSpaces.geojson`

The source URLs are recorded in `data/source_manifest.json` and in the generated Cambridge output files.

## Builder

Run:

```bash
python3 build_cambridge_data.py
```

Outputs:

- `data/processed/cambridge/streets.geojson`
- `data/processed/cambridge/parking_rules.json`
- `data/processed/cambridge/metered_spaces.geojson`
- `data/processed/cambridge/accessible_spaces.geojson`

## Current Extraction

`build_cambridge_data.py` normalizes Cambridge street centerlines into the app street schema. It also preserves Cambridge meter polygons and accessible-space points as map evidence layers. Metered-space polygons are matched to the nearest street centerline and summarized by normalized street name for search/detail context.

Accessible spaces are counted by their source `StreetName` field.

## Accuracy Notes

The Cambridge meter layer is stronger than a pure street-name regulation table because each meter is spatial data. It is still not a final curb segment model:

- Meter polygons are nearest-matched to street centerlines, not snapped to a curb side.
- The current output summarizes by street name.
- A single matched meter must not classify a whole street as metered.
- Use `meter_match_confidence: nearest_street_approx` and the meter counts as segment evidence until curb-side geometry exists.

Future Cambridge work should add resident-permit, street-cleaning, loading-zone, no-parking, and sign-regulation sources.
