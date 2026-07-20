"""Match image detections to Mapillary parking-sign reference features."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

SIGNLIKE_DETECTION_TOKENS = ("sign", "meter", "pay station")


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def load_geojson(path: Path) -> dict:
    return json.loads(path.read_text())


def is_signlike_detection(row: dict) -> bool:
    text = str(row.get("detected_object") or row.get("DETECTED_OBJECT") or "").lower()
    return any(token in text for token in SIGNLIKE_DETECTION_TOKENS)


def image_id(row: dict) -> str:
    return str(row.get("image_id") or row.get("IMAGE_ID") or "")


def object_confidence(row: dict) -> float:
    try:
        return float(row.get("object_confidence") or row.get("OBJECT_CONFIDENCE") or 0)
    except (TypeError, ValueError):
        return 0.0


def build_reference_image_index(sign_features: list[dict]) -> dict[str, list[dict]]:
    index: dict[str, list[dict]] = {}
    for feature in sign_features:
        props = feature.get("properties") or {}
        for linked_image_id in props.get("LINKED_IMAGE_IDS") or []:
            index.setdefault(str(linked_image_id), []).append(feature)
    return index


def matched_rows(detections: list[dict], sign_features: list[dict], min_confidence: float) -> list[dict]:
    reference_by_image = build_reference_image_index(sign_features)
    output = []
    for detection in detections:
        detected_image_id = image_id(detection)
        if not detected_image_id or detected_image_id not in reference_by_image:
            continue
        if object_confidence(detection) < min_confidence:
            continue
        if not is_signlike_detection(detection):
            continue
        for reference in reference_by_image[detected_image_id]:
            props = reference.get("properties") or {}
            output.append(
                {
                    **detection,
                    "reference_provider": "mapillary",
                    "reference_feature_id": props.get("MAPILLARY_FEATURE_ID"),
                    "reference_object_value": props.get("OBJECT_VALUE"),
                    "reference_first_seen_at": props.get("FIRST_SEEN_AT"),
                    "reference_last_seen_at": props.get("LAST_SEEN_AT"),
                    "reference_linked_image_count": props.get("LINKED_IMAGE_COUNT"),
                    "reference_source_url": props.get("SOURCE_URL"),
                    "reference_geometry": reference.get("geometry"),
                    "match_method": "mapillary_reference_linked_image_id",
                }
            )
    return output


def to_geojson(rows: list[dict]) -> dict:
    features = []
    for row in rows:
        geometry = row.get("reference_geometry")
        props = {
            "PROVIDER": row.get("provider") or row.get("PROVIDER"),
            "IMAGE_ID": image_id(row),
            "DETECTED_OBJECT": row.get("detected_object") or row.get("DETECTED_OBJECT"),
            "OBJECT_CONFIDENCE": object_confidence(row),
            "CROP_PATH": row.get("crop_path") or row.get("CROP_PATH"),
            "REFERENCE_PROVIDER": row.get("reference_provider"),
            "REFERENCE_FEATURE_ID": row.get("reference_feature_id"),
            "REFERENCE_OBJECT_VALUE": row.get("reference_object_value"),
            "REFERENCE_FIRST_SEEN_AT": row.get("reference_first_seen_at"),
            "REFERENCE_LAST_SEEN_AT": row.get("reference_last_seen_at"),
            "REFERENCE_SOURCE_URL": row.get("reference_source_url"),
            "MATCH_METHOD": row.get("match_method"),
        }
        features.append({"type": "Feature", "geometry": geometry, "properties": props})
    return {
        "type": "FeatureCollection",
        "properties": {
            "row_count": len(rows),
            "note": (
                "Spark sign-like detections whose Mapillary image is linked to a parking-related "
                "Mapillary map feature. Prioritize these for OCR/review."
            ),
        },
        "features": features,
    }


def write_csv(rows: list[dict], output_path: Path) -> None:
    fieldnames = [
        "image_id",
        "detected_object",
        "object_confidence",
        "crop_path",
        "source_url",
        "reference_feature_id",
        "reference_object_value",
        "reference_first_seen_at",
        "reference_last_seen_at",
        "reference_linked_image_count",
        "reference_source_url",
        "match_method",
    ]
    with output_path.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--reference-signs", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--min-confidence", type=float, default=0.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_dir = args.run_dir.resolve()
    output_dir = args.output_dir or run_dir / "reference_matches"
    output_dir.mkdir(parents=True, exist_ok=True)

    detections = load_jsonl(run_dir / "detections.jsonl")
    signs = load_geojson(args.reference_signs).get("features", [])
    rows = matched_rows(detections, signs, args.min_confidence)

    (output_dir / "reference_matched_detections.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows)
    )
    (output_dir / "reference_matched_detections.geojson").write_text(json.dumps(to_geojson(rows), indent=2) + "\n")
    write_csv(rows, output_dir / "reference_matched_detections.csv")

    counts: dict[str, int] = {}
    for row in rows:
        key = row.get("reference_object_value") or ""
        counts[key] = counts.get(key, 0) + 1
    print(f"Wrote {output_dir}")
    print(f"Matched detection rows: {len(rows)}")
    print(f"Reference classes: {counts}")


if __name__ == "__main__":
    main()
