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


def endpoint_matches_text(endpoint: object, text_key: str) -> bool:
    return any(alias and alias in text_key for alias in endpoint_aliases(endpoint))


def is_simple_endpoint_rule(row: dict) -> bool:
    """Return whether a partial rule can safely map to one whole centerline segment.

    Complex rows with multiple ranges, ownership changes, address ranges, sides,
    or distance clauses need curb geometry and must remain unresolved.
    """
    raw_text = str(row.get("restriction_text") or row.get("raw_text") or "")
    normalized = normalize_street_name(raw_text)
    if len(re.findall(r"\bTO\b", normalized)) != 1:
        return False

    complex_scope_patterns = (
        r"#\s*\d",
        r"\b(?:NORTH|SOUTH|EAST|WEST|ODD|EVEN) SIDE\b",
        r"\bBOTH SIDES\b",
        r"\bIN FRONT OF\b",
        r"\bPUBLIC\b",
        r"\bPRIVATE(?: WAY)?\b",
        r"\b(?:RESIDENTIAL|NON RESIDENTIAL) USES\b",
        r"\b(?:FEET|FOOT|FT)\b",
        r"\bONLY\b",
    )
    return not any(re.search(pattern, normalized) for pattern in complex_scope_patterns)


def row_matches_segment(row: dict, props: dict) -> bool:
    """Return True when a partial rule row appears to match this centerline segment.

    Most Medford partial rows are phrased as "A to B" while MassDOT centerlines
    expose `FROM_STREET` and `TO_STREET`. We require both segment endpoints to
    appear in the row text to avoid broad street-level overmatching.
    """
    if not is_simple_endpoint_rule(row):
        return False

    row_text = normalize_street_name(
        " ".join(
            str(row.get(field) or "") for field in ("restriction_text", "raw_text")
        )
    )
    from_street = props.get("FROM_STREET")
    to_street = props.get("TO_STREET")
    if not row_text or not from_street or not to_street:
        return False

    from_match = endpoint_matches_text(from_street, row_text)
    to_match = endpoint_matches_text(to_street, row_text)
    return from_match and to_match


def summarize_rows(rows: list[dict], limit: int = 3) -> str:
    parts = []
    for row in rows[:limit]:
        parts.append(
            row.get("restriction_text") or row.get("permit_type") or "Resident"
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
        full_rows = [
            row for row in rows if row.get("scope") != "partial_or_segment_specific"
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
            "rule_summary": summarize_rows(matched_partial_rows or full_rows or rows),
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


def build_coverage_report(medford_evidence: dict) -> dict:
    counts = {"medford": medford_evidence["parking_access_counts"]}
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "note": (
            "Coverage counts summarize current evidence-backed classification. Unknown means "
            "the loaded sources do not determine a rule; it does not mean parking is unrestricted."
        ),
        "municipalities": counts,
    }


def main() -> None:
    MEDFORD_SEGMENT_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    COVERAGE_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    medford_evidence = build_medford_segment_evidence()
    MEDFORD_SEGMENT_OUTPUT_PATH.write_text(
        json.dumps(medford_evidence, indent=2) + "\n"
    )
    coverage_report = build_coverage_report(medford_evidence)
    COVERAGE_OUTPUT_PATH.write_text(json.dumps(coverage_report, indent=2) + "\n")

    print(f"Wrote {MEDFORD_SEGMENT_OUTPUT_PATH}")
    print(f"Wrote {COVERAGE_OUTPUT_PATH}")
    print(f"Medford segments: {medford_evidence['segment_count']}")
    print(f"Medford parking counts: {medford_evidence['parking_access_counts']}")


if __name__ == "__main__":
    main()
