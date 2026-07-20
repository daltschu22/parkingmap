"""Interpret image-derived sign evidence into review-gated parking rule candidates."""
from __future__ import annotations

import argparse
import csv
import html
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DAY_ALIASES = {
    "MON": "monday",
    "MONDAY": "monday",
    "TUE": "tuesday",
    "TUES": "tuesday",
    "TUESDAY": "tuesday",
    "WED": "wednesday",
    "WEDS": "wednesday",
    "WEDNESDAY": "wednesday",
    "THU": "thursday",
    "THUR": "thursday",
    "THURS": "thursday",
    "THURSDAY": "thursday",
    "FRI": "friday",
    "FRIDAY": "friday",
    "SAT": "saturday",
    "SATURDAY": "saturday",
    "SUN": "sunday",
    "SUNDAY": "sunday",
}
DAY_ORDER = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def normalize_text(value: str | None) -> str:
    text = str(value or "").upper()
    text = text.replace("0", "O")
    text = re.sub(r"[^A-Z0-9:.' -]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def compact_text(value: str | None) -> str:
    return re.sub(r"[^A-Z0-9]+", "", normalize_text(value))


def row_id(row: dict, prefix: str) -> str:
    image_id = row.get("image_id") or row.get("IMAGE_ID") or "unknown-image"
    crop = Path(row.get("crop_path") or row.get("CROP_PATH") or "unknown-crop").stem
    reference_id = row.get("reference_feature_id") or row.get("REFERENCE_FEATURE_ID") or ""
    return "-".join(part for part in [prefix, str(image_id), crop, str(reference_id)] if part)


def image_key(row: dict) -> str:
    return str(row.get("image_id") or row.get("IMAGE_ID") or "")


def sign_location(row: dict) -> tuple[float | None, float | None]:
    geometry = row.get("reference_geometry") or {}
    coords = geometry.get("coordinates") or []
    if geometry.get("type") == "Point" and len(coords) >= 2:
        return coords[1], coords[0]
    lat = row.get("sample_lat")
    lng = row.get("sample_lng")
    if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
        return lat, lng
    return None, None


def extract_days(text: str) -> list[str]:
    tokens = re.findall(r"\b[A-Z]{3,9}\b", normalize_text(text))
    found = []
    for token in tokens:
        day = DAY_ALIASES.get(token)
        if day and day not in found:
            found.append(day)
    return sorted(found, key=DAY_ORDER.index)


def parse_time_token(hour: str, minute: str | None, meridiem: str) -> str:
    value = int(hour)
    if value == 12:
        value = 0
    if meridiem == "PM":
        value += 12
    return f"{value:02d}:{int(minute or 0):02d}"


def extract_time_windows(text: str) -> list[dict]:
    normalized = normalize_text(text)
    pattern = re.compile(
        r"\b(?P<h1>\d{1,2})(?::(?P<m1>\d{2}))?\s*(?P<a1>AM|PM)?\s*[-TO]+\s*"
        r"(?P<h2>\d{1,2})(?::(?P<m2>\d{2}))?\s*(?P<a2>AM|PM)\b"
    )
    windows = []
    for match in pattern.finditer(normalized):
        a1 = match.group("a1") or match.group("a2")
        windows.append(
            {
                "start": parse_time_token(match.group("h1"), match.group("m1"), a1),
                "end": parse_time_token(match.group("h2"), match.group("m2"), match.group("a2")),
                "raw": match.group(0),
            }
        )
    return windows


def infer_from_reference(object_value: str | None) -> dict[str, Any]:
    value = str(object_value or "")
    inferred: dict[str, Any] = {
        "parking_allowed": "unknown",
        "rule_type": "unknown",
        "source_sign_class": value,
        "no_stopping": False,
        "tow_zone": False,
        "street_sweeping": False,
        "days": [],
        "time_windows": [],
        "ocr_text": "",
        "ocr_text_normalized": "",
        "permit_required": "unknown",
    }
    if "no-parking-or-no-stopping" in value:
        inferred.update(
            {
                "parking_allowed": False,
                "rule_type": "no_parking_or_no_stopping",
                "no_stopping": True,
                "restriction_summary": "Mapillary classified this as no parking or no stopping.",
            }
        )
    elif "no-parking" in value:
        inferred.update(
            {
                "parking_allowed": False,
                "rule_type": "no_parking",
                "restriction_summary": "Mapillary classified this as no parking.",
            }
        )
    elif "parking-restrictions" in value:
        inferred.update(
            {
                "parking_allowed": "conditional",
                "rule_type": "parking_restriction",
                "restriction_summary": "Mapillary classified this as a parking restriction sign.",
            }
        )
    elif "information-parking" in value or "information--parking" in value:
        inferred.update(
            {
                "parking_allowed": "conditional",
                "rule_type": "parking_information",
                "restriction_summary": "Mapillary classified this as parking information.",
            }
        )
    return inferred


def infer_from_ocr(text: str | None) -> dict[str, Any]:
    normalized = normalize_text(text)
    compact = compact_text(text)
    has_no_parking = "NOPARK" in compact or "NOFPARK" in compact
    has_tow = "TOW" in compact
    has_sweep = "SWEEP" in compact or "SWEEPIG" in compact or "SWEEPIC" in compact or "SNEEPIRG" in compact
    has_permit = "PERMIT" in compact or "RESIDENT" in compact or "RES" in compact
    days = extract_days(normalized)
    time_windows = extract_time_windows(normalized)

    inferred: dict[str, Any] = {
        "ocr_text_normalized": normalized,
        "ocr_no_parking": has_no_parking,
        "tow_zone": has_tow,
        "street_sweeping": has_sweep,
        "permit_required": True if has_permit else "unknown",
        "days": days,
        "time_windows": time_windows,
        "rule_type": "unknown",
        "parking_allowed": "unknown",
    }
    if has_no_parking and has_sweep:
        inferred.update(
            {
                "parking_allowed": False if not time_windows else "conditional",
                "rule_type": "street_sweeping_no_parking",
                "restriction_summary": "OCR suggests no parking for street sweeping.",
            }
        )
    elif has_sweep and has_tow:
        inferred.update(
            {
                "parking_allowed": "conditional",
                "rule_type": "street_sweeping_tow_zone",
                "restriction_summary": "OCR suggests a street-sweeping tow-zone parking restriction.",
            }
        )
    elif has_no_parking:
        inferred.update(
            {
                "parking_allowed": False if not time_windows else "conditional",
                "rule_type": "no_parking",
                "restriction_summary": "OCR suggests no parking.",
            }
        )
    elif has_tow:
        inferred.update(
            {
                "parking_allowed": "conditional",
                "rule_type": "tow_zone",
                "restriction_summary": "OCR suggests a tow-zone parking restriction.",
            }
        )
    elif has_permit:
        inferred.update(
            {
                "parking_allowed": "conditional",
                "rule_type": "permit_parking",
                "restriction_summary": "OCR suggests permit parking.",
            }
        )
    return inferred


def confidence_level(row: dict, interpreted: dict) -> str:
    has_reference = bool(interpreted.get("source_sign_class"))
    has_useful_ocr = interpreted.get("ocr_no_parking") or interpreted.get("tow_zone") or interpreted.get("street_sweeping")
    ocr_text = str(interpreted.get("ocr_text") or "")
    if has_reference and has_useful_ocr:
        return "medium_review_required"
    if has_reference:
        return "medium_low_review_required"
    if has_useful_ocr and len(ocr_text) >= 6:
        return "low_review_required"
    return "very_low_review_required"


def merge_interpretations(reference_rule: dict, ocr_rule: dict) -> dict:
    merged = dict(reference_rule)
    for key, value in ocr_rule.items():
        if value in (None, "", [], {}, "unknown", False):
            if key not in merged:
                merged[key] = value
            continue
        if key in {"parking_allowed", "rule_type", "restriction_summary"} and merged.get(key) not in (None, "", "unknown"):
            merged[f"ocr_{key}"] = value
        else:
            merged[key] = value
    return merged


def build_reference_rules(reference_rows: list[dict]) -> list[dict]:
    candidates = []
    for row in reference_rows:
        lat, lng = sign_location(row)
        rule = infer_from_reference(row.get("reference_object_value"))
        rule.update(
            {
                "candidate_id": row_id(row, "ref"),
                "review_required": True,
                "evidence_source": "mapillary_reference",
                "image_id": image_key(row),
                "street_name": row.get("street_name"),
                "captured_at": row.get("captured_at"),
                "lat": lat,
                "lng": lng,
                "detected_object": row.get("detected_object"),
                "object_confidence": row.get("object_confidence"),
                "reference_feature_id": row.get("reference_feature_id"),
                "reference_source_url": row.get("reference_source_url"),
                "image_source_url": row.get("source_url"),
                "crop_path": row.get("crop_path"),
                "ocr_text": "",
                "days": [],
                "time_windows": [],
            }
        )
        rule["confidence"] = confidence_level(row, rule)
        candidates.append(rule)
    return candidates


def build_ocr_rules(ocr_rows: list[dict], reference_by_image: dict[str, list[dict]]) -> list[dict]:
    candidates = []
    for row in ocr_rows:
        image_id = image_key(row)
        reference_rows = reference_by_image.get(image_id) or [None]
        ocr_rule = infer_from_ocr(row.get("ocr_text"))
        for reference_row in reference_rows:
            if reference_row:
                reference_rule = infer_from_reference(reference_row.get("reference_object_value"))
                interpreted = merge_interpretations(reference_rule, ocr_rule)
                lat, lng = sign_location(reference_row)
                reference_feature_id = reference_row.get("reference_feature_id")
                reference_source_url = reference_row.get("reference_source_url")
            else:
                interpreted = ocr_rule
                lat = row.get("sample_lat")
                lng = row.get("sample_lng")
                reference_feature_id = ""
                reference_source_url = ""

            interpreted.update(
                {
                    "candidate_id": row_id(row, "ocr") + (f"-{reference_feature_id}" if reference_feature_id else ""),
                    "review_required": True,
                    "evidence_source": "original_resolution_ocr",
                    "image_id": image_id,
                    "street_name": row.get("street_name"),
                    "captured_at": row.get("captured_at"),
                    "lat": lat,
                    "lng": lng,
                    "detected_object": row.get("detected_object"),
                    "object_confidence": row.get("object_confidence"),
                    "reference_feature_id": reference_feature_id,
                    "reference_source_url": reference_source_url,
                    "image_source_url": row.get("source_url"),
                    "crop_path": row.get("crop_path"),
                    "ocr_image_path": row.get("ocr_image_path"),
                    "ocr_text": row.get("ocr_text") or "",
                }
            )
            interpreted["confidence"] = confidence_level(row, interpreted)
            candidates.append(interpreted)
    return candidates


def dedupe_candidates(candidates: list[dict]) -> list[dict]:
    seen = set()
    output = []
    for candidate in candidates:
        key = (
            candidate.get("evidence_source"),
            candidate.get("image_id"),
            candidate.get("crop_path"),
            candidate.get("reference_feature_id"),
            candidate.get("ocr_text"),
        )
        if key in seen:
            continue
        seen.add(key)
        output.append(candidate)
    return output


def finalize_candidate(candidate: dict) -> dict:
    defaults = {
        "candidate_id": "",
        "review_required": True,
        "confidence": "very_low_review_required",
        "parking_allowed": "unknown",
        "rule_type": "unknown",
        "restriction_summary": "",
        "ocr_restriction_summary": "",
        "days": [],
        "time_windows": [],
        "permit_required": "unknown",
        "tow_zone": False,
        "street_sweeping": False,
        "no_stopping": False,
        "source_sign_class": "",
        "ocr_text": "",
        "ocr_text_normalized": "",
        "ocr_no_parking": False,
        "evidence_source": "",
        "image_id": "",
        "street_name": "",
        "lat": None,
        "lng": None,
        "detected_object": "",
        "object_confidence": None,
        "reference_feature_id": "",
        "reference_source_url": "",
        "image_source_url": "",
        "crop_path": "",
        "ocr_image_path": "",
    }
    finalized = {**defaults, **candidate}
    finalized["days"] = finalized.get("days") or []
    finalized["time_windows"] = finalized.get("time_windows") or []
    finalized["review_required"] = True
    return finalized


def geojson_from_rules(rows: list[dict]) -> dict:
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


def write_csv(rows: list[dict], path: Path) -> None:
    fieldnames = [
        "candidate_id",
        "review_required",
        "confidence",
        "parking_allowed",
        "rule_type",
        "restriction_summary",
        "ocr_restriction_summary",
        "days",
        "time_windows",
        "permit_required",
        "tow_zone",
        "street_sweeping",
        "no_stopping",
        "source_sign_class",
        "ocr_text",
        "ocr_text_normalized",
        "ocr_no_parking",
        "evidence_source",
        "image_id",
        "street_name",
        "lat",
        "lng",
        "detected_object",
        "object_confidence",
        "reference_feature_id",
        "reference_source_url",
        "image_source_url",
        "crop_path",
        "ocr_image_path",
    ]
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            serialized = dict(row)
            for key in ("days", "time_windows"):
                serialized[key] = json.dumps(serialized.get(key) or [])
            writer.writerow(serialized)


def write_html(rows: list[dict], path: Path, summary: dict) -> None:
    table_rows = []
    for row in rows:
        ref_url = row.get("reference_source_url") or ""
        image_url = row.get("image_source_url") or ""
        links = []
        if image_url:
            links.append(f'<a href="{html.escape(str(image_url))}">image</a>')
        if ref_url:
            links.append(f'<a href="{html.escape(str(ref_url))}">reference</a>')
        table_rows.append(
            "<tr>"
            f"<td>{html.escape(str(row.get('parking_allowed')))}</td>"
            f"<td>{html.escape(str(row.get('rule_type')))}</td>"
            f"<td>{html.escape(str(row.get('confidence')))}</td>"
            f"<td>{html.escape(json.dumps(row.get('days') or []))}</td>"
            f"<td>{html.escape(json.dumps(row.get('time_windows') or []))}</td>"
            f"<td>{html.escape(str(row.get('permit_required')))}</td>"
            f"<td>{html.escape(str(row.get('tow_zone')))}</td>"
            f"<td>{html.escape(str(row.get('street_sweeping')))}</td>"
            f"<td>{html.escape(str(row.get('source_sign_class')))}</td>"
            f"<td>{html.escape(str(row.get('ocr_text') or ''))}</td>"
            f"<td>{' / '.join(links)}</td>"
            "</tr>"
        )
    counts = "".join(
        f"<li>{html.escape(str(key))}: {value}</li>"
        for key, value in summary.get("rule_type_counts", {}).items()
    )
    path.write_text(
        """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Parking Rule Candidates</title>
<style>
body { margin: 0; font-family: system-ui, sans-serif; background: #111827; color: #f9fafb; }
main { padding: 20px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { border-bottom: 1px solid #374151; padding: 8px; vertical-align: top; text-align: left; }
th { position: sticky; top: 0; background: #1f2937; }
a { color: #93c5fd; }
code { color: #bfdbfe; }
</style>
</head>
<body><main>
<h1>Parking Rule Candidates</h1>
<p>All rows are <code>review_required</code>; these are image-derived candidate rules, not legal determinations.</p>
<ul>"""
        + counts
        + """</ul>
<table>
<thead><tr>
<th>Allowed</th><th>Rule Type</th><th>Confidence</th><th>Days</th><th>Times</th>
<th>Permit</th><th>Tow</th><th>Sweeping</th><th>Sign Class</th><th>OCR Text</th><th>Links</th>
</tr></thead>
<tbody>"""
        + "\n".join(table_rows)
        + """
</tbody></table>
</main></body></html>
"""
    )


def build_rule_candidates(args: argparse.Namespace) -> dict:
    run_dir = args.run_dir.resolve()
    output_dir = args.output_dir or run_dir / "dataset"
    output_dir.mkdir(parents=True, exist_ok=True)

    reference_rows = load_jsonl(run_dir / "reference_matches" / "reference_matched_detections.jsonl")
    ocr_rows = load_jsonl(run_dir / "original_refs" / "ocr_signs_original_refs.jsonl")
    reference_by_image: dict[str, list[dict]] = {}
    for row in reference_rows:
        reference_by_image.setdefault(image_key(row), []).append(row)

    candidates = [
        finalize_candidate(candidate)
        for candidate in dedupe_candidates(build_reference_rules(reference_rows) + build_ocr_rules(ocr_rows, reference_by_image))
    ]
    (output_dir / "parking_rule_candidates.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in candidates)
    )
    (output_dir / "parking_rule_candidates.geojson").write_text(
        json.dumps(geojson_from_rules(candidates), indent=2) + "\n"
    )
    write_csv(candidates, output_dir / "parking_rule_candidates.csv")

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "run_dir": str(run_dir),
        "candidate_count": len(candidates),
        "parking_allowed_counts": dict(Counter(str(row.get("parking_allowed")) for row in candidates)),
        "rule_type_counts": dict(Counter(str(row.get("rule_type")) for row in candidates)),
        "confidence_counts": dict(Counter(str(row.get("confidence")) for row in candidates)),
        "tow_zone_count": sum(1 for row in candidates if row.get("tow_zone") is True),
        "street_sweeping_count": sum(1 for row in candidates if row.get("street_sweeping") is True),
        "permit_required_counts": dict(Counter(str(row.get("permit_required")) for row in candidates)),
        "review_required_count": sum(1 for row in candidates if row.get("review_required") is True),
        "notes": [
            "All interpreted rules are review-gated image-derived candidates.",
            "Mapillary sign classes are used for coarse rule type.",
            "OCR text is noisy and used only to add candidate flags such as tow-zone or street-sweeping.",
            "Missing days/time windows means the image evidence did not reliably expose them.",
        ],
    }
    (output_dir / "parking_rule_candidates_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    write_html(candidates, output_dir / "parking_rule_candidates.html", summary)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    print(json.dumps(build_rule_candidates(args), indent=2))


if __name__ == "__main__":
    main()
