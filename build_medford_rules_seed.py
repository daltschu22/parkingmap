"""Build a first-pass structured Medford resident-permit parking dataset.

This preserves Medford's row-level restriction text and marks rows that look
partial, such as address ranges, side-of-street rules, and from/to blocks.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent
RAW_PATH = BASE_DIR / "data" / "raw" / "medford" / "resident-permit-parking-streets.pdf"
OUTPUT_DIR = BASE_DIR / "data" / "processed" / "medford"
OUTPUT_PATH = OUTPUT_DIR / "resident_permit_parking_rules.json"
TEXT_PATH = OUTPUT_DIR / "resident_permit_parking_text.txt"

SOURCE_ID = "medford_resident_permit_streets_pdf"

STREET_TYPES = [
    "Avenue",
    "Ave",
    "Boulevard",
    "Blvd",
    "Circle",
    "Court",
    "Ct",
    "Drive",
    "Dr",
    "Lane",
    "Ln",
    "Park",
    "Parkway",
    "Pkwy",
    "Place",
    "Pl",
    "Road",
    "Rd",
    "Square",
    "Sq",
    "Street",
    "St",
    "Terrace",
    "Way",
]

SEGMENT_CLUE_PATTERN = re.compile(
    r"(?:#\s*\d|\b\d+\s*[-–]\s*\d+\b|\bfrom\b|\bto\b|\bbetween\b|\bside\b|"
    r"\bin front\b|\bonly\b|\bdistance\b|\balongside\b|\bintersection\b|\bPRIVATE WAY\b|"
    r"\bPUBLIC\b|\bexception\b|\balong the\b|\bnorth\b|\bsouth\b|\beast\b|\bwest\b)",
    re.I,
)
TIME_CLUE_PATTERN = re.compile(
    r"\b\d{1,2}\s*(?::\d{2})?\s*(?:AM|PM|am|pm)\b|\b\d{1,2}\s*hr\b|"
    r"\bMonday\b|\bTuesday\b|\bWednesday\b|\bThursday\b|\bFriday\b|\bSaturday\b|\bSunday\b|"
    r"\bHolidays?\b|\bMemorial Day\b|\bLabor Day\b|\b24/7\b|\b24/6\b",
    re.I,
)


def extract_pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ModuleNotFoundError:
        PdfReader = None

    if PdfReader is not None:
        reader = PdfReader(path)
        return "\n".join(page.extract_text() or "" for page in reader.pages)

    if shutil.which("pdftotext"):
        with tempfile.TemporaryDirectory() as tmpdir:
            text_file = Path(tmpdir) / "resident_permit_parking.txt"
            subprocess.run(
                ["pdftotext", "-layout", str(path), str(text_file)],
                check=True,
            )
            return text_file.read_text(errors="replace")

    raise RuntimeError("Install pypdf or pdftotext to extract Medford PDF text.")


def clean_line(line: str) -> str:
    return re.sub(r"\s+", " ", line.replace("\x0c", " ")).strip()


def split_text_lines(text: str) -> list[str]:
    lines = [line.replace("\x0c", " ").rstrip() for line in text.splitlines()]
    return [line for line in lines if line.strip()]


def parse_rows(lines: list[str]) -> list[dict]:
    rows = []
    pending_restriction: list[str] = []
    pending_permit_prefix: str | None = None

    for line in lines:
        stripped_lower = line.strip().lower()
        if stripped_lower.startswith("street name") and "restriction" in stripped_lower:
            continue

        parts = [part.strip() for part in re.split(r"\s{2,}", line.strip()) if part.strip()]
        if not parts:
            continue

        if line[:1].isspace() or not is_street_name(parts[0]):
            pending_restriction, pending_permit_prefix = collect_pending_parts(
                parts,
                pending_restriction,
                pending_permit_prefix,
            )
            continue

        street_name = normalize_street_display(parts[0])
        remainder = parts[1:]
        zone = None
        if remainder and remainder[-1].upper() == "G":
            zone = "G"
            remainder = remainder[:-1]

        permit = None
        if remainder and is_permit_fragment(remainder[-1]):
            permit = remainder.pop()
        if pending_permit_prefix:
            permit = clean_line(f"{pending_permit_prefix} {permit or ''}")
            pending_permit_prefix = None

        restriction_parts = pending_restriction + remainder
        pending_restriction = []
        row = build_row(street_name, restriction_parts, permit, zone)
        rows.append(row)

    return rows


def is_street_name(value: str) -> bool:
    return any(
        re.search(rf"\b{re.escape(street_type)}\.?$", value)
        for street_type in STREET_TYPES
    )


def normalize_street_display(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def is_permit_fragment(value: str) -> bool:
    return bool(re.fullmatch(r"(Resident(?:\s+or)?|Business|Permit)", value, re.I))


def collect_pending_parts(
    parts: list[str],
    pending_restriction: list[str],
    pending_permit_prefix: str | None,
) -> tuple[list[str], str | None]:
    for part in parts:
        if is_permit_fragment(part):
            pending_permit_prefix = part
        else:
            pending_restriction.append(part)
    return pending_restriction, pending_permit_prefix


def build_row(
    street_name: str,
    restriction_parts: list[str],
    permit: str | None,
    zone: str | None,
) -> dict:
    restriction = clean_line(" ".join(restriction_parts))
    permit = clean_line(permit) if permit else None
    raw_parts = [street_name, restriction, permit or "", zone or ""]
    raw_text = clean_line(" ".join(part for part in raw_parts if part))
    row = {
        "street_name": street_name,
        "raw_text": raw_text,
        "restriction_text": restriction,
        "permit_type": permit,
        "zone": zone,
    }
    row["has_segment_clues"] = bool(SEGMENT_CLUE_PATTERN.search(raw_text))
    row["has_time_clues"] = bool(TIME_CLUE_PATTERN.search(raw_text))
    row["scope"] = classify_scope(row)
    return row


def classify_scope(row: dict) -> str:
    if row["has_segment_clues"]:
        return "partial_or_segment_specific"
    if row["has_time_clues"]:
        return "street_level_time_window"
    return "likely_full_street"


def build_seed() -> dict:
    if not RAW_PATH.exists():
        raise FileNotFoundError(
            f"Missing {RAW_PATH}. Run: python3 scripts/fetch_public_sources.py --municipality medford"
        )

    text = extract_pdf_text(RAW_PATH)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    TEXT_PATH.write_text(text)

    rows = parse_rows(split_text_lines(text))
    streets: dict[str, list[dict]] = {}
    for row in rows:
        row["source_id"] = SOURCE_ID
        row["source_file"] = str(RAW_PATH.relative_to(BASE_DIR))
        streets.setdefault(row["street_name"].upper(), []).append(row)

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "municipality": "medford",
        "source_id": SOURCE_ID,
        "source_file": str(RAW_PATH.relative_to(BASE_DIR)),
        "parser_note": (
            "First-pass text extraction from the Medford resident-permit PDF. "
            "Rows with partial_or_segment_specific scope must not be applied to an entire street."
        ),
        "row_count": len(rows),
        "street_count": len(streets),
        "rows": rows,
        "streets": streets,
    }


def main() -> None:
    seed = build_seed()
    OUTPUT_PATH.write_text(json.dumps(seed, indent=2) + "\n")
    partial = sum(1 for row in seed["rows"] if row["scope"] == "partial_or_segment_specific")
    print(f"Wrote {OUTPUT_PATH}")
    print(f"Rows: {seed['row_count']}")
    print(f"Unique streets: {seed['street_count']}")
    print(f"Partial/segment-specific rows: {partial}")


if __name__ == "__main__":
    main()
