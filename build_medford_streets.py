"""Download Medford street geometry from MassGIS/MassDOT Roads."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "data" / "processed" / "medford"
OUTPUT_PATH = OUTPUT_DIR / "streets.geojson"

SERVICE_URL = (
    "https://services1.arcgis.com/hGdibHYSPO59RG1h/arcgis/rest/services/"
    "MassDOTRoads_gdb/FeatureServer/0/query"
)
SOURCE_ID = "massgis_massdot_roads_medford"
PAGE_SIZE = 2000


def fetch_page(offset: int) -> dict:
    params = {
        "where": "MGIS_TOWN='MEDFORD'",
        "outFields": (
            "OBJECTID,STREETNAME,STREET_NAME,FM_ST_NAME,TO_ST_NAME,"
            "RDTYPE,ADMIN_TYPE,F_CLASS,FACILITY,JURISDICTN,ROW_WIDTH,"
            "LENGTH_FT,MGIS_TOWN"
        ),
        "returnGeometry": "true",
        "outSR": "4326",
        "resultOffset": str(offset),
        "resultRecordCount": str(PAGE_SIZE),
        "f": "geojson",
    }
    url = f"{SERVICE_URL}?{urlencode(params)}"
    with urlopen(url, timeout=60) as response:
        return json.loads(response.read())


def normalize_feature(feature: dict) -> dict:
    props = dict(feature.get("properties") or {})
    street_name = clean_name(props.get("STREETNAME")) or clean_name(props.get("STREET_NAME"))
    from_street = clean_name(props.get("FM_ST_NAME"))
    to_street = clean_name(props.get("TO_ST_NAME"))
    ownership, ownership_source, ownership_confidence = ownership_details(
        props.get("FACILITY"), props.get("JURISDICTN")
    )
    normalized = {
        "STNAME": street_name,
        "MUNICIPALITY": "Medford",
        "DATA_SOURCE": SOURCE_ID,
        "OWNERSHIP": ownership,
        "OWNERSHIP_SOURCE": ownership_source,
        "OWNERSHIP_CONFIDENCE": ownership_confidence,
        "FUNC_CLASS": functional_class_text(props.get("F_CLASS")),
        "ROAD_TYPE": road_type_text(props.get("RDTYPE")),
        "FROM_STREET": from_street,
        "TO_STREET": to_street,
        "ROW_WIDTH": props.get("ROW_WIDTH"),
        "Shape_Leng": props.get("LENGTH_FT"),
        "MASSDOT_OBJECTID": props.get("OBJECTID"),
    }
    updated = dict(feature)
    updated["properties"] = normalized
    return updated


def clean_name(value: object) -> str:
    text = str(value or "").strip()
    return "" if text in {"", " "} else text


def ownership_details(facility: object, jurisdiction: object) -> tuple[str, str, str]:
    """Keep MassDOT ownership inference explicit until the city map is registered."""
    if facility == 14:
        return "Private", "MassDOT FACILITY code 14", "medium"
    if str(jurisdiction or "").strip() == "2":
        return "Public", "MassDOT JURISDICTN code 2", "medium"
    return "Unknown", "Not resolved from MassDOT road attributes", "none"


def ownership_text(facility: object, jurisdiction: object) -> str:
    return ownership_details(facility, jurisdiction)[0]


def functional_class_text(value: object) -> str:
    labels = {
        0: "Local",
        1: "Interstate",
        2: "Principal Arterial",
        3: "Minor Arterial",
        5: "Urban Minor Arterial / Rural Major Collector",
        6: "Urban Collector / Rural Minor Collector",
    }
    return labels.get(value, "Unknown")


def road_type_text(value: object) -> str:
    labels = {
        1: "Limited Access Highway",
        2: "Multi-lane Highway",
        3: "Numbered Route",
        4: "Major Road",
        5: "Local Road",
        6: "Local Road without Inventory",
        7: "Ramp",
    }
    return labels.get(value, "Unknown")


def build_geojson() -> dict:
    features = []
    offset = 0
    while True:
        page = fetch_page(offset)
        page_features = page.get("features", [])
        features.extend(normalize_feature(feature) for feature in page_features)
        if not page.get("properties", {}).get("exceededTransferLimit"):
            break
        offset += PAGE_SIZE

    ownership_counts = {}
    for feature in features:
        ownership = feature.get("properties", {}).get("OWNERSHIP", "Unknown")
        ownership_counts[ownership] = ownership_counts.get(ownership, 0) + 1

    return {
        "type": "FeatureCollection",
        "properties": {
            "municipality": "Medford",
            "source_id": SOURCE_ID,
            "source_url": SERVICE_URL,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "feature_count": len(features),
            "ownership_counts": ownership_counts,
            "ownership_note": (
                "Ownership is inferred only from MassDOT FACILITY/JURISDICTN attributes. "
                "The official Medford public/private ways map remains source evidence and "
                "has not been georegistered; unresolved segments stay Unknown."
            ),
        },
        "features": features,
    }


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    geojson = build_geojson()
    OUTPUT_PATH.write_text(json.dumps(geojson, indent=2) + "\n")
    unique_names = {
        feature.get("properties", {}).get("STNAME")
        for feature in geojson["features"]
        if feature.get("properties", {}).get("STNAME")
    }
    print(f"Wrote {OUTPUT_PATH}")
    print(f"Segments: {len(geojson['features'])}")
    print(f"Unique named streets: {len(unique_names)}")


if __name__ == "__main__":
    main()
