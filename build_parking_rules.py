"""
Build a structured per-street parking rules dataset from Somerville traffic regulations.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import Counter
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pdfplumber
from pypdf import PdfReader

from visitor_parking import public_time_limit

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
PDF_PATH = DATA_DIR / "traffic-regulations.pdf"
STREETS_PATH = DATA_DIR / "streets.geojson"
OUTPUT_PATH = DATA_DIR / "parking_rules_by_street.json"
SOURCE_METADATA_PATH = PDF_PATH.with_suffix(PDF_PATH.suffix + ".metadata.json")
PARSER_VERSION = 3


class SourceValidationError(ValueError):
    """Raised when the legal source is missing expected identity or schedules."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_street_name(value: str | None) -> str:
    if not value:
        return ""

    text = re.sub(r"[^A-Za-z0-9 ]+", " ", value.upper())
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""

    token_map = {
        "STREET": "ST",
        "ST": "ST",
        "AVENUE": "AVE",
        "AVE": "AVE",
        "ROAD": "RD",
        "RD": "RD",
        "DRIVE": "DR",
        "DR": "DR",
        "PLACE": "PL",
        "PL": "PL",
        "COURT": "CT",
        "CT": "CT",
        "TERRACE": "TER",
        "TER": "TER",
        "PARKWAY": "PKWY",
        "PKWY": "PKWY",
        "SQUARE": "SQ",
        "SQ": "SQ",
        "HIGHWAY": "HWY",
        "HWY": "HWY",
        "LANE": "LN",
        "LN": "LN",
        "BOULEVARD": "BLVD",
        "BLVD": "BLVD",
        "CIRCLE": "CIR",
        "CIR": "CIR",
    }
    tokens = [token_map.get(token, token) for token in text.split()]
    return " ".join(tokens)


def load_street_candidates() -> tuple[list[str], dict[str, str]]:
    geo = json.loads(STREETS_PATH.read_text())
    names = {}
    for feature in geo.get("features", []):
        raw = (feature.get("properties", {}).get("STNAME") or "").strip()
        norm = normalize_street_name(raw)
        if norm and norm not in names:
            names[norm] = raw
    candidates = sorted(names.keys(), key=len, reverse=True)
    return candidates, names


def schedule_pages(reader: PdfReader) -> dict[str, int]:
    pages = {}
    for index, page in enumerate(reader.pages):
        for letter in re.findall(
            r"^\s*SCHEDULE\s+([DEFG])\s*$", page.extract_text() or "", re.I | re.M
        ):
            pages[letter.upper()] = index
    if sorted(pages) != list("DEFG") or [pages[k] for k in "DEFG"] != sorted(
        pages.values()
    ):
        raise SourceValidationError(
            "Expected Somerville schedules D, E, F and G in order"
        )
    return pages


def schedule_rows(document, start: int, end: int) -> list[dict]:
    rows = []
    location = ""
    for index in range(start, end):
        table = document.pages[index].find_table()
        if table is None:
            raise SourceValidationError(
                f"Missing regulation table on PDF page {index + 1}"
            )
        # Some header cells contain extra borders. The ordinary five-column
        # rows establish column positions even where an individual cell is merged.
        layouts = Counter(
            tuple(round(c[0]) for c in row.cells if c) for row in table.rows
        )
        columns = next(
            (layout for layout, _ in layouts.most_common() if len(layout) == 5), None
        )
        if columns is None:
            raise SourceValidationError(
                f"Unexpected table columns on PDF page {index + 1}"
            )
        for row_number, (row, values) in enumerate(zip(table.rows, table.extract()), 1):
            fields = [""] * 5
            for box, value in zip(row.cells, values):
                if box and value:
                    column = bisect_right(columns, round(box[0])) - 1
                    fields[column] = (
                        fields[column] + " " + " ".join(value.split())
                    ).strip()
            name, side, start_at, end_at, restriction = fields
            if name.lower() == "location" or restriction.lower() == "restrictions":
                continue
            if not any(fields):
                continue
            if row_number == 1 and rows and not any(fields[:4]):
                previous = rows[-1]
                previous["restriction"] += " " + restriction
                previous["raw_text"] += "\n" + restriction
                previous["end_page"] = index + 1
                continue
            if name:
                location = name
            rows.append(
                {
                    "page": index + 1,
                    "row": row_number,
                    "location": location,
                    "side": side,
                    "from": start_at,
                    "to": end_at,
                    "restriction": restriction,
                    "raw_text": " | ".join(fields),
                }
            )
    return rows


def meter_action(restriction: str) -> str:
    text = restriction.casefold()
    if re.search(r"\b(remov\w*|rescind\w*)\b", text):
        return "seasonal_removal" if "seasonal" in text else "removal"
    if re.search(r"\b(?:proposed|pending)\b", text):
        return "proposed"
    if "seasonal" in text:
        return "seasonal"
    if re.search(r"\bmeters?\b|\bmetered\b", text):
        return "installation" if re.search(r"\binstall\w*\b", text) else "meter_listed"
    return "other"


def meter_count(restriction: str) -> int | None:
    words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
    match = re.search(
        r"(?<![#\w])\(?(\d+|one|two|three|four|five)\)?\s+"
        r"(?:\d+[- ]*(?:minute|hour|hr)s?\s+)?(?:parking\s+)?meter(?:s|ed\s+spaces?)?\b",
        restriction.casefold(),
    )
    if match:
        count = match.group(1)
        return int(count) if count.isdigit() else words[count]
    spaces = re.search(
        r"(\d+)\s+(?:parking\s+spaces?\s+metered|spaces?\))", restriction.casefold()
    )
    return int(spaces.group(1)) if spaces else None


def source_metadata(reader: PdfReader, first_page_text: str) -> dict:
    if not SOURCE_METADATA_PATH.exists():
        raise SourceValidationError(
            "Traffic-regulation fetch metadata is missing; run the Somerville source "
            "fetcher before rebuilding."
        )
    try:
        metadata = json.loads(SOURCE_METADATA_PATH.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceValidationError(
            "Traffic-regulation fetch metadata is unreadable; refetch the source before "
            "rebuilding."
        ) from exc

    edition_match = re.search(
        r"\b(January|February|March|April|May|June|July|August|September|"
        r"October|November|December)\s+(20\d{2})\b",
        first_page_text,
        re.IGNORECASE,
    )
    edition = edition_match.group(0).title() if edition_match else None
    digest = sha256_file(PDF_PATH)
    recorded_digest = metadata.get("sha256")
    if recorded_digest and recorded_digest != digest:
        raise SourceValidationError(
            "Traffic-regulation PDF does not match its fetch metadata; refetch the source "
            "before rebuilding."
        )

    return {
        "id": metadata.get("id", "somerville_traffic_regulations_pdf"),
        "file": str(PDF_PATH.relative_to(BASE_DIR)),
        "url": metadata.get("final_url") or metadata.get("url"),
        "downloaded_at_utc": metadata.get("downloaded_at_utc"),
        "last_modified": metadata.get("last_modified"),
        "etag": metadata.get("etag"),
        "bytes": PDF_PATH.stat().st_size,
        "sha256": digest,
        "page_count": len(reader.pages),
        "edition": edition,
    }


def build_rules() -> dict:
    candidates, display_names = load_street_candidates()
    reader = PdfReader(PDF_PATH)
    first_page_text = reader.pages[0].extract_text() or ""
    first_page_identity = first_page_text.casefold()
    if (
        "somerville" not in first_page_identity
        or "traffic regulations" not in first_page_identity
    ):
        raise SourceValidationError(
            "Source PDF does not identify itself as Somerville Traffic Regulations."
        )

    pages = schedule_pages(reader)
    with pdfplumber.open(PDF_PATH) as document:
        schedules = {
            "D": schedule_rows(document, pages["D"], pages["E"]),
            "F": schedule_rows(document, pages["F"], pages["G"]),
        }

    streets: dict[str, dict] = {}
    for street in candidates:
        streets[street] = {
            "street_name": display_names.get(street, street),
            "has_metered_segment": False,
            "meter_count_estimate": None,
            "meter_count_confidence": "none",
            "has_time_limited_segment": False,
            "schedule_f_rows": [],
            "schedule_d_rows": [],
        }

    unmatched = []
    for schedule, rows in schedules.items():
        for row in rows:
            key = normalize_street_name(row["location"])
            if schedule == "F":
                row["action"] = meter_action(row["restriction"])
            if key not in streets:
                unmatched.append({"schedule": schedule, **row})
                continue
            streets[key][f"schedule_{schedule.lower()}_rows"].append(row)

    for record in streets.values():
        listed = [
            row
            for row in record["schedule_f_rows"]
            if row["action"] in {"meter_listed", "installation"}
        ]
        record["has_metered_segment"] = bool(listed)
        if listed:
            counts = [meter_count(row["restriction"]) for row in listed]
            record["meter_count_estimate"] = (
                sum(n for n in counts if n is not None) or None
            )
            # A regulation lists changes and quantities, not a current inventory.
            record["meter_count_confidence"] = (
                "approx" if record["meter_count_estimate"] else "unknown"
            )
        record["has_time_limited_segment"] = any(
            public_time_limit(row["restriction"]) for row in record["schedule_d_rows"]
        )
    if not any(r["has_metered_segment"] for r in streets.values()) or not any(
        r["has_time_limited_segment"] for r in streets.values()
    ):
        raise SourceValidationError("No usable Somerville meter or time-limit records")

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "parser_version": PARSER_VERSION,
        "source_pdf": PDF_PATH.name,
        "source": source_metadata(reader, first_page_text),
        "page_index": {
            f"schedule_{letter.lower()}_start_page": page + 1
            for letter, page in pages.items()
        },
        "unmatched_schedule_rows": unmatched,
        "streets": streets,
    }


def main():
    rules = build_rules()
    OUTPUT_PATH.write_text(json.dumps(rules, indent=2))
    streets = rules["streets"]
    metered = sum(1 for v in streets.values() if v["has_metered_segment"])
    limited = sum(1 for v in streets.values() if v["has_time_limited_segment"])
    print(f"Wrote {OUTPUT_PATH}")
    print(f"Streets with metered segments: {metered}")
    print(f"Streets with time-limited segments: {limited}")


if __name__ == "__main__":
    main()
