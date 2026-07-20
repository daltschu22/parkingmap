"""Run first-pass parking evidence detection on downloaded street imagery.

This script is designed for the DGX Spark batch host, but it also runs locally.
It uses Ultralytics YOLO/YOLO-World when available and emits raw detections,
optional crops, and GeoJSON point evidence. Image detections are evidence only;
they are not authoritative parking rules.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

DEFAULT_CLASSES = [
    "parking sign",
    "no parking sign",
    "resident permit parking sign",
    "street cleaning sign",
    "loading zone sign",
    "bus stop sign",
    "accessible parking sign",
    "parking meter",
    "pay station",
    "fire hydrant",
    "parking space",
    "empty parking space",
    "marked parking space",
    "parking bay",
    "parking stall",
    "painted parking line",
    "parking space line",
    "road marking",
    "curb marking",
    "curb paint",
    "red curb",
    "yellow curb",
    "white curb",
    "driveway",
    "crosswalk",
    "bike lane",
]


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(path: Path) -> dict:
    return json.loads(path.read_text())


def safe_name(value: object) -> str:
    text = str(value or "").strip()
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in text)[:120]


def resolve_image_path(item: dict, manifest_dir: Path, repo_root: Path | None) -> Path:
    raw_path = Path(item["path"])
    candidates = []
    if raw_path.is_absolute():
        candidates.append(raw_path)
    if repo_root is not None:
        candidates.append(repo_root / raw_path)
    candidates.append(manifest_dir / raw_path.name)
    candidates.append(manifest_dir / raw_path)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(item["path"])


def load_model(model_name: str, classes: list[str], device: str):
    from ultralytics import YOLO

    model = YOLO(model_name)
    if hasattr(model, "set_classes"):
        model.set_classes(classes)
    return model


def box_values(box) -> tuple[list[float], float, int]:
    xyxy = [float(value) for value in box.xyxy[0].tolist()]
    confidence = float(box.conf[0])
    class_id = int(box.cls[0])
    return xyxy, confidence, class_id


def crop_detection(image_path: Path, output_path: Path, xyxy: list[float]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(image_path) as image:
        width, height = image.size
        left = max(0, int(xyxy[0]))
        top = max(0, int(xyxy[1]))
        right = min(width, int(xyxy[2]))
        bottom = min(height, int(xyxy[3]))
        if right <= left or bottom <= top:
            return
        image.crop((left, top, right, bottom)).save(output_path)


def evidence_feature(record: dict) -> dict:
    lng = record.get("sample_lng")
    lat = record.get("sample_lat")
    geometry = None
    if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
        geometry = {"type": "Point", "coordinates": [lng, lat]}
    return {
        "type": "Feature",
        "geometry": geometry,
        "properties": {
            "PROVIDER": record.get("provider"),
            "IMAGE_ID": record.get("image_id"),
            "IMAGE_PATH": record.get("image_path"),
            "SOURCE_URL": record.get("source_url"),
            "CAPTURED_AT": record.get("captured_at"),
            "HEADING": record.get("heading"),
            "SAMPLE_MUNICIPALITY": record.get("sample_municipality"),
            "STREET_NAME": record.get("street_name"),
            "DETECTED_OBJECT": record.get("detected_object"),
            "OBJECT_CONFIDENCE": record.get("object_confidence"),
            "BBOX_XYXY": record.get("bbox_xyxy"),
            "CROP_PATH": record.get("crop_path"),
            "PROJECTION_CONFIDENCE": "image_location_only",
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path)
    parser.add_argument("--model", default="yolov8s-world.pt")
    parser.add_argument("--device", default="0")
    parser.add_argument("--confidence", type=float, default=0.12)
    parser.add_argument("--iou", type=float, default=0.5)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--classes", nargs="*", default=DEFAULT_CLASSES)
    parser.add_argument("--save-crops", action="store_true", default=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    started = time.time()
    manifest_path = args.manifest.resolve()
    manifest = load_manifest(manifest_path)
    manifest_dir = manifest_path.parent
    repo_root = args.repo_root.resolve() if args.repo_root else None
    output_dir = args.output.resolve()
    crops_dir = output_dir / "crops"
    output_dir.mkdir(parents=True, exist_ok=True)

    items = manifest.get("items", [])
    if args.limit:
        items = items[: args.limit]

    model = load_model(args.model, args.classes, args.device)
    names = getattr(model, "names", {})
    detections = []
    errors = []

    for item_index, item in enumerate(items):
        try:
            image_path = resolve_image_path(item, manifest_dir, repo_root)
            results = model.predict(
                source=str(image_path),
                conf=args.confidence,
                iou=args.iou,
                device=args.device,
                verbose=False,
            )
            for result_index, result in enumerate(results):
                boxes = getattr(result, "boxes", None)
                if boxes is None:
                    continue
                for box_index, box in enumerate(boxes):
                    xyxy, confidence, class_id = box_values(box)
                    detected_object = names.get(class_id, str(class_id)) if isinstance(names, dict) else str(class_id)
                    crop_path = None
                    if args.save_crops:
                        crop_file = crops_dir / f"{safe_name(item.get('image_id'))}_{result_index}_{box_index}.jpg"
                        crop_detection(image_path, crop_file, xyxy)
                        if crop_file.exists():
                            crop_path = str(crop_file.relative_to(output_dir))
                    detections.append(
                        {
                            "provider": item.get("provider"),
                            "image_id": item.get("image_id"),
                            "image_path": item.get("path"),
                            "source_url": item.get("source_url"),
                            "captured_at": item.get("captured_at"),
                            "heading": item.get("heading"),
                            "street_name": item.get("sample_street_name"),
                            "sample_municipality": item.get("sample_municipality"),
                            "sample_lat": item.get("sample_lat"),
                            "sample_lng": item.get("sample_lng"),
                            "detected_object": detected_object,
                            "bbox_xyxy": xyxy,
                            "object_confidence": confidence,
                            "crop_path": crop_path,
                        }
                    )
        except Exception as exc:  # noqa: BLE001 - batch jobs should preserve per-image failures.
            errors.append(
                {
                    "item_index": item_index,
                    "image_id": item.get("image_id"),
                    "path": item.get("path"),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    detections_path = output_dir / "detections.jsonl"
    detections_path.write_text("".join(json.dumps(row) + "\n" for row in detections))
    evidence = {
        "type": "FeatureCollection",
        "properties": {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "source_manifest": str(manifest_path),
            "model": args.model,
            "classes": args.classes,
            "confidence": args.confidence,
            "iou": args.iou,
            "image_count": len(items),
            "detection_count": len(detections),
            "error_count": len(errors),
        },
        "features": [evidence_feature(row) for row in detections],
    }
    (output_dir / "parking_image_evidence.geojson").write_text(json.dumps(evidence, indent=2) + "\n")
    run_manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": round(time.time() - started, 3),
        "source_manifest": str(manifest_path),
        "source_manifest_sha256": sha256_path(manifest_path),
        "model": args.model,
        "classes": args.classes,
        "device": args.device,
        "confidence": args.confidence,
        "iou": args.iou,
        "image_count": len(items),
        "detection_count": len(detections),
        "error_count": len(errors),
        "errors": errors,
        "ultralytics_available": shutil.which("yolo") is not None,
    }
    (output_dir / "run_manifest.json").write_text(json.dumps(run_manifest, indent=2) + "\n")

    print(f"Wrote {detections_path}")
    print(f"Wrote {output_dir / 'parking_image_evidence.geojson'}")
    print(f"Wrote {output_dir / 'run_manifest.json'}")
    print(f"Images: {len(items)}")
    print(f"Detections: {len(detections)}")
    print(f"Errors: {len(errors)}")


if __name__ == "__main__":
    main()
