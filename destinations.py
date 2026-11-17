"""Search public address points and named destinations in the covered municipalities."""

import gzip
import json
import re
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

SNAPSHOT = Path(__file__).parent / "data/processed/destinations.json.gz"
CITIES = ("Cambridge", "Medford", "Somerville")


def normalize_street_name(value: str | None) -> str:
    """Normalize street names for loose matching across GIS and PDF text."""
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


@lru_cache(maxsize=1)
def destination_index():
    with gzip.open(SNAPSHOT, "rt") as stream:
        data = json.load(stream)
    numbers = defaultdict(list)
    places = []
    for city, records in data["cities"].items():
        for name, zipcode, lat, lon in records["addresses"]:
            key = normalize_street_name(name)
            numbers[key.split()[0]].append((key, city, zipcode, name, (lat, lon)))
        # Alternate building points at the same civic address are a single search result.
        seen = set()
        for name, address, lat, lon in records["places"]:
            if (name, address) not in seen:
                places.append(
                    (
                        normalize_street_name(name),
                        name,
                        address,
                        city,
                        (lat, lon),
                        "Building",
                    )
                )
                seen.add((name, address))
    for venue, address, town, _source in data.get("venues", []):
        key = normalize_street_name(address)
        matches = {p for n, city, _, _, p in numbers[key.split()[0]] if n == key and city == town}
        if len(matches) == 1:
            places.append((normalize_street_name(venue), venue, address, town, matches.pop(), "Venue"))
    verified = {(key, city, point): address for key, _, address, city, point, kind in places if kind == "Venue"}
    for name, address, lat, lon in data.get("somerville_restaurants", {}).get("entries", []):
        key = normalize_street_name(name)
        point = (lat, lon)
        address = verified.get((key, "Somerville", point), address)
        places.append((key, name, address, "Somerville", point, "Restaurant"))
    square_stations = {
        "place-davis": "Davis Square",
        "place-portr": "Porter Square",
        "place-cntsq": "Central Square",
        "place-harsq": "Harvard Square",
        "place-knncl": "Kendall Square",
    }
    for stop_id, name, city, lat, lon in data["stations"]:
        station = (f"{name} Station", "", city, (lat, lon), "MBTA station")
        for alias in {name, station[0], square_stations.get(stop_id, name)}:
            places.append((normalize_street_name(alias), *station))
    return numbers, places


def exact_address(label, city):
    key = normalize_street_name(label)
    matches = {
        point
        for name, town, _, _, point in destination_index()[0].get(key.split()[0], ())
        if name == key and town == city
    }
    return next(iter(matches)) if len(matches) == 1 else None


def search_destinations(query, facilities):
    if re.match(r"^\s*\d", query):
        query = re.sub(
            r"(?:,?\s+(?:apt\.?|unit|suite|ste\.?)\s*#?\s*|\s+#)\w+(?:-\w+)?\b",
            "",
            query,
            flags=re.I,
        )
    key = normalize_street_name(query)
    key = re.sub(r"\bMASS AVE\b", "MASSACHUSETTS AVE", key)
    zip_match = re.search(r"\s(\d{5})(?: \d{4})?$", key)
    zipcode = zip_match[1] if zip_match else None
    if zip_match:
        key = key[: zip_match.start()]
    key = re.sub(r"\s+(?:MA|MASSACHUSETTS)(?: USA)?$", "", key)
    full_key = key
    city = next((c for c in CITIES if key.endswith(" " + c.upper())), None)
    if city:
        key = key[: -len(city)].strip()
    if len(key) < 2:
        return []
    numbers, places = destination_index()
    results = []
    if key[0].isdigit():
        if " " not in key:
            return []
        for candidate, town, postal, name, point in numbers.get(key.split()[0], ()):
            if (city and town != city) or (zipcode and postal != zipcode):
                continue
            if candidate == key or candidate.startswith(key + " "):
                results.append(
                    (int(candidate != key), name, "", town, point, "Street address")
                )
    elif not zipcode:
        facility_names = [
            (
                normalize_street_name(p["properties"]["NAME"]),
                p["properties"]["NAME"],
                p["properties"].get("ADDRESS") or "",
                p["properties"]["MUNICIPALITY"],
                tuple(reversed(p["geometry"]["coordinates"][:2])),
                "Parking facility",
            )
            for p in facilities
        ]
        for candidate, name, address, town, point, kind in places + facility_names:
            if city and town != city:
                continue
            term = key
            if kind == "Restaurant":
                term = re.sub(r"\s+RESTAURANT$", "", key)
            if candidate in {term, full_key}:
                rank = 0
            elif all(word in candidate.split() for word in term.split()):
                rank = 1
            else:
                continue
            results.append((rank, name, address, town, point, kind))
    unique = {}
    best_rank = min((row[0] for row in results), default=None)
    for rank, name, address, town, point, kind in sorted(results):
        if rank != best_rank:
            break
        unique.setdefault(
            (name, address, town, point),
            dict(name=name, city=town, address=address, point=point, kind=kind),
        )
    return list(unique.values())[:10]
