# Somerville Parking Map

Interactive web application for visualizing Somerville, MA street data with parking information.

## Features

- Interactive map with all Somerville streets
- Search streets by name
- Click streets to see details (ownership, road class, material, etc.)
- Dark theme with modern UI
- Statistics sidebar

## Setup

```bash
# Install dependencies and run
uv sync
uv run parkingmap
```

Then open http://localhost:8000 in your browser.

Without `uv`:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m parkingmap
```

## Coolify

- Build command: `pip install -r requirements.txt`
- Start command: `python -m parkingmap`
- The app binds to `0.0.0.0` and reads `PORT` from the environment.

## Data Sources

- **Street Centerlines**: City of Somerville GIS via [data.somervillema.gov](https://data.somervillema.gov)
- **Parking Regulations**: [Traffic Commission Regulations PDF](https://s3.amazonaws.com/somervillema-live/s3fs-public/traffic-commission-regulations_1.pdf)
  - Schedule E: Permit Parking streets
  - Schedule D: Parking prohibitions
  - Schedule F: Metered parking zones

## Project Structure

```
parkingmap/
├── app.py              # FastAPI application
├── parkingmap.py       # Module entrypoint for `python -m parkingmap`
├── requirements.txt    # Pip dependencies (for non-uv deploys)
├── pyproject.toml      # Project metadata and dependencies
├── uv.lock             # Locked dependency versions
├── data/
│   └── streets.geojson # Somerville street data
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
- `GET /api/streets` - All streets as GeoJSON
- `GET /api/streets/search?q=<query>` - Search streets by name
- `GET /api/stats` - Street statistics

## Future Enhancements

- [ ] Parse permit parking data from Traffic Commission PDF
- [ ] Color-code streets by permit zone
- [ ] Add metered parking locations
- [ ] Add parking prohibition overlays
- [ ] Filter by road ownership (public/private)
