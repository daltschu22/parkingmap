from __future__ import annotations

import hashlib
import json
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]


def load_json(relative_path: str) -> dict:
    return json.loads((BASE_DIR / relative_path).read_text())


def test_cambridge_active_summary_matches_in_service_features():
    meters = load_json("data/processed/cambridge/metered_spaces.geojson")
    rules = load_json("data/processed/cambridge/parking_rules.json")

    in_service_count = sum(
        1
        for feature in meters["features"]
        if str(feature["properties"].get("STATUS") or "").casefold() == "in service"
    )
    summarized_active_count = sum(
        int(rule.get("active_meter_count_estimate") or 0)
        for rule in rules["streets"].values()
    )

    assert summarized_active_count == in_service_count
    assert all(
        int(rule.get("active_meter_count_estimate") or 0)
        <= int(rule.get("meter_count_estimate") or 0)
        for rule in rules["streets"].values()
    )


def test_sagamore_mixed_ownership_segments_remain_unresolved():
    evidence = load_json("data/processed/medford/segment_parking_evidence.json")

    for object_id in ("95912", "96051"):
        segment = evidence["segments"][object_id]
        assert segment["parking_access"] == "resident_permit_segment_rules_known"
        assert segment["match_level"] == "street_name_partial_rows_unmatched"
        assert segment["confidence"] == "low"


def test_somerville_rules_match_the_fetched_legal_source():
    rules = load_json("data/parking_rules_by_street.json")
    fetch_metadata = load_json("data/traffic-regulations.pdf.metadata.json")
    source_pdf = BASE_DIR / rules["source"]["file"]

    assert rules["parser_version"] >= 2
    assert rules["source"]["edition"] == "June 2026"
    assert rules["source"]["sha256"] == hashlib.sha256(source_pdf.read_bytes()).hexdigest()
    assert rules["source"]["sha256"] == fetch_metadata["sha256"]
    assert rules["source"]["url"] == fetch_metadata["final_url"]
    assert rules["source"]["page_count"] == 190
    assert all(page is not None for page in rules["page_index"].values())


def test_cambridge_meter_matches_retain_distance_and_threshold():
    meters = load_json("data/processed/cambridge/metered_spaces.geojson")
    rules = load_json("data/processed/cambridge/parking_rules.json")
    threshold = rules["meter_match_threshold_meters"]

    matched = [
        feature["properties"]
        for feature in meters["features"]
        if feature["properties"].get("MATCH_CONFIDENCE") == "nearest_street_approx"
    ]
    assert len(matched) + rules["unmatched_meter_spaces"] == len(meters["features"])
    assert matched
    assert all(0 <= props["MATCH_DISTANCE_METERS"] <= threshold for props in matched)
    assert all(props.get("MATCHED_STREET_KEY") for props in matched)
    assert rules["meter_match_distance_meters"]["max"] <= threshold
