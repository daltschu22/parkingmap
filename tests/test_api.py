from __future__ import annotations

from fastapi.testclient import TestClient

from app import APP_VERSION, app


client = TestClient(app)


def test_health_is_lightweight_and_hardened():
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "app_version": APP_VERSION}
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in response.headers["content-security-policy"]


def test_stats_normalizes_missing_ownership():
    response = client.get("/api/stats")

    assert response.status_code == 200
    ownership = response.json()["ownership"]
    assert "null" not in ownership
    assert ownership["Unknown"] > 0


def test_stats_separate_cambridge_point_evidence_from_street_rules():
    response = client.get("/api/stats")

    assert response.status_code == 200
    stats = response.json()
    assert stats["parking_evidence"] == {
        "cambridge_meter_spaces": 3310,
        "cambridge_active_meter_spaces": 2562,
        "cambridge_accessible_spaces": 154,
    }
    assert stats["parking_access"]["unknown"] >= 2643
    assert stats["parking_access"].get("metered_segments_known", 0) == 0
    assert stats["parking_display"] == {
        "metered": 592,
        "open_time_limited": 373,
        "restricted": 1758,
        "unknown": 4364,
    }


def test_large_street_response_is_compressed_and_has_provenance():
    response = client.get("/api/streets", headers={"Accept-Encoding": "gzip"})

    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"
    assert response.headers["cache-control"].startswith("public, max-age=300")
    first_feature = response.json()["features"][0]
    properties = first_feature["properties"]
    assert properties["PARKING_RULE_SOURCE"]
    assert properties["PARKING_SOURCE_URL"].startswith("https://")
    assert "PARKING_CONFIDENCE" in properties
    assert properties["PARKING_DISPLAY_STATUS"] in {
        "metered",
        "open_time_limited",
        "restricted",
        "unknown",
    }


def test_cambridge_search_keeps_street_rules_unknown_and_evidence_separate():
    response = client.get("/api/streets/search?q=Cambridge")

    assert response.status_code == 200
    properties = [feature["properties"] for feature in response.json()["features"]]
    cambridge = [row for row in properties if row.get("MUNICIPALITY") == "Cambridge"]
    assert len(cambridge) == 2643
    assert {row["PARKING_ACCESS"] for row in cambridge} == {"unknown"}
    assert {row["PARKING_DISPLAY_STATUS"] for row in cambridge} == {"unknown"}
    assert all(not row.get("PARKING_RULE_SOURCE") for row in cambridge)
    assert all(row.get("PARKING_EVIDENCE_SOURCE") for row in cambridge)
    assert any(row.get("PARKING_EVIDENCE") == "active_meter_spaces_nearby" for row in cambridge)


def test_search_can_disambiguate_a_street_with_municipality_terms():
    response = client.get("/api/streets/search", params={"q": "Cambridge Otis St"})

    assert response.status_code == 200
    properties = [feature["properties"] for feature in response.json()["features"]]
    assert properties
    assert {row["MUNICIPALITY"] for row in properties} == {"Cambridge"}
    assert {row["STNAME"] for row in properties} == {"Otis St"}
