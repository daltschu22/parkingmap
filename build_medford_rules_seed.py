"""Build a first-pass structured Medford resident-permit parking dataset.

This preserves Medford's row-level restriction text and marks rows that look
partial, such as address ranges, side-of-street rules, and from/to blocks.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pdfplumber

BASE_DIR = Path(__file__).parent
RAW_PATH = BASE_DIR / "data" / "raw" / "medford" / "resident-permit-parking-streets.pdf"
SOURCE_METADATA_PATH = RAW_PATH.with_suffix(RAW_PATH.suffix + ".metadata.json")
OUTPUT_DIR = BASE_DIR / "data" / "processed" / "medford"
OUTPUT_PATH = OUTPUT_DIR / "resident_permit_parking_rules.json"
TEXT_PATH = OUTPUT_DIR / "resident_permit_parking_text.txt"

SOURCE_ID = "medford_resident_permit_streets_pdf"


class SourceValidationError(ValueError):
    """Raised when the Medford PDF and its fetch provenance disagree."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_metadata() -> dict:
    if not SOURCE_METADATA_PATH.exists():
        raise SourceValidationError(
            "Medford source metadata is missing; refetch official sources before rebuilding."
        )
    try:
        metadata = json.loads(SOURCE_METADATA_PATH.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceValidationError("Medford source metadata is unreadable") from exc

    digest = sha256_file(RAW_PATH)
    if metadata.get("sha256") != digest:
        raise SourceValidationError(
            "Medford resident-permit PDF does not match its fetch metadata"
        )
    return {
        "id": metadata.get("id", SOURCE_ID),
        "file": str(RAW_PATH.relative_to(BASE_DIR)),
        "url": metadata.get("final_url") or metadata.get("url"),
        "downloaded_at_utc": metadata.get("downloaded_at_utc"),
        "last_modified": metadata.get("last_modified"),
        "etag": metadata.get("etag"),
        "bytes": RAW_PATH.stat().st_size,
        "sha256": digest,
    }

SEGMENT_CLUE_PATTERN = re.compile(
    r"(?:#\s*\d|\b\d+\s*[-–]\s*\d+\b|\bfrom\b|\bto\b|\bbetween\b|\bsides?\b|\b(?:lower|upper) end\b|"
    r"\bin front\b|\bonly\b|\bdistance\b|\balongside\b|\bintersection\b|\bPRIVATE WAY\b|"
    r"\bPUBLIC\b|\bexception\b|\balong the\b|\bnorth\b|\bsouth\b|\beast\b|\bwest\b)",
    re.I,
)
TIME_CLUE_PATTERN = re.compile(
    r"\b\d{1,2}\s*(?::\d{2})?\s*(?:AM|PM|am|pm)\b|\b\d{1,2}\s*hr\b|"
    r"\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)s?\b|"
    r"\bHolidays?\b|\bMemorial Day\b|\bLabor Day\b|\b24/7\b|\b24/6\b",
    re.I,
)


def extract_pdf_text(path: Path) -> str:
    # This source is a four-column table. Layout-preserving extraction is
    # required; plain pypdf extraction can collapse the columns and reduce 191
    # source rows to roughly 20 malformed records.
    if shutil.which("pdftotext"):
        with tempfile.TemporaryDirectory() as tmpdir:
            text_file = Path(tmpdir) / "resident_permit_parking.txt"
            subprocess.run(
                ["pdftotext", "-layout", str(path), str(text_file)],
                check=True,
            )
            return text_file.read_text(errors="replace")

    try:
        from pypdf import PdfReader
    except ModuleNotFoundError:
        PdfReader = None

    if PdfReader is not None:
        reader = PdfReader(path)
        return "\n".join(
            page.extract_text(extraction_mode="layout") or ""
            for page in reader.pages
        )

    raise RuntimeError("Install pypdf or pdftotext to extract Medford PDF text.")


def clean_line(line: str) -> str:
    return re.sub(r"\s+", " ", line.replace("\x0c", " ")).strip()


def parse_rows(path: Path) -> list[dict]:
    rows = []
    with pdfplumber.open(path) as document:
        for page in document.pages:
            table = page.extract_table()
            if not table or any(len(cells) != 4 for cells in table):
                raise SourceValidationError(
                    f"Expected four permit columns on page {page.page_number}"
                )
            for number, cells in enumerate(table, 1):
                name, restriction, permit, zone = [
                    clean_line(value or "") for value in cells
                ]
                if name.casefold() == "street name":
                    continue
                row = build_row(name, restriction, permit or None, zone or None)
                row.update(page=page.page_number, row=number)
                rows.append(row)
    return rows


def build_row(
    street_name: str,
    restriction: str,
    permit: str | None,
    zone: str | None,
) -> dict:
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
    row["has_segment_clues"] = bool(SEGMENT_CLUE_PATTERN.search(restriction))
    row["has_time_clues"] = bool(TIME_CLUE_PATTERN.search(restriction))
    row["scope"] = classify_scope(row)
    return row


def classify_scope(row: dict) -> str:
    if not row["street_name"] or row["permit_type"] not in {
        "Resident",
        "Resident or Business",
        "Business",
    }:
        return "unresolved"
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

    source = source_metadata()
    text = extract_pdf_text(RAW_PATH)
    rows = parse_rows(RAW_PATH)
    unique_street_count = len({row["street_name"].casefold() for row in rows})
    if len(rows) < 150 or unique_street_count < 150:
        raise SourceValidationError(
            "Medford resident-permit extraction is implausibly small: "
            f"{len(rows)} rows across {unique_street_count} streets"
        )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    TEXT_PATH.write_text(text)
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
        "source": source,
        "parser_note": (
            "Rows extracted from the four columns of the Medford resident-permit PDF. "
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
