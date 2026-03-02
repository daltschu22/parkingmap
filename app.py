"""
Somerville Parking Map - Interactive street parking visualization
"""
import json
import os
import re
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app = FastAPI(title="Somerville Parking Map")
APP_VERSION = "2026-03-02-parking-access-v6"

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# Cache for street data
_streets_cache = None
_parking_rules_cache = None
PARKING_RULES_PATH = DATA_DIR / "parking_rules_by_street.json"


def _normalize_street_name(value: str | None) -> str:
    """Normalize street names for loose matching across GIS and PDF text."""
    if not value:
        return ""

    text = re.sub(r"[^A-Za-z0-9 ]+", " ", value.upper())
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""

    token_map = {
        "STREET": "ST",
        "ST": "ST",
        "AVENUE": "AVE",
        "AVE": "AVE",
        "ROAD": "RD",
        "RD": "RD",
        "DRIVE": "DR",
        "DR": "DR",
        "PLACE": "PL",
        "PL": "PL",
        "COURT": "CT",
        "CT": "CT",
        "TERRACE": "TER",
        "TER": "TER",
        "PARKWAY": "PKWY",
        "PKWY": "PKWY",
        "SQUARE": "SQ",
        "SQ": "SQ",
        "HIGHWAY": "HWY",
        "HWY": "HWY",
        "LANE": "LN",
        "LN": "LN",
        "BOULEVARD": "BLVD",
        "BLVD": "BLVD",
        "CIRCLE": "CIR",
        "CIR": "CIR",
    }
    tokens = [token_map.get(token, token) for token in text.split()]
    return " ".join(tokens)


def _classify_parking_access(properties: dict, rules: dict) -> tuple[str, str]:
    ownership_raw = properties.get("OWNERSHIP")
    ownership = str(ownership_raw or "").strip().lower()
    street_name = _normalize_street_name(properties.get("STNAME"))
    street_rule = rules.get(street_name, {})
    has_metered_segment = bool(street_rule.get("has_metered_segment"))
    has_time_limited_segment = bool(street_rule.get("has_time_limited_segment"))
    meter_count = street_rule.get("meter_count_estimate")

    if ownership in {"public", "state land"}:
        if has_metered_segment:
            if meter_count is not None:
                return (
                    "permit_with_metered_segments",
                    f"Resident permit required by default; this street has metered segments (~{meter_count} meter spaces listed).",
                )
            return (
                "permit_with_metered_segments",
                "Resident permit required by default; this street has metered segments.",
            )
        if has_time_limited_segment:
            return (
                "permit_with_time_limited_segments",
                "Resident permit required by default; this street has time-limited parking segments.",
            )
        return "resident_permit_required", "Resident permit required unless otherwise posted."

    if ownership == "private":
        return "private_rules_apply", "Private street; parking rules are set by owner/signage."
    return "unknown", "Parking access could not be determined from available data."


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


def load_parking_rules():
    """Load and cache per-street rules derived from Schedule D/F parsing."""
    global _parking_rules_cache
    if _parking_rules_cache is None:
        if PARKING_RULES_PATH.exists():
            content = json.loads(PARKING_RULES_PATH.read_text())
            _parking_rules_cache = content.get("streets", {})
        else:
            _parking_rules_cache = {}
    return _parking_rules_cache


def get_enriched_streets():
    streets = load_streets()
    rules = load_parking_rules()

    features = []
    for feature in streets.get("features", []):
        props = dict(feature.get("properties", {}))
        street_key = _normalize_street_name(props.get("STNAME"))
        rule = rules.get(street_key, {})
        category, note = _classify_parking_access(props, rules)
        props["PARKING_ACCESS"] = category
        props["PARKING_NOTE"] = note
        props["PARKING_METER_COUNT_ESTIMATE"] = rule.get("meter_count_estimate")
        props["PARKING_METER_COUNT_CONFIDENCE"] = rule.get("meter_count_confidence", "none")
        props["PARKING_HAS_METERED_SEGMENT"] = bool(rule.get("has_metered_segment"))
        props["PARKING_HAS_TIME_LIMITED_SEGMENT"] = bool(rule.get("has_time_limited_segment"))
        updated_feature = dict(feature)
        updated_feature["properties"] = props
        features.append(updated_feature)

    return {
        "type": "FeatureCollection",
        "features": features,
    }


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Render the main map page."""
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "app_version": APP_VERSION},
    )


@app.get("/api/version")
async def get_version():
    """Return running application version for deployment verification."""
    return JSONResponse(content={"app_version": APP_VERSION})


@app.get("/api/streets")
async def get_streets():
    """Return all streets as GeoJSON."""
    return JSONResponse(content=get_enriched_streets())


@app.get("/api/streets/search")
async def search_streets(q: str = ""):
    """Search streets by name."""
    streets = get_enriched_streets()
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
    streets = get_enriched_streets()
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
    
    parking_access_counts = {}

    for f in features:
        props = f.get("properties", {})
        access = props.get("PARKING_ACCESS", "unknown")
        parking_access_counts[access] = parking_access_counts.get(access, 0) + 1

    return JSONResponse(content={
        "total_segments": len(features),
        "unique_streets": len(street_names),
        "ownership": ownership_counts,
        "functional_class": func_class_counts,
        "parking_access": parking_access_counts,
    })


def main():
    """Run the development server."""
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
