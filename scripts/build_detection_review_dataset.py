"""Build an inspectable HTML/image review dataset from image detections."""
from __future__ import annotations

import argparse
import csv
import html
import json
from collections import Counter
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_RUN_DIR = BASE_DIR / "data" / "processed" / "imagery" / "spark_runs"

COLORS = {
    "resident permit parking sign": "#ef4444",
    "parking sign": "#f97316",
    "street cleaning sign": "#eab308",
    "bus stop sign": "#3b82f6",
    "parking meter": "#06b6d4",
    "fire hydrant": "#a855f7",
}
DEFAULT_COLOR = "#22c55e"


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def safe_name(value: object) -> str:
    text = str(value or "").strip()
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in text)[:120]


def resolve_image_path(image_path: str, repo_root: Path) -> Path:
    path = Path(image_path)
    if path.is_absolute():
        return path
    return repo_root / path


def group_by_image(rows: list[dict]) -> dict[str, list[dict]]:
    grouped = {}
    for row in rows:
        grouped.setdefault(str(row.get("image_id") or ""), []).append(row)
    return grouped


def draw_label(draw: ImageDraw.ImageDraw, xy: tuple[int, int], label: str, color: str) -> None:
    font = ImageFont.load_default()
    x, y = xy
    bbox = draw.textbbox((x, y), label, font=font)
    pad = 3
    rect = (bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad)
    draw.rectangle(rect, fill=color)
    draw.text((x, y), label, fill="white", font=font)


def annotate_image(source: Path, rows: list[dict], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        annotated = image.convert("RGB")
    draw = ImageDraw.Draw(annotated)
    for row in rows:
        xyxy = row.get("bbox_xyxy") or []
        if len(xyxy) != 4:
            continue
        label = str(row.get("detected_object") or "object")
        confidence = float(row.get("object_confidence") or 0)
        color = COLORS.get(label, DEFAULT_COLOR)
        box = tuple(int(value) for value in xyxy)
        width = max(2, round(confidence * 6))
        for offset in range(width):
            draw.rectangle(
                (
                    box[0] - offset,
                    box[1] - offset,
                    box[2] + offset,
                    box[3] + offset,
                ),
                outline=color,
            )
        draw_label(draw, (box[0], max(0, box[1] - 15)), f"{label} {confidence:.2f}", color)
    annotated.save(output, quality=92)


def write_csv(rows: list[dict], output_path: Path) -> None:
    fieldnames = [
        "image_id",
        "street_name",
        "detected_object",
        "object_confidence",
        "bbox_xyxy",
        "source_url",
        "image_path",
        "annotated_path",
        "crop_path",
        "reference_feature_id",
        "reference_object_value",
        "reference_first_seen_at",
        "reference_last_seen_at",
        "match_method",
    ]
    with output_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def relative(path: Path, root: Path) -> str:
    return str(path.relative_to(root))


def write_html(rows: list[dict], grouped: dict[str, list[dict]], output_dir: Path) -> None:
    class_counts = Counter(row.get("detected_object") for row in rows)
    image_cards = []
    for image_id, image_rows in grouped.items():
        first = image_rows[0]
        annotated_path = first.get("annotated_path")
        detections = "\n".join(
            f"<li><strong>{html.escape(str(row.get('detected_object')))}</strong> "
            f"{float(row.get('object_confidence') or 0):.3f} "
            f"<code>{html.escape(str(row.get('bbox_xyxy')))}</code>"
            f"{reference_html(row)}</li>"
            for row in sorted(image_rows, key=lambda item: item.get("object_confidence") or 0, reverse=True)
        )
        image_cards.append(
            f"""
            <article class="card">
              <a href="{html.escape(annotated_path)}"><img src="{html.escape(annotated_path)}" loading="lazy"></a>
              <div class="meta">
                <div><strong>{html.escape(str(first.get('street_name') or ''))}</strong></div>
                <div>Image <code>{html.escape(image_id)}</code></div>
                <div><a href="{html.escape(str(first.get('source_url') or '#'))}">Mapillary source</a></div>
                <ul>{detections}</ul>
              </div>
            </article>
            """
        )

    counts = "".join(
        f"<li>{html.escape(str(label))}: {count}</li>"
        for label, count in class_counts.most_common()
    )
    html_doc = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Parking Detection Review</title>
  <style>
    body {{ margin: 0; font-family: system-ui, sans-serif; background: #111827; color: #f9fafb; }}
    header {{ padding: 20px; background: #0f172a; position: sticky; top: 0; z-index: 1; }}
    main {{ padding: 20px; display: grid; grid-template-columns: repeat(auto-fill, minmax(360px, 1fr)); gap: 16px; }}
    .card {{ background: #1f2937; border: 1px solid #374151; border-radius: 8px; overflow: hidden; }}
    img {{ width: 100%; display: block; }}
    .meta {{ padding: 12px; font-size: 14px; }}
    code {{ color: #bfdbfe; }}
    a {{ color: #93c5fd; }}
  </style>
</head>
<body>
  <header>
    <h1>Parking Detection Review</h1>
    <div>{len(rows)} detections across {len(grouped)} images</div>
    <ul>{counts}</ul>
  </header>
  <main>
    {''.join(image_cards)}
  </main>
</body>
</html>
"""
    (output_dir / "index.html").write_text(html_doc)


def reference_html(row: dict) -> str:
    object_value = row.get("reference_object_value")
    if not object_value:
        return ""
    source = html.escape(str(row.get("reference_source_url") or "#"))
    feature_id = html.escape(str(row.get("reference_feature_id") or ""))
    value = html.escape(str(object_value))
    return f"<br><span>Reference: <a href=\"{source}\">{value}</a> <code>{feature_id}</code></span>"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument(
        "--detections-jsonl",
        type=Path,
        help="Detection JSONL to review. Defaults to RUN_DIR/detections.jsonl.",
    )
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--min-confidence", type=float, default=0.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_dir = args.run_dir.resolve()
    output_dir = (args.output_dir or run_dir / "review").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    annotated_dir = output_dir / "annotated"
    repo_root = BASE_DIR
    detections_path = args.detections_jsonl or run_dir / "detections.jsonl"

    rows = [
        row
        for row in load_jsonl(detections_path)
        if float(row.get("object_confidence") or 0) >= args.min_confidence
    ]
    grouped = group_by_image(rows)
    review_rows = []
    for image_id, image_rows in grouped.items():
        source = resolve_image_path(image_rows[0]["image_path"], repo_root)
        annotated = annotated_dir / f"{safe_name(image_id)}.jpg"
        annotate_image(source, image_rows, annotated)
        for row in image_rows:
            row = dict(row)
            row["annotated_path"] = relative(annotated, output_dir)
            review_rows.append(row)

    review_grouped = group_by_image(review_rows)
    write_csv(review_rows, output_dir / "detections_review.csv")
    (output_dir / "detections_review.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in review_rows)
    )
    write_html(review_rows, review_grouped, output_dir)

    print(f"Wrote {output_dir / 'index.html'}")
    print(f"Annotated images: {len(grouped)}")
    print(f"Detections: {len(review_rows)}")


if __name__ == "__main__":
    main()
