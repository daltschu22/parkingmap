"""Run OCR over detection crops and classify likely parking-sign text."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter, ImageOps

BASE_DIR = Path(__file__).resolve().parent.parent

PARKING_PATTERNS = [
    r"\bNO\s+PARK",
    r"\bPARKING\b",
    r"\bPERMIT\b",
    r"\bRESIDENT\b",
    r"\bMETER\b",
    r"\bPAY\b",
    r"\bLOADING\b",
    r"\bSTREET\s+CLEAN",
    r"\bTOW\b",
    r"\bMON\b|\bTUE\b|\bWED\b|\bTHU\b|\bFRI\b|\bSAT\b|\bSUN\b",
    r"\bAM\b|\bPM\b",
    r"\bHOUR\b|\bHR\b|\bMIN\b",
]

NON_PARKING_PATTERNS = [
    r"\bRTE\b|\bROUTE\b",
    r"\bNORTH\b|\bSOUTH\b|\bEAST\b|\bWEST\b",
    r"\bLANE\b",
    r"\bONE\s+WAY\b",
    r"\bSTOP\b",
    r"\bSPEED\b",
]


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def crop_path(run_dir: Path, row: dict) -> Path | None:
    value = row.get("crop_path")
    if not value:
        return None
    path = run_dir / value
    return path if path.exists() else None


def original_image_path(run_dir: Path, row: dict) -> Path | None:
    value = row.get("image_path") or row.get("IMAGE_PATH")
    if not value:
        return None
    raw_path = Path(value)
    candidates = []
    if raw_path.is_absolute():
        candidates.append(raw_path)
    candidates.append(run_dir / raw_path)
    candidates.append(run_dir.parent / raw_path)
    candidates.append(run_dir.parent / raw_path.name)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def preprocessed_crop_path(
    run_dir: Path,
    row: dict,
    output_dir: Path,
    padding_ratio: float,
    upscale: int,
) -> Path | None:
    image_path = original_image_path(run_dir, row)
    bbox = row.get("bbox_xyxy") or row.get("BBOX_XYXY") or []
    if not image_path or len(bbox) != 4:
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    image_id = row.get("image_id") or row.get("IMAGE_ID") or image_path.stem
    crop_name = Path(row.get("crop_path") or row.get("CROP_PATH") or f"{image_id}.jpg").stem
    output_path = output_dir / f"{crop_name}_ocr.jpg"
    if output_path.exists():
        return output_path

    with Image.open(image_path) as image:
        width, height = image.size
        left, top, right, bottom = [float(value) for value in bbox]
        box_width = max(1, right - left)
        box_height = max(1, bottom - top)
        pad_x = box_width * padding_ratio
        pad_y = box_height * padding_ratio
        crop_box = (
            max(0, int(left - pad_x)),
            max(0, int(top - pad_y)),
            min(width, int(right + pad_x)),
            min(height, int(bottom + pad_y)),
        )
        if crop_box[2] <= crop_box[0] or crop_box[3] <= crop_box[1]:
            return None
        crop = image.crop(crop_box).convert("RGB")

    scale = max(1, upscale)
    if scale > 1:
        crop = crop.resize((crop.width * scale, crop.height * scale), Image.Resampling.LANCZOS)
    crop = ImageOps.autocontrast(crop)
    crop = crop.filter(ImageFilter.SHARPEN)
    crop.save(output_path, quality=95)
    return output_path


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.upper()).strip()


def classify_text(text: str) -> tuple[str, list[str]]:
    normalized = normalize_text(text)
    parking_hits = [pattern for pattern in PARKING_PATTERNS if re.search(pattern, normalized)]
    non_parking_hits = [pattern for pattern in NON_PARKING_PATTERNS if re.search(pattern, normalized)]
    if parking_hits:
        return "parking_text_candidate", parking_hits
    if non_parking_hits:
        return "non_parking_traffic_sign", non_parking_hits
    if normalized:
        return "text_unclassified", []
    return "no_text", []


def json_safe(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    if isinstance(value, tuple):
        return [json_safe(item) for item in value]
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    return value


def run_easyocr(
    rows: list[dict],
    run_dir: Path,
    languages: list[str],
    gpu: bool,
    padding_ratio: float,
    upscale: int,
) -> list[dict]:
    import easyocr

    reader = easyocr.Reader(languages, gpu=gpu)
    output = []
    preprocessed_dir = run_dir / "ocr_crops"
    for row in rows:
        path = preprocessed_crop_path(run_dir, row, preprocessed_dir, padding_ratio, upscale) or crop_path(run_dir, row)
        ocr_rows = []
        if path:
            try:
                for bbox, text, confidence in reader.readtext(str(path), detail=1, paragraph=False):
                    ocr_rows.append(
                        {
                            "text": text,
                            "confidence": float(confidence),
                            "bbox": json_safe(bbox),
                        }
                    )
            except Exception as exc:  # noqa: BLE001 - preserve OCR failures per crop.
                ocr_rows.append({"text": "", "confidence": 0.0, "error": f"{type(exc).__name__}: {exc}"})
        merged_text = " ".join(item.get("text", "") for item in ocr_rows)
        text_class, text_patterns = classify_text(merged_text)
        output.append(
            {
                **row,
                "ocr_engine": "easyocr",
                "ocr_image_path": str(path.relative_to(run_dir)) if path.is_relative_to(run_dir) else str(path),
                "ocr_text": merged_text,
                "ocr_rows": ocr_rows,
                "ocr_classification": text_class,
                "ocr_classification_patterns": text_patterns,
            }
        )
    return output


def matches_requested_object(row: dict, tokens: list[str]) -> bool:
    if not tokens:
        return True
    detected_object = str(row.get("detected_object") or row.get("DETECTED_OBJECT") or "").lower()
    return any(token.lower() in detected_object for token in tokens)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--input-jsonl", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--languages", nargs="+", default=["en"])
    parser.add_argument("--min-confidence", type=float, default=0.0)
    parser.add_argument("--ocr-padding-ratio", type=float, default=0.8)
    parser.add_argument("--ocr-upscale", type=int, default=4)
    parser.add_argument(
        "--include-detected-object",
        action="append",
        default=["sign"],
        help="Only OCR detections whose detected_object contains this token. Repeat for more. Default: sign.",
    )
    parser.add_argument("--cpu", action="store_true", help="Disable EasyOCR GPU mode.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_dir = args.run_dir.resolve()
    input_jsonl = args.input_jsonl.resolve() if args.input_jsonl else run_dir / "detections.jsonl"
    rows = [
        row
        for row in load_jsonl(input_jsonl)
        if float(row.get("object_confidence") or 0) >= args.min_confidence
        and matches_requested_object(row, args.include_detected_object)
    ]
    output_path = args.output or run_dir / "ocr.jsonl"
    output = run_easyocr(
        rows,
        run_dir,
        args.languages,
        gpu=not args.cpu,
        padding_ratio=args.ocr_padding_ratio,
        upscale=args.ocr_upscale,
    )
    output_path.write_text("".join(json.dumps(row) + "\n" for row in output))
    counts = {}
    for row in output:
        key = row.get("ocr_classification")
        counts[key] = counts.get(key, 0) + 1
    print(f"Wrote {output_path}")
    print(f"OCR rows: {len(output)}")
    print(f"Classifications: {counts}")


if __name__ == "__main__":
    main()
