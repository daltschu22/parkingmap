from __future__ import annotations

import pytest

from app import _classify_cambridge_parking_access
from build_cambridge_data import is_active_meter_status, summarize_distances
from build_medford_streets import ownership_details
from build_parking_coverage import row_matches_segment


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("In Service", True),
        ("in service", True),
        ("Out of Service", False),
        ("Temp Out of Service", False),
        ("Permanently Removed", False),
        ("Proposed", False),
        (None, False),
    ],
)
def test_active_meter_status_is_an_explicit_allowlist(status, expected):
    assert is_active_meter_status(status) is expected


def test_cambridge_does_not_fallback_to_inactive_meter_count():
    category, note = _classify_cambridge_parking_access(
        {"STNAME": "Bennett Street", "OWNERSHIP": "Unknown"},
        {
            "BENNETT ST": {
                "active_meter_count_estimate": 0,
                "meter_count_estimate": 5,
            }
        },
    )

    assert category == "inactive_metered_segments_known"
    assert "no active meters" in note.lower()


def test_simple_endpoint_rule_can_match_a_whole_centerline_segment():
    row = {
        "restriction_text": "Boston Avenue to Somerville Line",
        "raw_text": "Adams Street Boston Avenue to Somerville Line Resident",
    }
    segment = {
        "FROM_STREET": "BOSTON AVENUE",
        "TO_STREET": "SOMERVILLE CITY LINE",
    }

    assert row_matches_segment(row, segment)


def test_mixed_public_private_rule_is_not_promoted_to_whole_segment():
    row = {
        "restriction_text": (
            "#6 - 31 Sagamore: PUBLIC High St to Ravine "
            "PRIVATE WAY Ravine to Mystic Valley Parkway"
        ),
        "raw_text": (
            "Sagamore Avenue #6 - 31 Sagamore: PUBLIC High St to Ravine "
            "PRIVATE WAY Ravine to Mystic Valley Parkway Resident"
        ),
    }
    segment = {
        "FROM_STREET": "HIGH STREET",
        "TO_STREET": "MYSTIC VALLEY PARKWAY",
    }

    assert not row_matches_segment(row, segment)


def test_distance_summary_is_deterministic_and_keeps_quality_tail():
    assert summarize_distances([1, 2, 3, 4, 100]) == {
        "min": 1,
        "mean": 22,
        "median": 3,
        "p95": 100,
        "max": 100,
    }
    assert summarize_distances([]) is None


def test_medford_ownership_inference_exposes_source_and_confidence():
    assert ownership_details(14, None) == (
        "Private",
        "MassDOT FACILITY code 14",
        "medium",
    )
    assert ownership_details(None, "2") == (
        "Public",
        "MassDOT JURISDICTN code 2",
        "medium",
    )
    assert ownership_details(None, None) == (
        "Unknown",
        "Not resolved from MassDOT road attributes",
        "none",
    )
