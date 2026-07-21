from __future__ import annotations

import hashlib
import json

import pytest

from scripts import fetch_public_sources as fetcher


class FakeResponse:
    def __init__(self, content: bytes, final_url: str):
        self.content = content
        self.final_url = final_url
        self.status = 200
        self.headers = {
            "Content-Type": "application/pdf",
            "Last-Modified": "Tue, 21 Jul 2026 00:00:00 GMT",
            "ETag": '"test-etag"',
        }

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.content

    def geturl(self):
        return self.final_url


def test_fetcher_records_hash_redirect_and_http_validators(tmp_path, monkeypatch):
    content = b"%PDF-1.7\nvalidated test document"
    final_url = "https://city.example/current.pdf"
    monkeypatch.setattr(fetcher, "BASE_DIR", tmp_path)
    monkeypatch.setattr(
        fetcher,
        "urlopen",
        lambda request, timeout: FakeResponse(content, final_url),
    )
    source = {
        "id": "test_pdf",
        "title": "Test PDF",
        "municipality": "test",
        "category": "traffic_regulations",
        "url": "https://city.example/redirect",
        "destination": "data/raw/test/current.pdf",
        "type": "pdf",
    }

    metadata = fetcher.download_source(source)
    destination = tmp_path / source["destination"]
    stored_metadata = json.loads(
        destination.with_suffix(".pdf.metadata.json").read_text()
    )

    assert destination.read_bytes() == content
    assert metadata == stored_metadata
    assert metadata["final_url"] == final_url
    assert metadata["last_modified"] == "Tue, 21 Jul 2026 00:00:00 GMT"
    assert metadata["etag"] == '"test-etag"'
    assert metadata["sha256"] == hashlib.sha256(content).hexdigest()


def test_fetcher_does_not_replace_a_pdf_with_an_error_page(tmp_path, monkeypatch):
    monkeypatch.setattr(fetcher, "BASE_DIR", tmp_path)
    monkeypatch.setattr(
        fetcher,
        "urlopen",
        lambda request, timeout: FakeResponse(b"<html>error</html>", request.full_url),
    )
    source = {
        "id": "test_pdf",
        "url": "https://city.example/current.pdf",
        "destination": "data/raw/test/current.pdf",
        "type": "pdf",
    }
    destination = tmp_path / source["destination"]
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"%PDF-1.7\nknown-good")

    with pytest.raises(fetcher.ContentValidationError):
        fetcher.download_source(source)

    assert destination.read_bytes() == b"%PDF-1.7\nknown-good"
