# Parking Map

Interactive web application for visualizing street-level parking rules in Boston and surrounding communities, starting with Somerville, Medford, and Cambridge, MA.

The long-term goal is to answer a practical curbside question: for a specific street or block, can a driver legally park there, and under what conditions? The app should distinguish resident-permit restrictions, meters, time limits, no-parking rules, private streets, and other posted constraints as accurately as the available municipal data allows.

See [docs/PROJECT_GOAL.md](docs/PROJECT_GOAL.md), [docs/CURB_SEGMENT_STRATEGY.md](docs/CURB_SEGMENT_STRATEGY.md), [docs/DATA_PIPELINE.md](docs/DATA_PIPELINE.md), [docs/IMAGERY_SOURCES.md](docs/IMAGERY_SOURCES.md), [docs/SPARK_IMAGE_WORKLOAD.md](docs/SPARK_IMAGE_WORKLOAD.md), [docs/MEDFORD_SOURCES.md](docs/MEDFORD_SOURCES.md), [docs/CAMBRIDGE_SOURCES.md](docs/CAMBRIDGE_SOURCES.md), [docs/BOSTON_SOURCES.md](docs/BOSTON_SOURCES.md), and [docs/ROADMAP.md](docs/ROADMAP.md) for the product scope and implementation direction.

## Current Status

This repo currently implements a Somerville, Medford, and Cambridge prototype:

- Somerville street centerlines are loaded from `data/streets.geojson`.
- Somerville parking rules are derived from the local traffic regulations PDF.
- Medford street geometry is loaded from MassGIS/MassDOT Roads.
- Medford resident-permit rules are derived from the official resident permit street PDF.
- Cambridge street geometry, metered parking spaces, and public accessible parking spaces are loaded from Cambridge GIS.
- Cambridge streets render as a neutral network because curb-level rules are not yet mapped. Official meter polygons and accessible-space points are enabled as separate evidence layers by default and nearest-matched only for sidebar context, never whole-street status.
- Matching is street-name based, not exact block-segment based.
- Boston and other surrounding communities are target future coverage areas.

## Features

- Interactive map with Somerville, Medford, and Cambridge streets
- Search streets by name
- Click streets to see details, including current parking-rule classification
- Source, confidence, match level, and data-generation provenance in street details
- Lazy-loaded, independently toggleable parking evidence layers
- Dark theme with modern UI
- Statistics sidebar

## Setup

```bash
# Install dependencies and run
uv sync
uv run python scripts/fetch_public_sources.py --municipality somerville
uv run python build_parking_rules.py
uv run python build_parking_coverage.py
uv run python -m parkingmap
```

Then open http://localhost:8000 in your browser.

To fetch raw public source files for Medford:

```bash
python3 scripts/fetch_public_sources.py --municipality medford
python3 build_medford_streets.py
python3 build_medford_rules_seed.py
```

The Medford seed output keeps restriction rows separate and flags partial block/range rules so they are not accidentally treated as whole-street rules.

To rebuild Cambridge GIS-derived data:

```bash
python3 build_cambridge_data.py
```

The Cambridge output keeps meter polygons as their own evidence layer plus approximate nearest-street summaries. Each meter feature retains its nearest-centerline distance and threshold for review. A street with one matched meter is not treated as fully metered.

To start indexing Mapillary imagery metadata:

```bash
export MAPILLARY_ACCESS_TOKEN="..."
python3 scripts/build_imagery_index.py --provider mapillary --municipality somerville --limit 25 --radius-meters 35 --photos-per-sample 2
```

See [docs/IMAGERY_SOURCES.md](docs/IMAGERY_SOURCES.md) and [docs/SPARK_IMAGE_WORKLOAD.md](docs/SPARK_IMAGE_WORKLOAD.md) for source notes and the image-analysis pipeline direction.

Without `uv`:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python build_parking_rules.py
python build_parking_coverage.py
python -m parkingmap
```

## Coolify

- Build command: `pip install -r requirements.txt`
- Post-build command (or pre-start): `python build_parking_rules.py && python build_parking_coverage.py`
- Start command: `python -m parkingmap`
- The app binds to `0.0.0.0` and reads `PORT` from the environment.
- `requirements.txt` is exported from `uv.lock`; update it with `uv export --frozen --no-dev --no-emit-project --output-file requirements.txt` after dependency changes.

## Testing

```bash
uv run pytest -q
node --check static/js/main.js
```

## Data Sources

- **Somerville Street Centerlines**: City of Somerville GIS via [data.somervillema.gov](https://data.somervillema.gov)
- **Somerville Parking Regulations**: [Traffic Commission Regulations PDF](https://s3.amazonaws.com/somervillema-live/s3fs-public/traffic-commission-rules-regulations.pdf)
  - Schedule E: Permit Parking streets
  - Schedule D: Parking prohibitions
  - Schedule F: Metered parking zones
- **Cambridge GIS Street Centerlines**: [Cambridge GIS GitHub](https://github.com/cambridgegis/cambridgegis_data/tree/main/Trans/Street_Centerlines)
- **Cambridge GIS Metered Parking Spaces**: [Cambridge GIS GitHub](https://github.com/cambridgegis/cambridgegis_data/tree/main/Traffic/Metered_Parking_Spaces)
- **Cambridge GIS Public Handicap Parking Spaces**: [Cambridge GIS GitHub](https://github.com/cambridgegis/cambridgegis_data/tree/main/Traffic/Public_Handicap_Parking_Spaces)

## Parking Classification Logic

- `build_parking_rules.py` parses Schedule D/F from the city PDF and writes `data/parking_rules_by_street.json`.
- `scripts/fetch_public_sources.py` validates declared PDFs/JSON before replacement and records resolved URL, HTTP validators, byte count, and SHA-256 metadata.
- `build_parking_coverage.py` combines loaded city sources into evidence-backed coverage summaries and Medford segment-level matches.
- Public streets default to **Resident Permit Required** (Schedule E was removed citywide in 2010).
- If a public street has Schedule F rows, it is labeled **Permit Street with Metered Segments**.
- If a public street has Schedule D time-limited rows, it is labeled **Permit Street with Time-Limited Segments**.
- Private streets are labeled **Private Street Rules Apply**.
- Medford sources are tracked in `data/source_manifest.json`; `build_medford_rules_seed.py` emits row-level resident-permit records under `data/processed/medford/`.
- Cambridge sources are tracked in `data/source_manifest.json`; `build_cambridge_data.py` emits normalized street geometry and parking summaries under `data/processed/cambridge/`.

Current limitation:
- Matching is by street name, not exact block segment geometry from regulation tables. This can over-include streets with only partial exceptions.
- Medford partial resident-permit rows are matched to street segments only when their endpoints match MassDOT `FROM_STREET`/`TO_STREET` values; other segment-specific rows remain flagged for confirmation.

## Project Structure

```
parkingmap/
├── app.py              # FastAPI application
├── build_parking_rules.py # Build structured rules from the city PDF
├── build_parking_coverage.py # Build evidence-backed coverage summaries
├── build_medford_rules_seed.py # Build first-pass Medford row-level permit rules
├── build_medford_streets.py # Download Medford street geometry from MassGIS/MassDOT
├── build_cambridge_data.py # Download Cambridge GIS streets and parking summaries
├── scripts/
│   └── fetch_public_sources.py # Download official source files from manifest
│   └── build_imagery_index.py # Build Mapillary/KartaView imagery metadata index
├── parkingmap.py       # Module entrypoint for `python -m parkingmap`
├── requirements.txt    # Pip dependencies (for non-uv deploys)
├── pyproject.toml      # Project metadata and dependencies
├── uv.lock             # Locked dependency versions
├── docs/
│   ├── PROJECT_GOAL.md # Product scope and accuracy principles
│   ├── CURB_SEGMENT_STRATEGY.md # Foot-by-foot curb accuracy plan
│   ├── DATA_PIPELINE.md # Data ingestion and rule-model direction
│   ├── IMAGERY_SOURCES.md # Street-level imagery provider setup
│   ├── SPARK_IMAGE_WORKLOAD.md # DGX Spark image inference plan
│   ├── MEDFORD_SOURCES.md # Official Medford source inventory
│   ├── CAMBRIDGE_SOURCES.md # Official Cambridge source inventory
│   └── ROADMAP.md      # Multi-city implementation phases
├── data/
│   └── source_manifest.json # Public source registry and download targets
│   └── streets.geojson # Somerville street data
│   └── parking_rules_by_street.json # Derived per-street parking rules
│   └── raw/          # Downloaded public source files
│   └── processed/    # Derived source-specific datasets
├── templates/
│   └── index.html      # Main HTML template
└── static/
    ├── css/
    │   └── style.css   # Styles
    └── js/
        └── main.js     # Map functionality
```

## API Endpoints

- `GET /` - Main map page
- `GET /api/version` - Running application version
- `GET /api/health` - Lightweight deployment health check
- `GET /api/streets` - All streets as GeoJSON
- `GET /api/parking-evidence/cambridge/meters` - Cambridge metered-space evidence GeoJSON
- `GET /api/parking-evidence/cambridge/accessible` - Cambridge public accessible-space evidence GeoJSON
- `GET /api/parking-evidence/imagery` - Street-level imagery metadata GeoJSON
- `GET /api/parking-evidence/imagery/detections` - Image-derived parking evidence GeoJSON
- `GET /api/parking-evidence/imagery/reference-signs` - Mapillary parking-sign reference features
- `GET /api/parking-evidence/imagery/reference-matches` - Image detections matched to parking-sign reference features
- `GET /api/streets/search?q=<query>` - Search streets by name
- `GET /api/stats` - Street statistics

## Future Enhancements

- [ ] Add multi-municipality data organization for Boston, Cambridge, Somerville, Medford, and nearby towns
- [ ] Replace street-name matching with block-segment matching wherever regulation source data supports it
- [ ] Add meter, resident-permit, time-limit, street-cleaning, loading, handicap, and no-parking rule layers
- [x] Show available source dates and confidence for rules in the UI
- [ ] Add user-facing filters for parking type, time window, and permit requirements
