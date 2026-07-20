"""Consolidate image-derived parking evidence into an inspectable dataset."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def evidence_type(label: str) -> str:
    text = label.lower()
    if "meter" in text or "pay station" in text:
        return "meter"
    if "parking space" in text or "parking bay" in text or "parking stall" in text:
        return "parking_space_marking"
    if "curb" in text or "road marking" in text or "bike lane" in text or "crosswalk" in text:
        return "curb_or_road_marking"
    if "sign" in text:
        return "sign"
    if "hydrant" in text:
        return "hydrant"
    if "driveway" in text:
        return "driveway"
    return "other"


def row_key(row: dict) -> tuple[str, str, str]:
    return (
        str(row.get("image_id") or row.get("IMAGE_ID") or ""),
        str(row.get("detected_object") or row.get("DETECTED_OBJECT") or ""),
        str(row.get("crop_path") or row.get("CROP_PATH") or ""),
    )


def write_csv(rows: list[dict], path: Path, fieldnames: list[str]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def compact_detection(row: dict) -> dict:
    label = row.get("detected_object") or ""
    return {
        "image_id": row.get("image_id"),
        "street_name": row.get("street_name"),
        "captured_at": row.get("captured_at"),
        "heading": row.get("heading"),
        "lat": row.get("sample_lat"),
        "lng": row.get("sample_lng"),
        "evidence_type": evidence_type(label),
        "detected_object": label,
        "object_confidence": row.get("object_confidence"),
        "bbox_xyxy": row.get("bbox_xyxy"),
        "crop_path": row.get("crop_path"),
        "image_path": row.get("image_path"),
        "source_url": row.get("source_url"),
    }


def geojson_from_rows(rows: list[dict]) -> dict:
    features = []
    for row in rows:
        geometry = None
        if isinstance(row.get("lat"), (int, float)) and isinstance(row.get("lng"), (int, float)):
            geometry = {"type": "Point", "coordinates": [row["lng"], row["lat"]]}
        features.append(
            {
                "type": "Feature",
                "geometry": geometry,
                "properties": {key.upper(): value for key, value in row.items() if key not in {"lat", "lng"}},
            }
        )
    return {"type": "FeatureCollection", "features": features}


def build_dataset(args: argparse.Namespace) -> dict:
    run_dir = args.run_dir.resolve()
    output_dir = args.output_dir or run_dir / "dataset"
    output_dir.mkdir(parents=True, exist_ok=True)

    broad_rows = load_jsonl(run_dir / "detections.jsonl")
    reference_rows = load_jsonl(run_dir / "reference_matches" / "reference_matched_detections.jsonl")
    ocr_rows = load_jsonl(run_dir / "original_refs" / "ocr_signs_original_refs.jsonl")
    rule_candidate_summary = load_json(output_dir / "parking_rule_candidates_summary.json")

    compact_rows = [compact_detection(row) for row in broad_rows]
    write_csv(
        compact_rows,
        output_dir / "parking_evidence_candidates.csv",
        [
            "image_id",
            "street_name",
            "captured_at",
            "heading",
            "lat",
            "lng",
            "evidence_type",
            "detected_object",
            "object_confidence",
            "bbox_xyxy",
            "crop_path",
            "image_path",
            "source_url",
        ],
    )
    (output_dir / "parking_evidence_candidates.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in compact_rows)
    )
    (output_dir / "parking_evidence_candidates.geojson").write_text(
        json.dumps(geojson_from_rows(compact_rows), indent=2) + "\n"
    )

    reference_fieldnames = [
        "image_id",
        "street_name",
        "detected_object",
        "object_confidence",
        "crop_path",
        "reference_feature_id",
        "reference_object_value",
        "reference_first_seen_at",
        "reference_last_seen_at",
        "match_method",
    ]
    write_csv(reference_rows, output_dir / "reference_matched_signs.csv", reference_fieldnames)
    (output_dir / "reference_matched_signs.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in reference_rows)
    )

    ocr_fieldnames = [
        "image_id",
        "detected_object",
        "object_confidence",
        "ocr_classification",
        "ocr_text",
        "ocr_image_path",
        "crop_path",
        "source_url",
    ]
    write_csv(ocr_rows, output_dir / "original_resolution_sign_ocr.csv", ocr_fieldnames)
    (output_dir / "original_resolution_sign_ocr.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in ocr_rows)
    )

    summary = {
        "run_dir": str(run_dir),
        "broad_detection_count": len(broad_rows),
        "broad_image_count": len({row.get("image_id") for row in broad_rows}),
        "broad_detection_counts": dict(Counter(row.get("detected_object") for row in broad_rows)),
        "evidence_type_counts": dict(Counter(row["evidence_type"] for row in compact_rows)),
        "reference_match_count": len(reference_rows),
        "reference_match_counts": dict(Counter(row.get("reference_object_value") for row in reference_rows)),
        "original_resolution_ocr_count": len(ocr_rows),
        "original_resolution_ocr_classifications": dict(Counter(row.get("ocr_classification") for row in ocr_rows)),
        "rule_candidate_count": rule_candidate_summary.get("candidate_count", 0),
        "rule_candidate_parking_allowed_counts": rule_candidate_summary.get("parking_allowed_counts", {}),
        "rule_candidate_type_counts": rule_candidate_summary.get("rule_type_counts", {}),
        "rule_candidate_confidence_counts": rule_candidate_summary.get("confidence_counts", {}),
        "rule_candidate_tow_zone_count": rule_candidate_summary.get("tow_zone_count", 0),
        "rule_candidate_street_sweeping_count": rule_candidate_summary.get("street_sweeping_count", 0),
        "rule_candidate_permit_required_counts": rule_candidate_summary.get("permit_required_counts", {}),
        "review_pages": {
            "all_detections": "../review_all/index.html",
            "reference_matches": "../review_reference_matches/index.html",
            "original_resolution_refs": "../review_original_refs/index.html",
            "interpreted_rule_candidates": "parking_rule_candidates.html",
        },
        "notes": [
            "Image detections are evidence candidates, not legal parking rules.",
            "Mapillary traffic-sign reference classes provide coarse sign meaning.",
            "Original-resolution OCR is promising but still noisy; keep OCR text review-gated.",
        ],
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (output_dir / "README.md").write_text(
        "# Parking Evidence Dataset\n\n"
        "This folder consolidates the current image-derived parking evidence run.\n\n"
        "Start with `summary.json`, then inspect:\n\n"
        "- `parking_evidence_candidates.csv` for all detections.\n"
        "- `reference_matched_signs.csv` for signs that matched Mapillary parking-sign features.\n"
        "- `original_resolution_sign_ocr.csv` for high-resolution sign OCR attempts.\n"
        "- `parking_rule_candidates.csv` and `parking_rule_candidates.html` for review-gated interpreted rule candidates.\n"
        "- `../review_all/index.html`, `../review_reference_matches/index.html`, and "
        "`../review_original_refs/index.html` for visual review.\n\n"
        "Treat this as a review dataset. Do not convert rows directly into parking rules without validation.\n"
    )
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = build_dataset(args)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
