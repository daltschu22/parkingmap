"""Download raw public parking sources listed in data/source_manifest.json."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).resolve().parent.parent
MANIFEST_PATH = BASE_DIR / "data" / "source_manifest.json"


def iter_sources(manifest: dict, municipality: str | None, include_disabled: bool):
    for source in manifest.get("sources", []):
        if municipality and source.get("municipality") != municipality:
            continue
        if not include_disabled and not source.get("enabled", True):
            continue
        yield source


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
        destination.write_bytes(content)
        metadata = {
            "id": source["id"],
            "title": source.get("title"),
            "municipality": source.get("municipality"),
            "category": source.get("category"),
            "url": source["url"],
            "downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
            "http_status": getattr(response, "status", None),
            "content_type": response.headers.get("Content-Type"),
            "bytes": len(content),
        }
        metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
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
        except (HTTPError, URLError, TimeoutError) as exc:
            failures.append((source["id"], str(exc)))
            print(f"{source['id']}: ERROR {exc}")

    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
