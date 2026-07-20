"""Download preview images from a street-level imagery metadata index."""
from __future__ import annotations

import argparse
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_INDEX_PATH = BASE_DIR / "data" / "processed" / "imagery" / "street_imagery_index.geojson"
DEFAULT_OUTPUT_DIR = BASE_DIR / "data" / "processed" / "imagery" / "assets"


def safe_name(value: object) -> str:
    text = str(value or "").strip()
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in text)[:120]


def image_url(properties: dict) -> str:
    return (
        properties.get("IMAGE_PREVIEW_URL")
        or properties.get("IMAGE_THUMB_URL")
        or properties.get("IMAGE_ORIGINAL_URL")
        or ""
    )


def download_file(url: str, output_path: Path) -> int:
    request = urllib.request.Request(url, headers={"User-Agent": "parkingmap-imagery-download/0.1"})
    with urllib.request.urlopen(request, timeout=60) as response:
        content = response.read()
    output_path.write_bytes(content)
    return len(content)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--limit", type=int, default=50)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    index_path = args.index.resolve()
    output_dir = args.output_dir.resolve()
    index = json.loads(index_path.read_text())
    dataset_dir = output_dir / index_path.stem
    dataset_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_index": str(index_path.relative_to(BASE_DIR)),
        "output_dir": str(dataset_dir.relative_to(BASE_DIR)),
        "items": [],
        "errors": [],
    }

    seen = set()
    for feature in index.get("features", [])[: args.limit]:
        props = feature.get("properties") or {}
        provider = safe_name(props.get("PROVIDER"))
        image_id = safe_name(props.get("IMAGE_ID"))
        url = image_url(props)
        if not image_id or not url:
            manifest["errors"].append({"image_id": image_id, "error": "missing image id or URL"})
            continue
        dedupe_key = (provider, image_id)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        output_path = dataset_dir / f"{provider}_{image_id}.jpg"
        try:
            size_bytes = output_path.stat().st_size if output_path.exists() else download_file(url, output_path)
            geometry = feature.get("geometry") or {}
            coordinates = geometry.get("coordinates") or []
            image_lng = coordinates[0] if geometry.get("type") == "Point" and len(coordinates) >= 2 else None
            image_lat = coordinates[1] if geometry.get("type") == "Point" and len(coordinates) >= 2 else None
            manifest["items"].append(
                {
                    "provider": provider,
                    "image_id": image_id,
                    "path": str(output_path.relative_to(BASE_DIR)),
                    "size_bytes": size_bytes,
                    "captured_at": props.get("CAPTURED_AT"),
                    "heading": props.get("HEADING"),
                    "sample_municipality": props.get("SAMPLE_MUNICIPALITY"),
                    "sample_street_name": props.get("SAMPLE_STREET_NAME"),
                    "sample_lat": props.get("SAMPLE_LAT") or image_lat,
                    "sample_lng": props.get("SAMPLE_LNG") or image_lng,
                    "image_lat": image_lat,
                    "image_lng": image_lng,
                    "source_url": props.get("SOURCE_URL"),
                }
            )
        except Exception as exc:  # noqa: BLE001 - keep batch running and report failures.
            manifest["errors"].append(
                {
                    "image_id": image_id,
                    "url": url,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    manifest_path = dataset_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Wrote {manifest_path}")
    print(f"Downloaded/available images: {len(manifest['items'])}")
    print(f"Errors: {len(manifest['errors'])}")


if __name__ == "__main__":
    main()
