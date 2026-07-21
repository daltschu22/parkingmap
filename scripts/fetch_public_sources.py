"""Download raw public parking sources listed in data/source_manifest.json."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).resolve().parent.parent
MANIFEST_PATH = BASE_DIR / "data" / "source_manifest.json"


class ContentValidationError(ValueError):
    """Raised when a source response does not match its declared file type."""


def iter_sources(manifest: dict, municipality: str | None, include_disabled: bool):
    for source in manifest.get("sources", []):
        if municipality and source.get("municipality") != municipality:
            continue
        if not include_disabled and not source.get("enabled", True):
            continue
        yield source


def validate_content(source: dict, content: bytes) -> None:
    """Reject empty responses and common error pages before replacing source files."""
    if not content:
        raise ContentValidationError(f"{source['id']}: downloaded an empty response")

    source_type = str(source.get("type") or "").casefold()
    if source_type == "pdf" and not content.lstrip().startswith(b"%PDF-"):
        raise ContentValidationError(
            f"{source['id']}: expected a PDF but the response was not a PDF"
        )
    if source_type == "html":
        prefix = content[:4096].lstrip().lower()
        if b"<html" not in prefix and b"<!doctype html" not in prefix:
            raise ContentValidationError(
                f"{source['id']}: expected HTML but the response was not HTML"
            )
    if source_type == "image" and not content.startswith(
        (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")
    ):
        raise ContentValidationError(
            f"{source['id']}: expected a PNG or JPEG image"
        )
    if source_type == "kml":
        try:
            import xml.etree.ElementTree as ET

            root = ET.fromstring(content)
        except ET.ParseError as exc:
            raise ContentValidationError(
                f"{source['id']}: expected valid KML/XML"
            ) from exc
        if not root.tag.casefold().endswith("kml"):
            raise ContentValidationError(
                f"{source['id']}: XML response is not a KML document"
            )
    if source_type in {"json", "geojson"}:
        try:
            json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContentValidationError(
                f"{source['id']}: expected valid JSON"
            ) from exc


def atomic_write(path: Path, content: bytes) -> None:
    """Replace a source artifact only after the full response has been validated."""
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)
        temp_path.replace(path)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()


def download_source(source: dict, dry_run: bool = False) -> dict:
    destination = BASE_DIR / source["destination"]
    metadata_path = destination.with_suffix(destination.suffix + ".metadata.json")

    if dry_run:
        return {
            "id": source["id"],
            "destination": str(destination.relative_to(BASE_DIR)),
            "status": "dry-run",
        }

    destination.parent.mkdir(parents=True, exist_ok=True)
    request = Request(
        source["url"],
        headers={
            "User-Agent": "parkingmap public source fetcher; contact source municipality for authoritative rules"
        },
    )

    with urlopen(request, timeout=60) as response:
        content = response.read()
        validate_content(source, content)
        atomic_write(destination, content)
        metadata = {
            "id": source["id"],
            "title": source.get("title"),
            "municipality": source.get("municipality"),
            "category": source.get("category"),
            "url": source["url"],
            "final_url": response.geturl(),
            "destination": str(destination.relative_to(BASE_DIR)),
            "downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
            "http_status": getattr(response, "status", None),
            "content_type": response.headers.get("Content-Type"),
            "last_modified": response.headers.get("Last-Modified"),
            "etag": response.headers.get("ETag"),
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        atomic_write(
            metadata_path,
            (json.dumps(metadata, indent=2) + "\n").encode(),
        )
        return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--municipality",
        help="Only fetch sources for one municipality, e.g. medford.",
    )
    parser.add_argument(
        "--include-disabled",
        action="store_true",
        help="Also fetch disabled manifest entries.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be fetched without downloading.",
    )
    args = parser.parse_args()

    manifest = json.loads(MANIFEST_PATH.read_text())
    failures = []
    for source in iter_sources(manifest, args.municipality, args.include_disabled):
        try:
            result = download_source(source, dry_run=args.dry_run)
            print(f"{result['id']}: {result.get('bytes', 0)} bytes -> {result['destination'] if 'destination' in result else source['destination']}")
        except (HTTPError, URLError, TimeoutError, ContentValidationError) as exc:
            failures.append((source["id"], str(exc)))
            print(f"{source['id']}: ERROR {exc}")

    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
