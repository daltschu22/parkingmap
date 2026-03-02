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

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# Cache for street data
_streets_cache = None
_parking_rules_cache = None


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


def _find_header_index(lines: list[str], header: str) -> int:
    pattern = re.compile(rf"^\s*{re.escape(header)}\s*$", re.IGNORECASE)
    for i, line in enumerate(lines):
        if pattern.match(line):
            return i
    return -1


def _extract_schedule_lines(start_header: str, end_header: str) -> list[str]:
    text_file = DATA_DIR / "pdf_text.txt"
    if not text_file.exists():
        return []

    lines = text_file.read_text(errors="ignore").splitlines()
    start = _find_header_index(lines, start_header)
    end = _find_header_index(lines, end_header) if end_header else -1
    if start == -1:
        return []
    if end == -1 or end <= start:
        end = len(lines)
    return [line.strip() for line in lines[start + 1:end] if line.strip()]


def _line_is_likely_limited_parking(line: str) -> bool:
    upper = line.upper()
    if re.search(r"\b\d+\s*(HR|HOUR|MIN|MINUTE)\b", upper):
        return True
    if "2HR" in upper or "2 HOUR" in upper or "15 MINUTE" in upper:
        return True
    return False


def _build_parking_rules(streets_geojson: dict) -> dict:
    street_names = {
        _normalize_street_name((f.get("properties", {}).get("STNAME") or "").strip())
        for f in streets_geojson.get("features", [])
    }
    street_names.discard("")
    candidates = sorted(street_names, key=len, reverse=True)

    schedule_d_lines = _extract_schedule_lines("Schedule D", "Schedule E")
    schedule_f_lines = _extract_schedule_lines("Schedule F", "Schedule G")

    metered_no_pass = set()
    limited_no_pass = set()

    def find_street_prefix(line: str) -> str | None:
        normalized_line = _normalize_street_name(line)
        for name in candidates:
            if normalized_line == name or normalized_line.startswith(f"{name} "):
                return name
        return None

    for line in schedule_f_lines:
        name = find_street_prefix(line)
        if name:
            metered_no_pass.add(name)

    for line in schedule_d_lines:
        name = find_street_prefix(line)
        if not name:
            continue
        if _line_is_likely_limited_parking(line):
            limited_no_pass.add(name)

    return {
        "metered_no_pass": metered_no_pass,
        "limited_no_pass": limited_no_pass,
    }


def _classify_parking_access(properties: dict, rules: dict) -> tuple[str, str]:
    ownership_raw = properties.get("OWNERSHIP")
    ownership = str(ownership_raw or "").strip().lower()
    street_name = _normalize_street_name(properties.get("STNAME"))

    if street_name and street_name in rules["metered_no_pass"]:
        return "metered_no_pass", "Metered parking available (resident pass not required)."
    if street_name and street_name in rules["limited_no_pass"]:
        return "time_limited_no_pass", "Time-limited parking available (resident pass not required)."

    if ownership == "private":
        return "private_rules_apply", "Private street; parking rules are set by owner/signage."
    if ownership in {"public", "state land"}:
        return "resident_permit_required", "Resident permit required unless otherwise posted."
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
    """Build and cache rules derived from Schedule D/F text."""
    global _parking_rules_cache
    if _parking_rules_cache is None:
        _parking_rules_cache = _build_parking_rules(load_streets())
    return _parking_rules_cache


def get_enriched_streets():
    streets = load_streets()
    rules = load_parking_rules()

    features = []
    for feature in streets.get("features", []):
        props = dict(feature.get("properties", {}))
        category, note = _classify_parking_access(props, rules)
        props["PARKING_ACCESS"] = category
        props["PARKING_NOTE"] = note
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
    return templates.TemplateResponse("index.html", {"request": request})


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
