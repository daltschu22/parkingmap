"""
Parking Map - Interactive street parking-rule visualization.
"""

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app = FastAPI(title="Parking Map")
app.add_middleware(GZipMiddleware, minimum_size=1_000, compresslevel=6)
APP_VERSION = "2026-07-21-marker-key-v4"

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# Cache for street data
_streets_cache = None
_parking_rules_cache = None
_medford_streets_cache = None
_medford_rules_cache = None
_medford_segment_rules_cache = None
_cambridge_streets_cache = None
_cambridge_rules_cache = None
_cambridge_meter_spaces_cache = None
_cambridge_accessible_spaces_cache = None
_imagery_index_cache = None
_imagery_detection_cache = None
_imagery_reference_signs_cache = None
_imagery_reference_matches_cache = None
_enriched_streets_cache = None
_stats_cache = None
PARKING_RULES_PATH = DATA_DIR / "parking_rules_by_street.json"
MEDFORD_STREETS_PATH = DATA_DIR / "processed" / "medford" / "streets.geojson"
MEDFORD_RULES_PATH = (
    DATA_DIR / "processed" / "medford" / "resident_permit_parking_rules.json"
)
MEDFORD_SEGMENT_RULES_PATH = (
    DATA_DIR / "processed" / "medford" / "segment_parking_evidence.json"
)
CAMBRIDGE_STREETS_PATH = DATA_DIR / "processed" / "cambridge" / "streets.geojson"
CAMBRIDGE_RULES_PATH = DATA_DIR / "processed" / "cambridge" / "parking_rules.json"
CAMBRIDGE_METER_SPACES_PATH = (
    DATA_DIR / "processed" / "cambridge" / "metered_spaces.geojson"
)
CAMBRIDGE_ACCESSIBLE_SPACES_PATH = (
    DATA_DIR / "processed" / "cambridge" / "accessible_spaces.geojson"
)
IMAGERY_INDEX_PATH = DATA_DIR / "processed" / "imagery" / "street_imagery_index.geojson"
IMAGERY_DETECTIONS_PATH = (
    DATA_DIR / "processed" / "imagery" / "parking_image_evidence.geojson"
)
IMAGERY_REFERENCE_SIGNS_PATH = (
    DATA_DIR / "processed" / "imagery" / "mapillary_parking_sign_features.geojson"
)
IMAGERY_REFERENCE_MATCHES_PATH = (
    DATA_DIR / "processed" / "imagery" / "reference_matched_detections.geojson"
)
SOMERVILLE_RULE_SOURCE_URL = "https://s3.amazonaws.com/somervillema-live/s3fs-public/traffic-commission-rules-regulations.pdf"
MEDFORD_RULE_SOURCE_URL = "https://www.medfordma.org/fs/resource-manager/view/c2132e77-d61f-40b1-9aa8-1add88772e3d"
MEDFORD_STREET_SOURCE_URL = (
    "https://services1.arcgis.com/hGdibHYSPO59RG1h/arcgis/rest/services/"
    "MassDOTRoads_gdb/FeatureServer/0"
)
CAMBRIDGE_PARKING_SOURCE_URL = (
    "https://github.com/cambridgegis/cambridgegis_data/tree/main/Traffic"
)


@app.middleware("http")
async def add_response_headers(request: Request, call_next):
    """Add baseline browser security and cache headers."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' https://unpkg.com; "
        "style-src 'self' 'unsafe-inline' https://unpkg.com; "
        "img-src 'self' data: blob: https://unpkg.com https://*.basemaps.cartocdn.com "
        "https://server.arcgisonline.com; "
        "connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
    )
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = (
            "public, max-age=300, stale-while-revalidate=3600"
        )
    return response


def _json_response(content: object) -> JSONResponse:
    return JSONResponse(content=content)


@lru_cache(maxsize=None)
def _generated_at(path: Path) -> str:
    """Read a generated timestamp once for source provenance in the UI."""
    if not path.exists():
        return ""
    content = json.loads(path.read_text())
    return str(
        content.get("generated_at_utc")
        or (content.get("properties") or {}).get("generated_at_utc")
        or ""
    )


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
        return (
            "resident_permit_required",
            "Resident permit required unless otherwise posted.",
        )

    if ownership == "private":
        return (
            "private_rules_apply",
            "Private street; parking rules are set by owner/signage.",
        )
    return "unknown", "Parking access could not be determined from available data."


def _classify_medford_parking_access(properties: dict, rules: dict) -> tuple[str, str]:
    ownership = str(properties.get("OWNERSHIP") or "").strip().lower()
    if ownership == "private":
        return (
            "private_rules_apply",
            "Private street; parking rules are set by owner/signage.",
        )

    street_key = _normalize_street_name(properties.get("STNAME"))
    rows = rules.get(street_key, [])
    if not rows:
        return (
            "unknown",
            "No Medford resident-permit row matched this street in the current seed data; other posted rules may apply.",
        )

    partial_rows = [
        row for row in rows if row.get("scope") == "partial_or_segment_specific"
    ]
    permit_types = sorted(
        {row.get("permit_type") for row in rows if row.get("permit_type")}
    )
    permit_text = ", ".join(permit_types) if permit_types else "Resident"

    if partial_rows:
        return (
            "resident_permit_segment_rules_known",
            (
                f"Medford resident-permit source has {len(rows)} row(s) for this street, "
                f"including {len(partial_rows)} partial/segment-specific row(s). "
                "Do not treat the whole street as one parking status yet."
            ),
        )

    return (
        "resident_permit_required",
        f"Medford resident-permit source lists this as {permit_text} permit parking. Confirm posted signs.",
    )


def _classify_cambridge_parking_access(
    properties: dict, rules: dict
) -> tuple[str, str]:
    ownership = str(properties.get("OWNERSHIP") or "").strip().lower()
    if ownership == "private":
        return (
            "private_rules_apply",
            "Private street; parking rules are set by owner/signage.",
        )

    street_key = _normalize_street_name(properties.get("STNAME"))
    rule = rules.get(street_key, {})
    active_meter_count = int(rule.get("active_meter_count_estimate") or 0)
    total_meter_count = int(rule.get("meter_count_estimate") or 0)
    accessible_count = int(rule.get("accessible_space_count") or 0)

    if active_meter_count > 0:
        return (
            "unknown",
            (
                "Curb-level parking rules are not mapped for this street. Cambridge GIS "
                f"shows {active_meter_count} active meter space(s) nearby; use the exact "
                "meter-space overlay and posted signs rather than treating the whole street as metered."
            ),
        )
    if total_meter_count > 0:
        return (
            "unknown",
            (
                "Curb-level parking rules are not mapped for this street. Cambridge GIS "
                f"shows {total_meter_count} inactive, removed, or proposed meter space(s) "
                "nearby and no active meters; this is historical point evidence only."
            ),
        )
    if accessible_count > 0:
        return (
            "unknown",
            (
                "Curb-level parking rules are not mapped for this street. Cambridge GIS "
                f"lists {accessible_count} public accessible parking space(s) nearby; use "
                "the exact accessible-space overlay and posted signs."
            ),
        )
    return (
        "unknown",
        "Curb-level parking rules are not mapped for this Cambridge street. No meter or "
        "accessible-space record matched it; posted rules may still apply.",
    )


def _cambridge_evidence_category(rule: dict) -> str:
    active_meter_count = int(rule.get("active_meter_count_estimate") or 0)
    total_meter_count = int(rule.get("meter_count_estimate") or 0)
    accessible_count = int(rule.get("accessible_space_count") or 0)
    if active_meter_count > 0:
        return "active_meter_spaces_nearby"
    if total_meter_count > 0:
        return "inactive_meter_spaces_nearby"
    if accessible_count > 0:
        return "accessible_spaces_nearby"
    return "none"


def _parking_display_status(access: str) -> str:
    """Collapse internal evidence categories into four driver-facing map states."""
    if access == "permit_with_metered_segments":
        return "metered"
    if access == "permit_with_time_limited_segments":
        return "open_time_limited"
    if access in {
        "resident_permit_required",
        "resident_permit_segment_rules_known",
        "private_rules_apply",
    }:
        return "restricted"
    return "unknown"


def _format_count_values(values: list[dict], limit: int = 3) -> str:
    parts = []
    for item in values[:limit]:
        value = item.get("value")
        count = item.get("count")
        if value and count:
            parts.append(f"{value} ({count})")
    return " | ".join(parts)


def _format_distance_summary(summary: dict | None) -> str:
    if not summary:
        return ""
    median = summary.get("median")
    p95 = summary.get("p95")
    maximum = summary.get("max")
    if median is None or p95 is None or maximum is None:
        return ""
    return f"median {median} m | p95 {p95} m | max {maximum} m"


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


def load_medford_streets():
    """Load and cache Medford street GeoJSON data."""
    global _medford_streets_cache
    if _medford_streets_cache is None:
        if MEDFORD_STREETS_PATH.exists():
            _medford_streets_cache = json.loads(MEDFORD_STREETS_PATH.read_text())
        else:
            _medford_streets_cache = {"type": "FeatureCollection", "features": []}
    return _medford_streets_cache


def load_cambridge_streets():
    """Load and cache Cambridge street GeoJSON data."""
    global _cambridge_streets_cache
    if _cambridge_streets_cache is None:
        if CAMBRIDGE_STREETS_PATH.exists():
            _cambridge_streets_cache = json.loads(CAMBRIDGE_STREETS_PATH.read_text())
        else:
            _cambridge_streets_cache = {"type": "FeatureCollection", "features": []}
    return _cambridge_streets_cache


def load_cambridge_meter_spaces():
    """Load and cache Cambridge metered-space evidence geometry."""
    global _cambridge_meter_spaces_cache
    if _cambridge_meter_spaces_cache is None:
        if CAMBRIDGE_METER_SPACES_PATH.exists():
            _cambridge_meter_spaces_cache = json.loads(
                CAMBRIDGE_METER_SPACES_PATH.read_text()
            )
        else:
            _cambridge_meter_spaces_cache = {
                "type": "FeatureCollection",
                "features": [],
            }
    return _cambridge_meter_spaces_cache


def load_cambridge_accessible_spaces():
    """Load and cache Cambridge accessible-space evidence geometry."""
    global _cambridge_accessible_spaces_cache
    if _cambridge_accessible_spaces_cache is None:
        if CAMBRIDGE_ACCESSIBLE_SPACES_PATH.exists():
            _cambridge_accessible_spaces_cache = json.loads(
                CAMBRIDGE_ACCESSIBLE_SPACES_PATH.read_text()
            )
        else:
            _cambridge_accessible_spaces_cache = {
                "type": "FeatureCollection",
                "features": [],
            }
    return _cambridge_accessible_spaces_cache


def load_imagery_index():
    """Load and cache street-level imagery metadata evidence."""
    global _imagery_index_cache
    if _imagery_index_cache is None:
        if IMAGERY_INDEX_PATH.exists():
            _imagery_index_cache = json.loads(IMAGERY_INDEX_PATH.read_text())
        else:
            _imagery_index_cache = {"type": "FeatureCollection", "features": []}
    return _imagery_index_cache


def load_imagery_detections():
    """Load and cache image-derived parking evidence detections."""
    global _imagery_detection_cache
    if _imagery_detection_cache is None:
        if IMAGERY_DETECTIONS_PATH.exists():
            _imagery_detection_cache = json.loads(IMAGERY_DETECTIONS_PATH.read_text())
        else:
            _imagery_detection_cache = {"type": "FeatureCollection", "features": []}
    return _imagery_detection_cache


def load_imagery_reference_signs():
    """Load and cache Mapillary parking-sign reference features."""
    global _imagery_reference_signs_cache
    if _imagery_reference_signs_cache is None:
        if IMAGERY_REFERENCE_SIGNS_PATH.exists():
            _imagery_reference_signs_cache = json.loads(
                IMAGERY_REFERENCE_SIGNS_PATH.read_text()
            )
        else:
            _imagery_reference_signs_cache = {
                "type": "FeatureCollection",
                "features": [],
            }
    return _imagery_reference_signs_cache


def load_imagery_reference_matches():
    """Load and cache detections matched to Mapillary parking-sign reference features."""
    global _imagery_reference_matches_cache
    if _imagery_reference_matches_cache is None:
        if IMAGERY_REFERENCE_MATCHES_PATH.exists():
            _imagery_reference_matches_cache = json.loads(
                IMAGERY_REFERENCE_MATCHES_PATH.read_text()
            )
        else:
            _imagery_reference_matches_cache = {
                "type": "FeatureCollection",
                "features": [],
            }
    return _imagery_reference_matches_cache


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


def load_medford_rules():
    """Load and cache Medford resident-permit rows keyed by normalized street name."""
    global _medford_rules_cache
    if _medford_rules_cache is None:
        if not MEDFORD_RULES_PATH.exists():
            _medford_rules_cache = {}
        else:
            content = json.loads(MEDFORD_RULES_PATH.read_text())
            rows_by_street = {}
            for row in content.get("rows", []):
                key = _normalize_street_name(row.get("street_name"))
                if key:
                    rows_by_street.setdefault(key, []).append(row)
            _medford_rules_cache = rows_by_street
    return _medford_rules_cache


def load_medford_segment_rules():
    """Load and cache Medford segment-level parking evidence keyed by MassDOT object ID."""
    global _medford_segment_rules_cache
    if _medford_segment_rules_cache is None:
        if not MEDFORD_SEGMENT_RULES_PATH.exists():
            _medford_segment_rules_cache = {}
        else:
            content = json.loads(MEDFORD_SEGMENT_RULES_PATH.read_text())
            _medford_segment_rules_cache = {
                str(key): value for key, value in content.get("segments", {}).items()
            }
    return _medford_segment_rules_cache


def load_cambridge_rules():
    """Load and cache Cambridge parking summaries keyed by normalized street name."""
    global _cambridge_rules_cache
    if _cambridge_rules_cache is None:
        if not CAMBRIDGE_RULES_PATH.exists():
            _cambridge_rules_cache = {}
        else:
            content = json.loads(CAMBRIDGE_RULES_PATH.read_text())
            _cambridge_rules_cache = {
                _normalize_street_name(key): value
                for key, value in content.get("streets", {}).items()
                if _normalize_street_name(key)
            }
    return _cambridge_rules_cache


def get_enriched_streets():
    global _enriched_streets_cache
    if _enriched_streets_cache is not None:
        return _enriched_streets_cache

    streets = load_streets()
    rules = load_parking_rules()

    features = []
    for feature in streets.get("features", []):
        props = dict(feature.get("properties", {}))
        props["MUNICIPALITY"] = "Somerville"
        props["DATA_SOURCE"] = props.get("DATA_SOURCE") or "somerville_streets_geojson"
        street_key = _normalize_street_name(props.get("STNAME"))
        rule = rules.get(street_key, {})
        category, note = _classify_parking_access(props, rules)
        props["PARKING_ACCESS"] = category
        props["PARKING_DISPLAY_STATUS"] = _parking_display_status(category)
        props["PARKING_NOTE"] = note
        props["PARKING_METER_COUNT_ESTIMATE"] = rule.get("meter_count_estimate")
        props["PARKING_METER_COUNT_CONFIDENCE"] = rule.get(
            "meter_count_confidence", "none"
        )
        props["PARKING_HAS_METERED_SEGMENT"] = bool(rule.get("has_metered_segment"))
        props["PARKING_HAS_TIME_LIMITED_SEGMENT"] = bool(
            rule.get("has_time_limited_segment")
        )
        props["PARKING_RULE_SOURCE"] = "Somerville Traffic Commission Regulations"
        props["PARKING_SOURCE_URL"] = SOMERVILLE_RULE_SOURCE_URL
        props["PARKING_CONFIDENCE"] = (
            rule.get("meter_count_confidence")
            if rule.get("has_metered_segment")
            else "street_name_only"
        )
        props["PARKING_DATA_UPDATED_AT"] = _generated_at(PARKING_RULES_PATH)
        updated_feature = dict(feature)
        updated_feature["properties"] = props
        features.append(updated_feature)

    medford_rules = load_medford_rules()
    medford_segment_rules = load_medford_segment_rules()
    medford_streets = load_medford_streets()
    for feature in medford_streets.get("features", []):
        props = dict(feature.get("properties", {}))
        segment_evidence = medford_segment_rules.get(
            str(props.get("MASSDOT_OBJECTID") or "")
        )
        street_key = _normalize_street_name(props.get("STNAME"))
        rows = medford_rules.get(street_key, [])
        partial_rows = [
            row for row in rows if row.get("scope") == "partial_or_segment_specific"
        ]
        if segment_evidence:
            category = segment_evidence.get("parking_access") or "unknown"
            note = segment_evidence.get("parking_note") or ""
            match_level = segment_evidence.get("match_level") or "none"
        else:
            category, note = _classify_medford_parking_access(props, medford_rules)
            match_level = (
                ("street_name_partial_rows" if partial_rows else "street_name")
                if rows
                else "none"
            )
        props["PARKING_ACCESS"] = category
        props["PARKING_DISPLAY_STATUS"] = _parking_display_status(category)
        props["PARKING_NOTE"] = note
        if category == "private_rules_apply":
            props["PARKING_RULE_SOURCE"] = "MassGIS/MassDOT Roads ownership"
            props["PARKING_SOURCE_URL"] = MEDFORD_STREET_SOURCE_URL
        else:
            props["PARKING_RULE_SOURCE"] = "Medford Resident Permit Parking Streets"
            props["PARKING_SOURCE_URL"] = MEDFORD_RULE_SOURCE_URL
        props["PARKING_RULE_MATCH_LEVEL"] = match_level
        props["PARKING_CONFIDENCE"] = (
            segment_evidence.get("confidence") if segment_evidence else None
        )
        props["PARKING_DATA_UPDATED_AT"] = _generated_at(MEDFORD_SEGMENT_RULES_PATH)
        props["PARKING_MEDFORD_RULE_COUNT"] = len(rows)
        props["PARKING_MEDFORD_PARTIAL_RULE_COUNT"] = len(partial_rows)
        props["PARKING_MEDFORD_MATCHED_PARTIAL_RULE_COUNT"] = (
            segment_evidence.get("matched_partial_rule_count", 0)
            if segment_evidence
            else 0
        )
        props["PARKING_MEDFORD_RULE_SUMMARY"] = " | ".join(
            row.get("restriction_text") or row.get("permit_type") or "Resident"
            for row in rows[:3]
        )
        updated_feature = dict(feature)
        updated_feature["properties"] = props
        features.append(updated_feature)

    cambridge_rules = load_cambridge_rules()
    cambridge_streets = load_cambridge_streets()
    for feature in cambridge_streets.get("features", []):
        props = dict(feature.get("properties", {}))
        street_key = _normalize_street_name(props.get("STNAME"))
        rule = cambridge_rules.get(street_key, {})
        category, note = _classify_cambridge_parking_access(props, cambridge_rules)
        meter_count = rule.get("meter_count_estimate", 0)
        active_meter_count = rule.get("active_meter_count_estimate", 0)
        accessible_count = rule.get("accessible_space_count", 0)
        if meter_count:
            match_level = rule.get("meter_match_confidence", "nearest_street_approx")
        elif accessible_count:
            match_level = "street_name_accessible_space"
        else:
            match_level = "none"
        props["PARKING_ACCESS"] = category
        props["PARKING_DISPLAY_STATUS"] = _parking_display_status(category)
        props["PARKING_NOTE"] = note
        props["PARKING_RULE_SOURCE"] = ""
        props["PARKING_SOURCE_URL"] = ""
        props["PARKING_RULE_MATCH_LEVEL"] = ""
        props["PARKING_CONFIDENCE"] = "none"
        props["PARKING_EVIDENCE"] = _cambridge_evidence_category(rule)
        props["PARKING_EVIDENCE_SOURCE"] = "Cambridge GIS parking evidence layers"
        props["PARKING_EVIDENCE_SOURCE_URL"] = CAMBRIDGE_PARKING_SOURCE_URL
        props["PARKING_EVIDENCE_MATCH_LEVEL"] = match_level
        props["PARKING_EVIDENCE_CONFIDENCE"] = (
            "low" if meter_count or accessible_count else "none"
        )
        props["PARKING_DATA_UPDATED_AT"] = _generated_at(CAMBRIDGE_RULES_PATH)
        props["PARKING_CAMBRIDGE_METER_COUNT_ESTIMATE"] = meter_count
        props["PARKING_CAMBRIDGE_ACTIVE_METER_COUNT_ESTIMATE"] = active_meter_count
        props["PARKING_CAMBRIDGE_INACTIVE_METER_COUNT_ESTIMATE"] = max(
            int(meter_count or 0) - int(active_meter_count or 0), 0
        )
        props["PARKING_CAMBRIDGE_ACCESSIBLE_SPACE_COUNT"] = accessible_count
        props["PARKING_CAMBRIDGE_METER_HOURS"] = _format_count_values(
            rule.get("meter_operation_hours", [])
        )
        props["PARKING_CAMBRIDGE_METER_MAX_TIMES"] = _format_count_values(
            rule.get("meter_max_times", [])
        )
        props["PARKING_CAMBRIDGE_METER_RATES"] = _format_count_values(
            rule.get("meter_rates", [])
        )
        props["PARKING_CAMBRIDGE_MATCH_DISTANCE"] = _format_distance_summary(
            rule.get("meter_match_distance_meters")
        )
        updated_feature = dict(feature)
        updated_feature["properties"] = props
        features.append(updated_feature)

    _enriched_streets_cache = {
        "type": "FeatureCollection",
        "features": features,
    }
    return _enriched_streets_cache


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Render the main map page."""
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"request": request, "app_version": APP_VERSION},
    )


@app.get("/api/version")
async def get_version():
    """Return running application version for deployment verification."""
    return _json_response({"app_version": APP_VERSION})


@app.get("/api/health")
async def get_health():
    """Return a lightweight health response without loading map datasets."""
    return _json_response({"status": "ok", "app_version": APP_VERSION})


@app.get("/api/streets")
async def get_streets():
    """Return all streets as GeoJSON."""
    return _json_response(get_enriched_streets())


@app.get("/api/parking-evidence/cambridge/meters")
async def get_cambridge_meter_spaces():
    """Return Cambridge metered-space evidence geometry as GeoJSON."""
    return _json_response(load_cambridge_meter_spaces())


@app.get("/api/parking-evidence/cambridge/accessible")
async def get_cambridge_accessible_spaces():
    """Return Cambridge accessible-space evidence geometry as GeoJSON."""
    return _json_response(load_cambridge_accessible_spaces())


@app.get("/api/parking-evidence/imagery")
async def get_imagery_index():
    """Return street-level imagery metadata evidence as GeoJSON."""
    return _json_response(load_imagery_index())


@app.get("/api/parking-evidence/imagery/detections")
async def get_imagery_detections():
    """Return image-derived parking evidence detections as GeoJSON."""
    return _json_response(load_imagery_detections())


@app.get("/api/parking-evidence/imagery/reference-signs")
async def get_imagery_reference_signs():
    """Return Mapillary parking-sign reference features as GeoJSON."""
    return _json_response(load_imagery_reference_signs())


@app.get("/api/parking-evidence/imagery/reference-matches")
async def get_imagery_reference_matches():
    """Return image detections matched to parking-sign reference features."""
    return _json_response(load_imagery_reference_matches())


@app.get("/api/streets/search")
async def search_streets(q: str = ""):
    """Search streets by street name, municipality, or both."""
    streets = get_enriched_streets()
    if not q:
        return _json_response(streets)

    features = streets.get("features", [])
    query = q.casefold().strip()
    municipality_matches = [
        f
        for f in features
        if str(f.get("properties", {}).get("MUNICIPALITY") or "").casefold()
        == query
    ]
    street_matches = [
        f
        for f in features
        if query in str(f.get("properties", {}).get("STNAME") or "").casefold()
    ]

    if municipality_matches:
        filtered_features = municipality_matches
    elif street_matches:
        filtered_features = street_matches
    else:
        query_terms = query.split()
        filtered_features = [
            f
            for f in features
            if all(
                term
                in " ".join(
                    [
                        str(f.get("properties", {}).get("MUNICIPALITY") or ""),
                        str(f.get("properties", {}).get("STNAME") or ""),
                    ]
                ).casefold()
                for term in query_terms
            )
        ]

    return _json_response({"type": "FeatureCollection", "features": filtered_features})


@app.get("/api/stats")
async def get_stats():
    """Return statistics about the street data."""
    global _stats_cache
    if _stats_cache is not None:
        return _json_response(_stats_cache)

    streets = get_enriched_streets()
    features = streets.get("features", [])

    # Count unique street names
    street_names = set()
    municipality_counts = {}
    ownership_counts = {}
    func_class_counts = {}

    for f in features:
        props = f.get("properties", {})
        name = props.get("STNAME")
        municipality = props.get("MUNICIPALITY", "Unknown")
        if name:
            street_names.add((municipality, _normalize_street_name(str(name))))
        municipality_counts[municipality] = municipality_counts.get(municipality, 0) + 1

        ownership = str(props.get("OWNERSHIP") or "Unknown")
        ownership_counts[ownership] = ownership_counts.get(ownership, 0) + 1

        func_class = str(props.get("FUNC_CLASS") or "Unknown")
        func_class_counts[func_class] = func_class_counts.get(func_class, 0) + 1

    parking_access_counts = {}
    parking_display_counts = {}

    for f in features:
        props = f.get("properties", {})
        access = props.get("PARKING_ACCESS", "unknown")
        parking_access_counts[access] = parking_access_counts.get(access, 0) + 1
        display_status = props.get("PARKING_DISPLAY_STATUS", "unknown")
        parking_display_counts[display_status] = (
            parking_display_counts.get(display_status, 0) + 1
        )

    cambridge_meters = load_cambridge_meter_spaces().get("features", [])
    cambridge_accessible = load_cambridge_accessible_spaces().get("features", [])
    parking_evidence_counts = {
        "cambridge_meter_spaces": len(cambridge_meters),
        "cambridge_active_meter_spaces": sum(
            1
            for feature in cambridge_meters
            if str(feature.get("properties", {}).get("STATUS") or "").strip().casefold()
            == "in service"
        ),
        "cambridge_accessible_spaces": len(cambridge_accessible),
    }

    _stats_cache = {
        "total_segments": len(features),
        "unique_streets": len(street_names),
        "municipalities": municipality_counts,
        "ownership": ownership_counts,
        "functional_class": func_class_counts,
        "parking_access": parking_access_counts,
        "parking_display": parking_display_counts,
        "parking_evidence": parking_evidence_counts,
    }
    return _json_response(_stats_cache)


def main():
    """Run the development server."""
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
