"""Build Cambridge street geometry and first-pass parking rule summaries."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "data" / "processed" / "cambridge"
STREETS_OUTPUT_PATH = OUTPUT_DIR / "streets.geojson"
RULES_OUTPUT_PATH = OUTPUT_DIR / "parking_rules.json"
METERS_OUTPUT_PATH = OUTPUT_DIR / "metered_spaces.geojson"
ACCESSIBLE_OUTPUT_PATH = OUTPUT_DIR / "accessible_spaces.geojson"

STREET_CENTERLINES_URL = (
    "https://raw.githubusercontent.com/cambridgegis/cambridgegis_data/main/"
    "Trans/Street_Centerlines/TRANS_Centerlines.geojson"
)
METERED_SPACES_URL = (
    "https://raw.githubusercontent.com/cambridgegis/cambridgegis_data/main/"
    "Traffic/Metered_Parking_Spaces/TRAFFIC_MeteredParkingSpaces.geojson"
)
ACCESSIBLE_SPACES_URL = (
    "https://raw.githubusercontent.com/cambridgegis/cambridgegis_data/main/"
    "Traffic/Public_Handicap_Parking_Spaces/TRAFFIC_PublicHandicapParkingSpaces.geojson"
)

SOURCE_ID = "cambridge_gis"
METER_MATCH_THRESHOLD_METERS = 45
ACTIVE_METER_STATUS = "in service"
KNOWN_METER_STATUSES = {
    "in service",
    "out of service",
    "temp out of service",
    "permanently removed",
    "proposed",
}


class SourceValidationError(ValueError):
    """Raised before an implausible Cambridge source can replace generated data."""


def is_active_meter_status(value: object) -> bool:
    """Return True only for meters that the source explicitly marks in service."""
    return clean_text(value).casefold() == ACTIVE_METER_STATUS


def normalize_source_date(value: object) -> str:
    """Normalize ArcGIS epoch milliseconds while preserving existing date strings."""
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return ""
    return clean_text(value)


def validate_feature_source(
    payload: dict,
    *,
    label: str,
    minimum_features: int,
    geometry_types: set[str],
) -> None:
    if payload.get("type") != "FeatureCollection":
        raise SourceValidationError(f"{label} is not a GeoJSON FeatureCollection")
    features = payload.get("features")
    if not isinstance(features, list) or len(features) < minimum_features:
        raise SourceValidationError(
            f"{label} unexpectedly contains {len(features or [])} features; "
            f"expected at least {minimum_features}"
        )
    invalid_geometry = sum(
        1
        for feature in features
        if (feature.get("geometry") or {}).get("type") not in geometry_types
    )
    if invalid_geometry:
        raise SourceValidationError(
            f"{label} contains {invalid_geometry} unsupported geometries"
        )


def fetch_json(url: str) -> tuple[dict, dict]:
    request = Request(url, headers={"User-Agent": "parkingmap Cambridge GIS builder"})
    with urlopen(request, timeout=60) as response:
        content = response.read()
        payload = json.loads(content)
        metadata = {
            "url": url,
            "final_url": response.geturl(),
            "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
            "content_type": response.headers.get("Content-Type"),
            "last_modified": response.headers.get("Last-Modified"),
            "etag": response.headers.get("ETag"),
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        return payload, metadata


def normalize_street_name(value: str | None) -> str:
    if not value:
        return ""
    text = value.upper().replace(".", " ")
    tokens = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in text).split()
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
        "LANE": "LN",
        "LN": "LN",
        "BOULEVARD": "BLVD",
        "BLVD": "BLVD",
        "CIRCLE": "CIR",
        "CIR": "CIR",
    }
    return " ".join(token_map.get(token, token) for token in tokens)


def normalize_street_feature(feature: dict) -> dict:
    props = dict(feature.get("properties") or {})
    street = clean_text(props.get("Street")) or clean_text(props.get("Label"))
    updated = dict(feature)
    updated["properties"] = {
        "STNAME": street,
        "MUNICIPALITY": "Cambridge",
        "DATA_SOURCE": SOURCE_ID,
        "OWNERSHIP": "Unknown",
        "FUNC_CLASS": "Major Road" if props.get("MajorRoad") == 1 else "Local / Other",
        "ONEWAY": props.get("Direction"),
        "ROAD_TYPE": clean_text(props.get("ROADWAYS")),
        "FROM_STREET": "",
        "TO_STREET": "",
        "CAMBRIDGE_STREET_ID": props.get("Street_ID"),
        "CAMBRIDGE_SEGMENT_ID": props.get("ID"),
        "L_FROM": props.get("L_From"),
        "L_TO": props.get("L_To"),
        "R_FROM": props.get("R_From"),
        "R_TO": props.get("R_To"),
    }
    return updated


def normalize_meter_feature(feature: dict) -> dict:
    props = feature.get("properties") or {}
    updated = dict(feature)
    updated["properties"] = {
        "MUNICIPALITY": "Cambridge",
        "DATA_SOURCE": SOURCE_ID,
        "PARKING_EVIDENCE": "metered_space",
        "SPACE_ID": props.get("SPACE_ID"),
        "STATUS": clean_text(props.get("Status")),
        "OPERATION_HOURS": clean_text(props.get("OperationHours")),
        "MAX_TIME": clean_text(props.get("MaxTime")),
        "RATE": clean_text(props.get("Rate")),
        "PAY_BY_PHONE_ZONE": clean_text(props.get("PbyP_Zone")),
        "LAST_EDITED_DATE": normalize_source_date(props.get("last_edited_date")),
    }
    return updated


def normalize_accessible_feature(feature: dict) -> dict:
    props = feature.get("properties") or {}
    updated = dict(feature)
    updated["properties"] = {
        "MUNICIPALITY": "Cambridge",
        "DATA_SOURCE": SOURCE_ID,
        "PARKING_EVIDENCE": "accessible_space",
        "STNAME": clean_text(props.get("StreetName")),
        "STREET_NUMBER": clean_text(props.get("StreetNumber")),
        "SIDE_OF_STREET": clean_text(props.get("SideOfStreet")),
        "FROM_STREET": clean_text(props.get("From_")),
        "TO_STREET": clean_text(props.get("To_")),
        "LAST_EDITED_DATE": normalize_source_date(props.get("last_edited_date")),
    }
    return updated


def clean_text(value: object) -> str:
    text = str(value or "").strip()
    return "" if text in {"", " "} else text


def coordinates_for_feature(feature: dict) -> list[list[float]]:
    geometry = feature.get("geometry") or {}
    if geometry.get("type") == "LineString":
        return geometry.get("coordinates") or []
    if geometry.get("type") == "MultiLineString":
        return [point for line in geometry.get("coordinates") or [] for point in line]
    return []


def polygon_centroid(geometry: dict) -> tuple[float, float] | None:
    coords = geometry.get("coordinates") or []
    if not coords:
        return None

    if geometry.get("type") == "Polygon":
        rings = coords[:1]
    elif geometry.get("type") == "MultiPolygon":
        rings = [polygon[0] for polygon in coords if polygon]
    else:
        rings = []

    points = [
        point
        for ring in rings
        for point in ring
        if isinstance(point, list) and len(point) >= 2
    ]
    if not points:
        return None
    lon = sum(point[0] for point in points) / len(points)
    lat = sum(point[1] for point in points) / len(points)
    return lon, lat


def point_coordinates(geometry: dict) -> tuple[float, float] | None:
    coords = geometry.get("coordinates") or []
    if geometry.get("type") == "Point" and len(coords) >= 2:
        return coords[0], coords[1]
    return None


def to_local_meters(
    point: tuple[float, float], origin_lat: float
) -> tuple[float, float]:
    lon, lat = point
    x = lon * 111_320 * math.cos(math.radians(origin_lat))
    y = lat * 110_540
    return x, y


def distance_point_to_segment(point, start, end) -> float:
    px, py = point
    ax, ay = start
    bx, by = end
    dx = bx - ax
    dy = by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    closest_x = ax + t * dx
    closest_y = ay + t * dy
    return math.hypot(px - closest_x, py - closest_y)


def build_street_index(features: list[dict], origin_lat: float) -> list[dict]:
    index = []
    seen_ids = set()
    for feature in features:
        props = feature["properties"]
        segment_id = props["CAMBRIDGE_SEGMENT_ID"]
        if not segment_id or segment_id in seen_ids:
            raise SourceValidationError(
                "Cambridge segment IDs must be present and unique"
            )
        seen_ids.add(segment_id)
        geometry = feature["geometry"]
        parts = geometry["coordinates"]
        if geometry["type"] == "LineString":
            parts = [parts]
        edges = []
        for part in parts:
            points = [to_local_meters(point, origin_lat) for point in part]
            edges.extend(zip(points, points[1:]))
        if edges:
            xs, ys = zip(*(point for edge in edges for point in edge))
            index.append(
                {
                    "segment_id": segment_id,
                    "street_name": props["STNAME"],
                    "street_key": normalize_street_name(props["STNAME"]),
                    "bounds": (min(xs), min(ys), max(xs), max(ys)),
                    "edges": edges,
                }
            )
    return index


def nearest_street(point, street_index, origin_lat):
    local_point = to_local_meters(point, origin_lat)
    x, y = local_point
    best = None
    distance = float("inf")
    for street in street_index:
        left, bottom, right, top = street["bounds"]
        if (
            x < left - distance
            or x > right + distance
            or y < bottom - distance
            or y > top + distance
        ):
            continue
        candidate = min(
            distance_point_to_segment(local_point, a, b) for a, b in street["edges"]
        )
        if candidate < distance:
            best, distance = street, candidate
    return best, distance


def summarize_distances(distances: list[float]) -> dict | None:
    if not distances:
        return None
    ordered = sorted(distances)
    p95_index = min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1)
    middle = len(ordered) // 2
    median = (
        ordered[middle]
        if len(ordered) % 2
        else (ordered[middle - 1] + ordered[middle]) / 2
    )
    return {
        "min": round(ordered[0], 2),
        "mean": round(sum(ordered) / len(ordered), 2),
        "median": round(median, 2),
        "p95": round(ordered[p95_index], 2),
        "max": round(ordered[-1], 2),
    }


def summarize_spaces(meter_features, street_features, accessible_features) -> dict:
    all_points = [
        tuple(point)
        for feature in street_features
        for point in coordinates_for_feature(feature)
        if len(point) >= 2
    ]
    origin_lat = sum(point[1] for point in all_points) / len(all_points)
    street_index = build_street_index(street_features, origin_lat)
    summaries = defaultdict(
        lambda: {
            "meter_count_estimate": 0,
            "active_meter_count_estimate": 0,
            "meter_match_confidence": "nearest_street_approx",
            "meter_operation_hours": Counter(),
            "meter_max_times": Counter(),
            "meter_rates": Counter(),
            "match_distances": [],
        }
    )
    by_segment = defaultdict(summaries.default_factory)
    unmatched = 0
    all_match_distances = []
    for feature in meter_features:
        props = feature.get("properties") or {}
        point = polygon_centroid(feature.get("geometry") or {})
        if not point:
            props["MATCH_CONFIDENCE"] = "unmatched_no_geometry"
            unmatched += 1
            continue
        street, distance = nearest_street(point, street_index, origin_lat)
        street_name = street["street_name"]
        street_key = street["street_key"]
        props["NEAREST_STREET"] = street_name
        props["NEAREST_STREET_KEY"] = street_key
        props["MATCH_DISTANCE_METERS"] = round(distance, 2)
        props["MATCH_THRESHOLD_METERS"] = METER_MATCH_THRESHOLD_METERS
        if not street_key or distance > METER_MATCH_THRESHOLD_METERS:
            props["MATCH_CONFIDENCE"] = "unmatched_beyond_threshold"
            unmatched += 1
            continue
        props["MATCHED_STREET"] = street_name
        props["MATCHED_STREET_KEY"] = street_key
        props["MATCH_CONFIDENCE"] = "nearest_street_approx"
        segment_id = street["segment_id"]
        props["MATCHED_SEGMENT_ID"] = segment_id
        for summary in (summaries[street_key], by_segment[segment_id]):
            summary["meter_count_estimate"] += 1
            if is_active_meter_status(props.get("STATUS")):
                summary["active_meter_count_estimate"] += 1
                summary["meter_operation_hours"][
                    clean_text(props.get("OPERATION_HOURS"))
                ] += 1
                summary["meter_max_times"][clean_text(props.get("MAX_TIME"))] += 1
                summary["meter_rates"][clean_text(props.get("RATE"))] += 1
            summary["match_distances"].append(distance)
        all_match_distances.append(distance)
    streets_by_name = defaultdict(list)
    for street in street_index:
        streets_by_name[street["street_key"]].append(street)
    for feature in accessible_features:
        props = feature["properties"]
        point = point_coordinates(feature["geometry"])
        candidates = streets_by_name.get(normalize_street_name(props["STNAME"]))
        if point and candidates:
            street, distance = nearest_street(point, candidates, origin_lat)
            if distance <= METER_MATCH_THRESHOLD_METERS:
                props["MATCHED_SEGMENT_ID"] = street["segment_id"]
                props["MATCH_DISTANCE_METERS"] = round(distance, 2)
                summary = by_segment[street["segment_id"]]
                summary["accessible_space_count"] = (
                    summary.get("accessible_space_count", 0) + 1
                )
    return {
        "streets": summaries,
        "segments": by_segment,
        "unmatched_meter_spaces": unmatched,
        "match_distance_meters": summarize_distances(all_match_distances),
    }


def counter_to_list(counter: Counter) -> list[dict]:
    return [
        {"value": value, "count": count}
        for value, count in counter.most_common()
        if value
    ]


def build() -> tuple[dict, dict, dict, dict]:
    raw_streets, street_source = fetch_json(STREET_CENTERLINES_URL)
    raw_meters, meter_source = fetch_json(METERED_SPACES_URL)
    raw_accessible, accessible_source = fetch_json(ACCESSIBLE_SPACES_URL)

    validate_feature_source(
        raw_streets,
        label="Cambridge street centerlines",
        minimum_features=2_000,
        geometry_types={"LineString", "MultiLineString"},
    )
    validate_feature_source(
        raw_meters,
        label="Cambridge metered spaces",
        minimum_features=2_000,
        geometry_types={"Polygon", "MultiPolygon"},
    )
    validate_feature_source(
        raw_accessible,
        label="Cambridge public accessible spaces",
        minimum_features=100,
        geometry_types={"Point"},
    )

    meter_ids = [
        clean_text((feature.get("properties") or {}).get("SPACE_ID"))
        for feature in raw_meters.get("features", [])
    ]
    if not all(meter_ids) or len(meter_ids) != len(set(meter_ids)):
        raise SourceValidationError("Cambridge meter SPACE_ID values must be present and unique")
    meter_statuses = {
        clean_text((feature.get("properties") or {}).get("Status")).casefold()
        for feature in raw_meters.get("features", [])
    }
    unexpected_statuses = meter_statuses - KNOWN_METER_STATUSES
    if unexpected_statuses:
        raise SourceValidationError(
            f"Cambridge meters contain unexpected statuses: {sorted(unexpected_statuses)}"
        )

    street_features = [
        normalize_street_feature(feature)
        for feature in raw_streets.get("features", [])
        if feature.get("geometry")
    ]
    meter_features = [
        normalize_meter_feature(feature)
        for feature in raw_meters.get("features", [])
        if feature.get("geometry")
    ]
    accessible_features = [
        normalize_accessible_feature(feature)
        for feature in raw_accessible.get("features", [])
        if feature.get("geometry")
    ]
    return assemble_layers(
        street_features,
        meter_features,
        accessible_features,
        street_source,
        meter_source,
        accessible_source,
    )


def assemble_layers(
    street_features,
    meter_features,
    accessible_features,
    street_source,
    meter_source,
    accessible_source,
):
    meter_summary = summarize_spaces(
        meter_features, street_features, accessible_features
    )
    accessible_counts = Counter(
        normalize_street_name(f["properties"]["STNAME"]) for f in accessible_features
    )

    rules = {}
    for feature in street_features:
        key = normalize_street_name(feature["properties"].get("STNAME"))
        if key and key not in rules:
            rules[key] = {
                "street_name": feature["properties"].get("STNAME"),
                "meter_count_estimate": 0,
                "active_meter_count_estimate": 0,
                "meter_match_confidence": "none",
                "meter_operation_hours": [],
                "meter_max_times": [],
                "meter_rates": [],
                "meter_match_distance_meters": None,
                "accessible_space_count": accessible_counts.get(key, 0),
            }

    segments = {}
    for scope, records in (("streets", rules), ("segments", segments)):
        for key, summary in meter_summary[scope].items():
            record = records.setdefault(key, {})
            for field in ("meter_count_estimate", "active_meter_count_estimate"):
                record[field] = summary[field]
            if scope == "segments":
                record["accessible_space_count"] = summary.get(
                    "accessible_space_count", 0
                )
            record["meter_match_confidence"] = (
                "nearest_segment_approx"
                if scope == "segments"
                else "nearest_street_approx"
            )
            for field in ("meter_operation_hours", "meter_max_times", "meter_rates"):
                record[field] = counter_to_list(summary[field])
            record["meter_match_distance_meters"] = summarize_distances(
                summary["match_distances"]
            )

    now = datetime.now(timezone.utc).isoformat()
    streets_geojson = {
        "type": "FeatureCollection",
        "properties": {
            "municipality": "Cambridge",
            "source_id": SOURCE_ID,
            "street_centerlines_url": STREET_CENTERLINES_URL,
            "source": street_source,
            "generated_at_utc": now,
            "feature_count": len(street_features),
        },
        "features": street_features,
    }
    rules_json = {
        "generated_at_utc": now,
        "municipality": "cambridge",
        "source_id": SOURCE_ID,
        "street_centerlines_url": STREET_CENTERLINES_URL,
        "metered_spaces_url": METERED_SPACES_URL,
        "accessible_spaces_url": ACCESSIBLE_SPACES_URL,
        "sources": {
            "street_centerlines": street_source,
            "metered_spaces": meter_source,
            "accessible_spaces": accessible_source,
        },
        "parser_note": (
            "Meter polygons are matched to the nearest centerline segment. An approximate "
            "association does not establish curb rules. Match distance is "
            "retained on each meter feature for quality review."
        ),
        "meter_match_threshold_meters": METER_MATCH_THRESHOLD_METERS,
        "meter_match_distance_meters": meter_summary["match_distance_meters"],
        "street_count": len(rules),
        "metered_street_count": sum(
            1 for record in rules.values() if record.get("meter_count_estimate", 0) > 0
        ),
        "unmatched_meter_spaces": meter_summary["unmatched_meter_spaces"],
        "streets": rules,
        "segments": segments,
    }
    meters_geojson = {
        "type": "FeatureCollection",
        "properties": {
            "municipality": "Cambridge",
            "source_id": SOURCE_ID,
            "metered_spaces_url": METERED_SPACES_URL,
            "source": meter_source,
            "generated_at_utc": now,
            "feature_count": len(meter_features),
        },
        "features": meter_features,
    }
    accessible_geojson = {
        "type": "FeatureCollection",
        "properties": {
            "municipality": "Cambridge",
            "source_id": SOURCE_ID,
            "accessible_spaces_url": ACCESSIBLE_SPACES_URL,
            "source": accessible_source,
            "generated_at_utc": now,
            "feature_count": len(accessible_features),
        },
        "features": accessible_features,
    }
    return streets_geojson, rules_json, meters_geojson, accessible_geojson


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--from-snapshot",
        action="store_true",
        help="Rematch the saved Cambridge geometry using its original source metadata.",
    )
    from_snapshot = parser.parse_args().from_snapshot
    if from_snapshot:
        layers = [
            json.loads(path.read_text())
            for path in (
                STREETS_OUTPUT_PATH,
                METERS_OUTPUT_PATH,
                ACCESSIBLE_OUTPUT_PATH,
            )
        ]
        # Clear previously derived associations before running the matcher again.
        for feature in layers[1]["features"] + layers[2]["features"]:
            props = feature["properties"]
            for key in list(props):
                if key.startswith(("NEAREST_", "MATCHED_", "MATCH_")):
                    del props[key]
        streets_geojson, rules_json, meters_geojson, accessible_geojson = (
            assemble_layers(
                *(layer["features"] for layer in layers),
                *(layer["properties"]["source"] for layer in layers),
            )
        )
    else:
        streets_geojson, rules_json, meters_geojson, accessible_geojson = build()
        STREETS_OUTPUT_PATH.write_text(json.dumps(streets_geojson, indent=2) + "\n")
    RULES_OUTPUT_PATH.write_text(json.dumps(rules_json, indent=2) + "\n")
    METERS_OUTPUT_PATH.write_text(json.dumps(meters_geojson, indent=2) + "\n")
    ACCESSIBLE_OUTPUT_PATH.write_text(json.dumps(accessible_geojson, indent=2) + "\n")
    if not from_snapshot:
        print(f"Wrote {STREETS_OUTPUT_PATH}")
    print(f"Wrote {RULES_OUTPUT_PATH}")
    print(f"Wrote {METERS_OUTPUT_PATH}")
    print(f"Wrote {ACCESSIBLE_OUTPUT_PATH}")
    print(f"Segments: {len(streets_geojson['features'])}")
    print(f"Unique streets: {rules_json['street_count']}")
    print(f"Metered streets: {rules_json['metered_street_count']}")
    print(f"Meter spaces: {len(meters_geojson['features'])}")
    print(f"Accessible spaces: {len(accessible_geojson['features'])}")
    print(f"Unmatched meter spaces: {rules_json['unmatched_meter_spaces']}")


if __name__ == "__main__":
    main()
