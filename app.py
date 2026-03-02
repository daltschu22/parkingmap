"""
Somerville Parking Map - Interactive street parking visualization
"""
import json
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app = FastAPI(title="Somerville Parking Map")

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# Cache for street data
_streets_cache = None


def load_streets():
    """Load and cache the streets GeoJSON data."""
    global _streets_cache
    if _streets_cache is None:
        streets_file = DATA_DIR / "streets.geojson"
        if streets_file.exists():
            with open(streets_file) as f:
                _streets_cache = json.load(f)
        else:
            _streets_cache = {"type": "FeatureCollection", "features": []}
    return _streets_cache


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Render the main map page."""
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/streets")
async def get_streets():
    """Return all streets as GeoJSON."""
    return JSONResponse(content=load_streets())


@app.get("/api/streets/search")
async def search_streets(q: str = ""):
    """Search streets by name."""
    streets = load_streets()
    if not q:
        return JSONResponse(content=streets)
    
    q_lower = q.lower()
    filtered_features = [
        f for f in streets.get("features", [])
        if q_lower in (f.get("properties", {}).get("STNAME", "") or "").lower()
    ]
    
    return JSONResponse(content={
        "type": "FeatureCollection",
        "features": filtered_features
    })


@app.get("/api/stats")
async def get_stats():
    """Return statistics about the street data."""
    streets = load_streets()
    features = streets.get("features", [])
    
    # Count unique street names
    street_names = set()
    ownership_counts = {}
    func_class_counts = {}
    
    for f in features:
        props = f.get("properties", {})
        name = props.get("STNAME")
        if name:
            street_names.add(name)
        
        ownership = props.get("OWNERSHIP", "Unknown")
        ownership_counts[ownership] = ownership_counts.get(ownership, 0) + 1
        
        func_class = props.get("FUNC_CLASS", "Unknown")
        func_class_counts[func_class] = func_class_counts.get(func_class, 0) + 1
    
    return JSONResponse(content={
        "total_segments": len(features),
        "unique_streets": len(street_names),
        "ownership": ownership_counts,
        "functional_class": func_class_counts
    })


def main():
    """Run the development server."""
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
