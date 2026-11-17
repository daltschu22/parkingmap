"""Build parking coverage summaries and segment-level evidence.

This script uses geometry metadata to improve confidence where the source data
supports it. It does not infer legal parking rules from road geometry alone.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
MEDFORD_STREETS_PATH = DATA_DIR / "processed" / "medford" / "streets.geojson"
MEDFORD_RULES_PATH = (
    DATA_DIR / "processed" / "medford" / "resident_permit_parking_rules.json"
)
MEDFORD_SEGMENT_OUTPUT_PATH = (
    DATA_DIR / "processed" / "medford" / "segment_parking_evidence.json"
)
COVERAGE_OUTPUT_PATH = DATA_DIR / "processed" / "parking_coverage_report.json"


TOKEN_MAP = {
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
    "WAY": "WAY",
    "PARK": "PARK",
    "LINE": "LINE",
}


def normalize_street_name(value: object) -> str:
    if not value:
        return ""
    text = re.sub(r"[^A-Za-z0-9 ]+", " ", str(value).upper())
    text = re.sub(r"\s+", " ", text).strip()
    return " ".join(TOKEN_MAP.get(token, token) for token in text.split())


def load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def endpoint_aliases(value: object) -> set[str]:
    key = normalize_street_name(value)
    aliases = {key} if key else set()
    if key.endswith(" CITY LINE"):
        aliases.add(key.removesuffix(" CITY LINE") + " LINE")
    if key.endswith(" TOWN LINE"):
        aliases.add(key.removesuffix(" TOWN LINE") + " LINE")
    if key.endswith(" LINE") and " " in key:
        aliases.add(key)
    return {alias for alias in aliases if alias}


def row_matches_segment(row: dict, props: dict) -> bool:
    """Only promote an unqualified pair of matching cross streets."""
    endpoints = normalize_street_name(row.get("restriction_text")).split(" TO ")
    if len(endpoints) != 2:
        return False
    start, end = endpoints
    from_names = endpoint_aliases(props.get("FROM_STREET"))
    to_names = endpoint_aliases(props.get("TO_STREET"))
    return (start in from_names and end in to_names) or (end in from_names and start in to_names)


def summarize_rows(rows: list[dict], limit: int = 3) -> str:
    parts = []
    for row in rows[:limit]:
        parts.append(
            row.get("restriction_text") or row.get("permit_type") or "Permit type missing"
        )
    return " | ".join(part for part in parts if part)


def build_medford_segment_evidence() -> dict:
    streets = load_json(MEDFORD_STREETS_PATH)
    rule_rows = load_json(MEDFORD_RULES_PATH).get("rows", [])
    rows_by_street: dict[str, list[dict]] = defaultdict(list)
    for row in rule_rows:
        key = normalize_street_name(row.get("street_name"))
        if key:
            rows_by_street[key].append(row)

    segments = {}
    counts = Counter()
    unique_streets: dict[str, set[str]] = defaultdict(set)

    for feature in streets.get("features", []):
        props = feature.get("properties") or {}
        object_id = str(props.get("MASSDOT_OBJECTID") or "")
        street_key = normalize_street_name(props.get("STNAME"))
        rows = rows_by_street.get(street_key, [])
        ownership = str(props.get("OWNERSHIP") or "").strip().lower()
        partial_rows = [
            row for row in rows if row.get("scope") == "partial_or_segment_specific"
        ]
        full_rows = [row for row in rows if row.get("scope") == "likely_full_street"]
        time_window_rows = [
            row for row in rows if row.get("scope") == "street_level_time_window"
        ]
        matched_partial_rows = [
            row for row in partial_rows if row_matches_segment(row, props)
        ]

        if ownership == "private":
            category = "private_rules_apply"
            confidence = "high"
            match_level = "ownership_private"
            note = "Private street; parking rules are set by owner/signage."
        elif full_rows:
            category = "resident_permit_required"
            confidence = "medium"
            match_level = "street_name_full_row"
            note = "Medford resident-permit source has a street-level row for this street. Confirm posted signs."
        elif time_window_rows:
            category = "resident_permit_time_restricted"
            confidence = "medium"
            match_level = "street_name_time_window"
            note = (
                "Medford documents a resident-permit restriction with scheduled hours "
                "for this street. Check the source detail and posted signs for when it applies."
            )
        elif matched_partial_rows:
            category = "resident_permit_required"
            confidence = "medium"
            match_level = "segment_endpoint_match"
            note = (
                "Medford resident-permit row endpoints match this road segment. "
                "Side-specific signs may still apply."
            )
        elif partial_rows:
            category = "resident_permit_segment_rules_known"
            confidence = "low"
            match_level = "street_name_partial_rows_unmatched"
            note = (
                "Medford has segment-specific permit row(s) for this street, but this centerline "
                "segment did not match the row endpoints."
            )
        elif rows:
            category = "unknown"
            confidence = "none"
            match_level = "unresolved_source_row"
            note = "The permit list has an incomplete row for this street. Check the posted signs."
        else:
            category = "unknown"
            confidence = "none"
            match_level = "none"
            note = "No Medford resident-permit row matched this street in the current source data."

        evidence = {
            "parking_access": category,
            "parking_note": note,
            "confidence": confidence,
            "match_level": match_level,
            "street_key": street_key,
            "rule_count": len(rows),
            "partial_rule_count": len(partial_rows),
            "matched_partial_rule_count": len(matched_partial_rows),
            "rule_summary": summarize_rows(
                matched_partial_rows or full_rows or time_window_rows or rows
            ),
            "matched_rows": matched_partial_rows[:5],
        }
        if object_id:
            segments[object_id] = evidence
        counts[category] += 1
        if street_key:
            unique_streets[category].add(street_key)

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "municipality": "medford",
        "source_files": [
            str(MEDFORD_STREETS_PATH.relative_to(BASE_DIR)),
            str(MEDFORD_RULES_PATH.relative_to(BASE_DIR)),
        ],
        "parser_note": (
            "Segment evidence matches partial resident-permit rows to MassDOT centerline "
            "FROM_STREET/TO_STREET values. Unmatched partial rows are intentionally not applied "
            "to every segment on the street."
        ),
        "segment_count": sum(counts.values()),
        "parking_access_counts": dict(counts),
        "unique_street_counts": {
            category: len(streets)
            for category, streets in sorted(unique_streets.items())
        },
        "segments": segments,
    }


def build_coverage_report(features: list[dict]) -> dict:
    coverage = {}
    access_counts = defaultdict(Counter)
    active_meter_segments = 0
    for feature in features:
        props = feature["properties"]
        city = props["MUNICIPALITY"].lower()
        counts = coverage.setdefault(
            city,
            {
                "block_specific_rule": 0,
                "street_level_evidence_only": 0,
                "unresolved": 0,
            },
        )
        access = props["PARKING_ACCESS"]
        access_counts[city][access] += 1
        if props["PARKING_RULE_MATCH_LEVEL"] == "segment_endpoint_match":
            counts["block_specific_rule"] += 1
        elif access == "unknown":
            counts["unresolved"] += 1
        else:
            counts["street_level_evidence_only"] += 1
        if props.get("PARKING_CAMBRIDGE_ACTIVE_METER_COUNT_ESTIMATE", 0) > 0:
            active_meter_segments += 1
    if not {"somerville", "medford", "cambridge"}.issubset(coverage):
        raise ValueError("Missing municipality in the mapped street network")
    for counts in coverage.values():
        counts["total_segments"] = sum(counts.values())
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "note": (
            "Each mapped centerline is counted once. A block-specific rule has matched endpoints. "
            "Street-level evidence includes citywide baselines and ownership. Unresolved segments "
            "may have individual mapped parking spaces or incomplete source rows."
        ),
        "municipalities": dict(access_counts),
        "segment_rule_coverage": coverage,
        "cambridge_segments_with_active_meter_evidence": active_meter_segments,
    }


def main() -> None:
    MEDFORD_SEGMENT_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    COVERAGE_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    medford_evidence = build_medford_segment_evidence()
    MEDFORD_SEGMENT_OUTPUT_PATH.write_text(
        json.dumps(medford_evidence, indent=2) + "\n"
    )
    from app import get_enriched_streets

    coverage_report = build_coverage_report(get_enriched_streets()["features"])
    COVERAGE_OUTPUT_PATH.write_text(json.dumps(coverage_report, indent=2) + "\n")

    print(f"Wrote {MEDFORD_SEGMENT_OUTPUT_PATH}")
    print(f"Wrote {COVERAGE_OUTPUT_PATH}")
    print(f"Medford segments: {medford_evidence['segment_count']}")
    print(f"Medford parking counts: {medford_evidence['parking_access_counts']}")


if __name__ == "__main__":
    main()
