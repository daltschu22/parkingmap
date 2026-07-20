"""Build a Mapillary traffic-sign reference layer for parking evidence.

This pulls sign map features, not raw imagery detections. Mapillary has already
triangulated these from multiple images, so they are a better comparison layer
than OCRing every sign-like crop from street photos.
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.parse
import urllib.request
from fnmatch import fnmatchcase
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "data" / "processed" / "imagery"
DEFAULT_OUTPUT_PATH = OUTPUT_DIR / "mapillary_parking_sign_features.geojson"
MAPILLARY_MAP_FEATURES_URL = "https://graph.mapillary.com/map_features"
MAPILLARY_FEATURE_FIELDS = "id,object_type,object_value,geometry,first_seen_at,last_seen_at,images"
DEFAULT_OBJECT_VALUES = [
    "*parking*",
    "regulatory--*parking*",
    "regulatory--parking-restrictions--*",
    "regulatory--no-parking--*",
    "regulatory--no-parking-or-no-stopping--*",
    "regulatory--no-stopping--*",
    "regulatory--no-standing--*",
]


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "parkingmap-mapillary-sign-index/0.1"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read())


def mapillary_url(access_token: str, bbox: list[float], object_value: str, limit: int, after: str | None = None) -> str:
    params = {
        "access_token": access_token,
        "fields": MAPILLARY_FEATURE_FIELDS,
        "bbox": ",".join(f"{value:.7f}" for value in bbox),
        "object_values": object_value,
        "limit": str(limit),
    }
    if after:
        params["after"] = after
    return f"{MAPILLARY_MAP_FEATURES_URL}?{urllib.parse.urlencode(params)}"


def next_after(payload: dict) -> str | None:
    next_url = ((payload.get("paging") or {}).get("cursors") or {}).get("after")
    if next_url:
        return str(next_url)
    paging_next = (payload.get("paging") or {}).get("next")
    if not paging_next:
        return None
    parsed = urllib.parse.urlparse(paging_next)
    query = urllib.parse.parse_qs(parsed.query)
    values = query.get("after") or []
    return values[0] if values else None


def linked_image_ids(feature: dict) -> list[str]:
    rows = ((feature.get("images") or {}).get("data") or [])
    return [str(row.get("id")) for row in rows if row.get("id")]


def matches_query_value(feature: dict, query_value: str) -> bool:
    object_value = str(feature.get("object_value") or "")
    return fnmatchcase(object_value, query_value)


def normalize_feature(feature: dict, query_value: str) -> dict:
    props = {
        "PROVIDER": "mapillary",
        "MAPILLARY_FEATURE_ID": str(feature.get("id") or ""),
        "OBJECT_TYPE": feature.get("object_type") or "",
        "OBJECT_VALUE": feature.get("object_value") or "",
        "FIRST_SEEN_AT": feature.get("first_seen_at"),
        "LAST_SEEN_AT": feature.get("last_seen_at"),
        "QUERY_OBJECT_VALUE": query_value,
        "LINKED_IMAGE_IDS": linked_image_ids(feature),
        "LINKED_IMAGE_COUNT": len(linked_image_ids(feature)),
        "SOURCE_URL": (
            f"https://www.mapillary.com/app/?focus=map&pKey={feature.get('id')}"
            if feature.get("id")
            else ""
        ),
        "PARKING_REFERENCE_CLASS": "mapillary_parking_sign_feature",
    }
    return {
        "type": "Feature",
        "geometry": feature.get("geometry") or None,
        "properties": props,
    }


def fetch_features(args: argparse.Namespace, access_token: str) -> tuple[list[dict], list[dict]]:
    features_by_id: dict[str, dict] = {}
    errors = []
    for object_value in args.object_value:
        after = None
        pages = 0
        while True:
            try:
                payload = fetch_json(mapillary_url(access_token, args.bbox, object_value, args.page_limit, after))
            except Exception as exc:  # noqa: BLE001 - preserve API failures in output metadata.
                errors.append({"object_value": object_value, "after": after, "error": f"{type(exc).__name__}: {exc}"})
                break

            for row in payload.get("data", []):
                if not matches_query_value(row, object_value):
                    continue
                feature_id = str(row.get("id") or "")
                if feature_id and feature_id not in features_by_id:
                    features_by_id[feature_id] = normalize_feature(row, object_value)

            pages += 1
            if args.max_pages and pages >= args.max_pages:
                break
            after = next_after(payload)
            if not after:
                break
            if args.sleep_seconds:
                time.sleep(args.sleep_seconds)
    return list(features_by_id.values()), errors


def build_collection(args: argparse.Namespace) -> dict:
    access_token = os.getenv("MAPILLARY_ACCESS_TOKEN", "").strip()
    if not access_token:
        raise SystemExit("Set MAPILLARY_ACCESS_TOKEN before running this script.")
    features, errors = fetch_features(args, access_token)
    counts: dict[str, int] = {}
    for feature in features:
        value = feature["properties"].get("OBJECT_VALUE") or ""
        counts[value] = counts.get(value, 0) + 1
    return {
        "type": "FeatureCollection",
        "properties": {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "provider": "mapillary",
            "provider_api": MAPILLARY_MAP_FEATURES_URL,
            "bbox": args.bbox,
            "object_values": args.object_value,
            "feature_count": len(features),
            "object_value_counts": counts,
            "errors": errors,
            "note": (
                "Parking-related traffic-sign map features. Use these to prioritize OCR/review of nearby "
                "street imagery detections; do not treat them as legal rules without municipal source checks."
            ),
        },
        "features": features,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bbox",
        type=float,
        nargs=4,
        metavar=("WEST", "SOUTH", "EAST", "NORTH"),
        required=True,
        help="Mapillary bbox in lon/lat order.",
    )
    parser.add_argument(
        "--object-value",
        action="append",
        default=list(DEFAULT_OBJECT_VALUES),
        help="Mapillary traffic-sign object_values wildcard. Repeat to add more.",
    )
    parser.add_argument("--page-limit", type=int, default=2000)
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--sleep-seconds", type=float, default=0.1)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    collection = build_collection(args)
    args.output.write_text(json.dumps(collection, indent=2) + "\n")
    props = collection["properties"]
    print(f"Wrote {args.output}")
    print(f"Features: {props['feature_count']}")
    print(f"Classes: {props['object_value_counts']}")
    print(f"Errors: {len(props['errors'])}")


if __name__ == "__main__":
    main()
