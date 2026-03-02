"""
Build a structured per-street parking rules dataset from Somerville traffic regulations.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from pypdf import PdfReader

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
PDF_PATH = DATA_DIR / "traffic-regulations.pdf"
STREETS_PATH = DATA_DIR / "streets.geojson"
OUTPUT_PATH = DATA_DIR / "parking_rules_by_street.json"
PDF_TEXT_PATH = DATA_DIR / "pdf_text.txt"


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


def split_lines(page_text: str) -> list[str]:
    return [line.strip() for line in page_text.splitlines() if line.strip()]


def find_schedule_page(reader: PdfReader, schedule_letter: str) -> int:
    pattern = re.compile(rf"^SCHEDULE\s+{schedule_letter}\s*$", re.IGNORECASE)
    matches = []
    for page_index, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        lines = split_lines(text)
        if any(pattern.match(line) for line in lines):
            matches.append(page_index)
    if not matches:
        return -1
    # The final occurrence is the actual schedule section, not the table of contents.
    return matches[-1]


def find_schedule_line(lines: list[str], schedule_letter: str) -> int:
    pattern = re.compile(rf"^\s*SCHEDULE\s+{schedule_letter}\s*$", re.IGNORECASE)
    matches = [i for i, line in enumerate(lines) if pattern.match(line.strip())]
    if not matches:
        return -1
    return matches[-1]


def extract_schedule_lines(reader: PdfReader, start_page: int, end_page: int) -> list[str]:
    if start_page == -1:
        return []
    if end_page == -1 or end_page <= start_page:
        end_page = len(reader.pages)

    out = []
    for page_index in range(start_page, end_page):
        text = reader.pages[page_index].extract_text() or ""
        out.extend(split_lines(text))
    return out


def extract_schedule_lines_from_text(
    lines: list[str], start_letter: str, end_letter: str
) -> list[str]:
    start = find_schedule_line(lines, start_letter)
    end = find_schedule_line(lines, end_letter) if end_letter else -1
    if start == -1:
        return []
    if end == -1 or end <= start:
        end = len(lines)
    out = [line.strip() for line in lines[start + 1:end] if line.strip()]
    return out


def find_street_prefix(line: str, candidates: list[str]) -> str | None:
    normalized_line = normalize_street_name(line)
    if not normalized_line:
        return None
    for name in candidates:
        if normalized_line == name or normalized_line.startswith(f"{name} "):
            return name
    return None


def rows_from_schedule(lines: list[str], candidates: list[str]) -> dict[str, list[str]]:
    rows: dict[str, list[str]] = {}
    current_street = None
    current_tokens: list[str] = []

    def flush_row():
        nonlocal current_street, current_tokens
        if current_street and current_tokens:
            merged = " ".join(current_tokens).strip()
            if merged:
                rows.setdefault(current_street, []).append(merged)
        current_street = None
        current_tokens = []

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if line.upper().startswith("SCHEDULE "):
            flush_row()
            continue
        if line.upper().startswith("LOCATION SIDE FROM TO"):
            continue
        if line.upper() == "RESTRICTIONS":
            continue

        street = find_street_prefix(line, candidates)
        if street:
            flush_row()
            current_street = street
            current_tokens = [line]
            continue

        if current_street:
            current_tokens.append(line)

    flush_row()
    return rows


def parse_meter_counts(row_text: str) -> tuple[int, bool]:
    text = row_text.upper()
    counts = 0
    saw_meter_word = "METER" in text

    for value in re.findall(r"(?<!#)(\d+)\s*(?:METER|METERS|METERED(?:\s+SPACES?)?)\b", text):
        counts += int(value)
    for value in re.findall(r"\((\d+)\)\s*\d+\s*MINUTE\s*METERS?\b", text):
        counts += int(value)

    return counts, saw_meter_word


def line_has_time_limit(row_text: str) -> bool:
    text = row_text.upper()
    patterns = [
        r"\b\d+\s*(?:HR|HRS|HOUR|HOURS)\b",
        r"\b\d+\s*(?:MIN|MINS|MINUTE|MINUTES)\b",
        r"\b\d{1,2}:\d{2}\s*(?:AM|PM)\s*-\s*\d{1,2}:\d{2}\s*(?:AM|PM)\b",
    ]
    return any(re.search(pattern, text) for pattern in patterns)


def build_rules() -> dict:
    candidates, display_names = load_street_candidates()
    reader = PdfReader(PDF_PATH)

    page_d = find_schedule_page(reader, "D")
    page_e = find_schedule_page(reader, "E")
    page_f = find_schedule_page(reader, "F")
    page_g = find_schedule_page(reader, "G")

    schedule_d_lines = extract_schedule_lines(reader, page_d, page_e)
    schedule_f_lines = extract_schedule_lines(reader, page_f, page_g)
    schedule_d_rows = rows_from_schedule(schedule_d_lines, candidates)
    schedule_f_rows = rows_from_schedule(schedule_f_lines, candidates)

    # Fallback for environments where direct PDF text extraction layout differs.
    if not schedule_d_rows and not schedule_f_rows and PDF_TEXT_PATH.exists():
        text_lines = PDF_TEXT_PATH.read_text(errors="ignore").splitlines()
        schedule_d_rows = rows_from_schedule(
            extract_schedule_lines_from_text(text_lines, "D", "E"),
            candidates,
        )
        schedule_f_rows = rows_from_schedule(
            extract_schedule_lines_from_text(text_lines, "F", "G"),
            candidates,
        )

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

    for street, rows in schedule_f_rows.items():
        record = streets[street]
        record["has_metered_segment"] = True
        record["schedule_f_rows"] = rows[:30]

        explicit_total = 0
        saw_implicit_meter = False
        for row in rows:
            row_count, saw_meter_word = parse_meter_counts(row)
            explicit_total += row_count
            if saw_meter_word and row_count == 0:
                saw_implicit_meter = True

        if explicit_total > 0 and not saw_implicit_meter:
            record["meter_count_estimate"] = explicit_total
            record["meter_count_confidence"] = "exact"
        elif explicit_total > 0:
            record["meter_count_estimate"] = explicit_total
            record["meter_count_confidence"] = "approx"
        else:
            record["meter_count_estimate"] = None
            record["meter_count_confidence"] = "unknown"

    for street, rows in schedule_d_rows.items():
        limited_rows = [row for row in rows if line_has_time_limit(row)]
        if not limited_rows:
            continue
        record = streets[street]
        record["has_time_limited_segment"] = True
        record["schedule_d_rows"] = limited_rows[:30]

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_pdf": PDF_PATH.name,
        "page_index": {
            "schedule_d_start_page": page_d + 1 if page_d >= 0 else None,
            "schedule_e_start_page": page_e + 1 if page_e >= 0 else None,
            "schedule_f_start_page": page_f + 1 if page_f >= 0 else None,
            "schedule_g_start_page": page_g + 1 if page_g >= 0 else None,
        },
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
