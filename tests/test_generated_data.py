from __future__ import annotations

import hashlib
import json
from collections import Counter
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


def test_public_parking_facilities_have_valid_shared_grain_and_sources():
    facilities = load_json("data/processed/public_parking_facilities.geojson")
    features = facilities["features"]
    ids = [feature["properties"]["FACILITY_ID"] for feature in features]

    assert len(features) == 24
    assert len(ids) == len(set(ids))
    assert Counter(
        feature["properties"]["MUNICIPALITY"] for feature in features
    ) == {"Cambridge": 11, "Somerville": 13}
    assert Counter(
        feature["properties"]["FACILITY_TYPE"]
        for feature in features
        if feature["properties"]["MUNICIPALITY"] == "Cambridge"
    ) == {"Municipal Parking Lot": 9, "Municipal Parking Garage": 2}
    assert all(
        feature["properties"]["SOURCE_URL"].startswith("https://")
        for feature in features
    )
    assert all(
        -71.20 < feature["geometry"]["coordinates"][0] < -70.95
        and 42.30 < feature["geometry"]["coordinates"][1] < 42.50
        for feature in features
    )
    by_id = {feature["properties"]["FACILITY_ID"]: feature for feature in features}
    lot_5 = next(
        feature
        for feature in by_id.values()
        if feature["properties"]["NAME"] == "Municipal Lot 5"
    )
    assert lot_5["properties"]["ADDRESS"] == "84 Bishop Allen Drive"
    assert lot_5["properties"]["GIS_ADDRESS"] == "84 Norfolk Street"

    raw_sources = {
        "cambridge_details": (
            "data/raw/cambridge/public-parking.html",
            "data/raw/cambridge/public-parking.html.metadata.json",
        ),
        "somerville_authority_page": (
            "data/raw/somerville/parking-department.html",
            "data/raw/somerville/parking-department.html.metadata.json",
        ),
        "somerville_lots": (
            "data/raw/somerville/municipal-parking-lots.kml",
            "data/raw/somerville/municipal-parking-lots.kml.metadata.json",
        ),
    }
    for source_key, (raw_path, metadata_path) in raw_sources.items():
        raw_content = (BASE_DIR / raw_path).read_bytes()
        metadata = load_json(metadata_path)
        digest = hashlib.sha256(raw_content).hexdigest()
        assert facilities["properties"]["sources"][source_key]["sha256"] == digest
        assert metadata["sha256"] == digest

    authority_page = (BASE_DIR / raw_sources["somerville_authority_page"][0]).read_text()
    assert "1Vs3VLhrTWksBWwmPl6lA-fHoPbzRH5Y" in authority_page


def test_medford_rules_match_the_fetched_official_source_and_keep_row_volume():
    rules = load_json("data/processed/medford/resident_permit_parking_rules.json")
    metadata = load_json(
        "data/raw/medford/resident-permit-parking-streets.pdf.metadata.json"
    )
    source_pdf = BASE_DIR / rules["source"]["file"]

    assert rules["row_count"] == 191
    assert rules["street_count"] == 181
    assert rules["source"]["sha256"] == hashlib.sha256(source_pdf.read_bytes()).hexdigest()
    assert rules["source"]["sha256"] == metadata["sha256"]
    assert rules["source"]["url"] == metadata["final_url"]


def test_medford_time_windows_are_not_reported_as_unconditional_permit_rules():
    evidence = load_json("data/processed/medford/segment_parking_evidence.json")
    counts = evidence["parking_access_counts"]

    assert counts["resident_permit_time_restricted"] == 104
    assert counts["resident_permit_required"] == 136
