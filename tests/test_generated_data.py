from __future__ import annotations

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
