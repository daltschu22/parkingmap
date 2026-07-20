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
