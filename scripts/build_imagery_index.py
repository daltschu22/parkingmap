"""Build a street-level imagery metadata index.

This script stores metadata only; it does not download photos or run image
inference. Use Mapillary first when you have an access token. KartaView remains
available as a no-token fallback.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = DATA_DIR / "processed" / "imagery"
DEFAULT_OUTPUT_PATH = OUTPUT_DIR / "street_imagery_index.geojson"
KARTAVIEW_PHOTO_URL = "https://api.openstreetcam.org/2.0/photo/"
MAPILLARY_IMAGES_URL = "https://graph.mapillary.com/images"
MAPILLARY_IMAGE_FIELDS = (
    "id,computed_geometry,geometry,captured_at,compass_angle,"
    "thumb_256_url,thumb_1024_url,thumb_original_url,sequence"
)

STREET_SOURCES = {
    "somerville": DATA_DIR / "streets.geojson",
    "medford": DATA_DIR / "processed" / "medford" / "streets.geojson",
    "cambridge": DATA_DIR / "processed" / "cambridge" / "streets.geojson",
}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def coordinates_for_feature(feature: dict) -> list[list[float]]:
    geometry = feature.get("geometry") or {}
    geom_type = geometry.get("type")
    coords = geometry.get("coordinates") or []
    if geom_type == "LineString":
        return coords
    if geom_type == "MultiLineString":
        return [point for line in coords for point in line]
    return []


def midpoint_coordinate(feature: dict) -> tuple[float, float] | None:
    coords = coordinates_for_feature(feature)
    if not coords:
        return None
    point = coords[len(coords) // 2]
    if len(point) < 2:
        return None
    lon, lat = point[0], point[1]
    return float(lat), float(lon)


def street_features(municipalities: Iterable[str]) -> Iterable[dict]:
    for municipality in municipalities:
        path = STREET_SOURCES[municipality]
        data = load_json(path)
        for feature in data.get("features", []):
            props = feature.get("properties") or {}
            point = midpoint_coordinate(feature)
            if not point:
                continue
            yield {
                "municipality": props.get("MUNICIPALITY") or municipality.title(),
                "street_name": props.get("STNAME") or "",
                "func_class": props.get("FUNC_CLASS") or "",
                "road_type": props.get("ROAD_TYPE") or "",
                "segment_id": (
                    props.get("MASSDOT_OBJECTID")
                    or props.get("CAMBRIDGE_SEGMENT_ID")
                    or props.get("OBJECTID")
                    or props.get("id")
                ),
                "sample_lat": point[0],
                "sample_lng": point[1],
            }


def normalize_name(value: object) -> str:
    return " ".join(str(value or "").upper().replace(".", " ").split())


def meters_between(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    lat_m = (lat1 - lat2) * 110_540
    lng_m = (lng1 - lng2) * 111_320 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(lat_m, lng_m)


def sample_in_bbox(sample: dict, bbox: tuple[float, float, float, float] | None) -> bool:
    if bbox is None:
        return True
    west, south, east, north = bbox
    return west <= sample["sample_lng"] <= east and south <= sample["sample_lat"] <= north


def sample_in_radius(sample: dict, center: tuple[float, float] | None, radius: int | None) -> bool:
    if center is None or radius is None:
        return True
    center_lat, center_lng = center
    return meters_between(sample["sample_lat"], sample["sample_lng"], center_lat, center_lng) <= radius


def is_highway_sample(sample: dict) -> bool:
    text = f"{sample.get('street_name')} {sample.get('func_class')} {sample.get('road_type')}".upper()
    return any(
        token in text
        for token in (
            "INTERSTATE",
            "LIMITED ACCESS",
            "RAMP",
            "HIGHWAY",
        )
    )


def dedupe_samples(samples: Iterable[dict]) -> list[dict]:
    seen = set()
    deduped = []
    for sample in samples:
        key = (
            sample["municipality"],
            sample["street_name"],
            round(float(sample["sample_lat"]), 4),
            round(float(sample["sample_lng"]), 4),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(sample)
    return deduped


def fetch_kartaview_nearby(lat: float, lng: float, radius: int, limit: int) -> list[dict]:
    params = {
        "lat": f"{lat:.7f}",
        "lng": f"{lng:.7f}",
        "radius": str(radius),
        "zoomLevel": "18",
        "join": "sequence",
        "orderBy": "id",
        "orderDirection": "desc",
    }
    url = f"{KARTAVIEW_PHOTO_URL}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": "parkingmap-imagery-index/0.1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read())
    rows = payload.get("result", {}).get("data", [])
    return rows[:limit]


def bbox_around_point(lat: float, lng: float, radius_meters: int) -> str:
    lat_delta = radius_meters / 111_320
    lng_delta = radius_meters / (111_320 * max(0.1, abs(math.cos(math.radians(lat)))))
    west = lng - lng_delta
    south = lat - lat_delta
    east = lng + lng_delta
    north = lat + lat_delta
    return f"{west:.7f},{south:.7f},{east:.7f},{north:.7f}"


def fetch_mapillary_nearby(
    lat: float,
    lng: float,
    radius: int,
    limit: int,
    access_token: str,
) -> list[dict]:
    params = {
        "access_token": access_token,
        "fields": MAPILLARY_IMAGE_FIELDS,
        "bbox": bbox_around_point(lat, lng, radius),
        "limit": str(limit),
    }
    url = f"{MAPILLARY_IMAGES_URL}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": "parkingmap-imagery-index/0.1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read())
    return payload.get("data", [])[:limit]


def fetch_mapillary_bbox(
    bbox: tuple[float, float, float, float],
    limit: int,
    access_token: str,
) -> list[dict]:
    params = {
        "access_token": access_token,
        "fields": MAPILLARY_IMAGE_FIELDS,
        "bbox": ",".join(f"{value:.7f}" for value in bbox),
        "limit": str(limit),
    }
    url = f"{MAPILLARY_IMAGES_URL}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": "parkingmap-imagery-index/0.1"})
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read())
    return payload.get("data", [])[:limit]


def normalize_kartaview_photo(photo: dict, sample: dict) -> dict:
    lat = photo.get("lat") or photo.get("matchLat") or sample["sample_lat"]
    lng = photo.get("lng") or photo.get("matchLng") or sample["sample_lng"]
    return {
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": [float(lng), float(lat)],
        },
        "properties": {
            "PROVIDER": "kartaview",
            "IMAGE_ID": str(photo.get("id") or ""),
            "SEQUENCE_ID": str(photo.get("sequenceId") or photo.get("sequence_id") or ""),
            "CAPTURED_AT": photo.get("dateAdded") or photo.get("dateProcessed"),
            "HEADING": photo.get("heading") or photo.get("headers"),
            "DISTANCE_METERS": photo.get("distance"),
            "IMAGE_THUMB_URL": photo.get("imageThUrl") or photo.get("fileurlTh"),
            "IMAGE_PREVIEW_URL": photo.get("imageProcUrl") or photo.get("fileurlProc"),
            "SOURCE_URL": f"https://kartaview.org/details/{photo.get('id')}" if photo.get("id") else "",
            "SAMPLE_MUNICIPALITY": sample["municipality"],
            "SAMPLE_STREET_NAME": sample["street_name"],
            "SAMPLE_SEGMENT_ID": sample["segment_id"],
            "SAMPLE_LAT": sample["sample_lat"],
            "SAMPLE_LNG": sample["sample_lng"],
        },
    }


def mapillary_point(photo: dict, sample: dict) -> tuple[float, float]:
    for key in ("computed_geometry", "geometry"):
        geometry = photo.get(key) or {}
        coords = geometry.get("coordinates") or []
        if geometry.get("type") == "Point" and len(coords) >= 2:
            return float(coords[1]), float(coords[0])
    return sample["sample_lat"], sample["sample_lng"]


def normalize_mapillary_image(photo: dict, sample: dict) -> dict:
    lat, lng = mapillary_point(photo, sample)
    return {
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": [lng, lat],
        },
        "properties": {
            "PROVIDER": "mapillary",
            "IMAGE_ID": str(photo.get("id") or ""),
            "SEQUENCE_ID": str(photo.get("sequence") or ""),
            "CAPTURED_AT": photo.get("captured_at"),
            "HEADING": photo.get("compass_angle"),
            "IMAGE_THUMB_URL": photo.get("thumb_256_url"),
            "IMAGE_PREVIEW_URL": photo.get("thumb_1024_url"),
            "IMAGE_ORIGINAL_URL": photo.get("thumb_original_url"),
            "SOURCE_URL": f"https://www.mapillary.com/app/?pKey={photo.get('id')}" if photo.get("id") else "",
            "SAMPLE_MUNICIPALITY": sample["municipality"],
            "SAMPLE_STREET_NAME": sample["street_name"],
            "SAMPLE_SEGMENT_ID": sample["segment_id"],
            "SAMPLE_LAT": sample["sample_lat"],
            "SAMPLE_LNG": sample["sample_lng"],
        },
    }


def normalize_mapillary_bbox_image(photo: dict) -> dict:
    sample = {
        "sample_lat": 0,
        "sample_lng": 0,
        "municipality": "",
        "street_name": "",
        "segment_id": "",
    }
    feature = normalize_mapillary_image(photo, sample)
    props = feature["properties"]
    props["SAMPLE_MUNICIPALITY"] = ""
    props["SAMPLE_STREET_NAME"] = ""
    props["SAMPLE_SEGMENT_ID"] = ""
    props["SAMPLE_LAT"] = None
    props["SAMPLE_LNG"] = None
    return feature


def build_index(args: argparse.Namespace) -> dict:
    municipalities = args.municipality or sorted(STREET_SOURCES)
    access_token = os.getenv("MAPILLARY_ACCESS_TOKEN", "").strip()
    if args.provider == "mapillary" and not access_token:
        raise SystemExit("Set MAPILLARY_ACCESS_TOKEN before using --provider mapillary.")

    bbox = tuple(args.bbox) if args.bbox else None
    if args.mode == "bbox":
        if args.provider != "mapillary":
            raise SystemExit("--mode bbox currently supports --provider mapillary only.")
        if bbox is None:
            raise SystemExit("--mode bbox requires --bbox WEST SOUTH EAST NORTH.")
        photos = fetch_mapillary_bbox(bbox, args.limit or 2000, access_token)
        return {
            "type": "FeatureCollection",
            "properties": {
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "provider": args.provider,
                "provider_api": MAPILLARY_IMAGES_URL,
                "municipalities": municipalities,
                "mode": args.mode,
                "sample_count": 0,
                "image_metadata_count": len(photos),
                "radius_meters": None,
                "sample_bbox": args.bbox,
                "sample_center": None,
                "sample_radius_meters": None,
                "photos_per_sample": None,
                "errors": [],
                "note": (
                    "Metadata index only. Image URLs are source evidence for later review/inference; "
                    "do not treat image detections as legal parking rules without confidence and source notes."
                ),
            },
            "features": [normalize_mapillary_bbox_image(photo) for photo in photos],
        }

    center = (args.center_lat, args.center_lng) if args.center_lat is not None and args.center_lng is not None else None
    samples = [
        sample
        for sample in dedupe_samples(street_features(municipalities))
        if sample_in_bbox(sample, bbox) and sample_in_radius(sample, center, args.sample_radius_meters)
    ]
    if args.street:
        street_key = normalize_name(args.street)
        samples = [sample for sample in samples if normalize_name(sample["street_name"]) == street_key]
    if args.exclude_highways:
        samples = [sample for sample in samples if not is_highway_sample(sample)]
    if center:
        samples.sort(
            key=lambda sample: meters_between(
                sample["sample_lat"],
                sample["sample_lng"],
                center[0],
                center[1],
            )
        )
    if args.limit:
        samples = samples[: args.limit]

    features = []
    errors = []
    for index, sample in enumerate(samples, start=1):
        try:
            if args.provider == "mapillary":
                photos = fetch_mapillary_nearby(
                    sample["sample_lat"],
                    sample["sample_lng"],
                    args.radius_meters,
                    args.photos_per_sample,
                    access_token,
                )
                features.extend(normalize_mapillary_image(photo, sample) for photo in photos)
            else:
                photos = fetch_kartaview_nearby(
                    sample["sample_lat"],
                    sample["sample_lng"],
                    args.radius_meters,
                    args.photos_per_sample,
                )
                features.extend(normalize_kartaview_photo(photo, sample) for photo in photos)
        except Exception as exc:  # noqa: BLE001 - persist provider/API failures in output.
            errors.append(
                {
                    "sample_index": index,
                    "municipality": sample["municipality"],
                    "street_name": sample["street_name"],
                    "lat": sample["sample_lat"],
                    "lng": sample["sample_lng"],
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        if args.sleep_seconds:
            time.sleep(args.sleep_seconds)

    return {
        "type": "FeatureCollection",
        "properties": {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "provider": args.provider,
            "provider_api": MAPILLARY_IMAGES_URL if args.provider == "mapillary" else KARTAVIEW_PHOTO_URL,
            "municipalities": municipalities,
            "mode": args.mode,
            "sample_count": len(samples),
            "image_metadata_count": len(features),
            "radius_meters": args.radius_meters,
            "sample_bbox": args.bbox,
            "sample_center": [args.center_lat, args.center_lng] if center else None,
            "sample_radius_meters": args.sample_radius_meters,
            "photos_per_sample": args.photos_per_sample,
            "errors": errors,
            "note": (
                "Metadata index only. Image URLs are source evidence for later review/inference; "
                "do not treat image detections as legal parking rules without confidence and source notes."
            ),
        },
        "features": features,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--provider",
        choices=["mapillary", "kartaview"],
        default="mapillary",
        help="Imagery metadata provider. Mapillary requires MAPILLARY_ACCESS_TOKEN.",
    )
    parser.add_argument(
        "--mode",
        choices=["samples", "bbox"],
        default="samples",
        help="samples queries around street segment midpoints; bbox pulls provider metadata directly for a bounding box.",
    )
    parser.add_argument(
        "--municipality",
        choices=sorted(STREET_SOURCES),
        action="append",
        help="Municipality to sample. Repeat to include multiple. Defaults to all.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=25,
        help="Maximum sampled street segments. Keep low because KartaView anonymous rate limit is modest.",
    )
    parser.add_argument("--radius-meters", type=int, default=35)
    parser.add_argument(
        "--bbox",
        type=float,
        nargs=4,
        metavar=("WEST", "SOUTH", "EAST", "NORTH"),
        help="Only sample street segment midpoints inside this lon/lat bounding box.",
    )
    parser.add_argument("--center-lat", type=float)
    parser.add_argument("--center-lng", type=float)
    parser.add_argument(
        "--sample-radius-meters",
        type=int,
        help="Only sample street segment midpoints within this radius of --center-lat/--center-lng.",
    )
    parser.add_argument(
        "--exclude-highways",
        action="store_true",
        help="Skip highway/ramp/limited-access samples when building curbside imagery sets.",
    )
    parser.add_argument(
        "--street",
        help="Only sample segments whose normalized STNAME exactly matches this street name.",
    )
    parser.add_argument("--photos-per-sample", type=int, default=2)
    parser.add_argument("--sleep-seconds", type=float, default=0.25)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    index = build_index(args)
    args.output.write_text(json.dumps(index, indent=2) + "\n")
    props = index["properties"]
    print(f"Wrote {args.output}")
    print(f"Samples: {props['sample_count']}")
    print(f"Image metadata rows: {props['image_metadata_count']}")
    print(f"Errors: {len(props['errors'])}")


if __name__ == "__main__":
    main()
