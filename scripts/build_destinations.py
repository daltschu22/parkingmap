"""Build the local street-address and named-building lookup from public MassGIS points."""

import gzip
import json
import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlencode
from urllib.request import urlopen

SOURCE = "https://arcgisserver.digital.mass.gov/arcgisserver/rest/services/AGOL/MassGIS_Master_Address_Points/FeatureServer/0"
OUTPUT = Path(__file__).resolve().parents[1] / "data/processed/destinations.json.gz"
CITIES = ("Cambridge", "Medford", "Somerville")
STATIONS = "https://api-v3.mbta.com/stops?filter%5Blocation_type%5D=1"
RESTAURANTS = "https://data.somervillema.gov/resource/nneb-s3f7.json"
FIELDS = "OBJECTID,FULL_NUMBER_STANDARDIZED,STREET_NAME,GEOGRAPHIC_TOWN,POSTCODE,BUILDING_NAME"
VENUES = [
    ("Sarma", "249 Pearl Street", "Somerville", "https://www.sarmarestaurant.com/"),
    ("Chevalier Theatre", "30 Forest Street", "Medford", "https://chevaliertheatre.com/event/bored-teachers-is-it-friday-yet-comedy-tour/"),
]


def query(**params):
    url = SOURCE + "/query?" + urlencode({"f": "json", **params})
    with urlopen(url, timeout=60) as response:
        data = json.load(response)
    if "error" in data:
        raise ValueError(data["error"])
    return data


def download_city(city):
    where = f"GEOGRAPHIC_TOWN = '{city.upper()}'"
    expected = query(where=where, returnCountOnly="true")["count"]
    # Seek by object ID so pages cannot overlap or skip records when IDs have gaps.
    last_id = 0
    total = 0
    addresses = defaultdict(set)
    names = set()
    while total < expected:
        batch = query(
            where=f"{where} AND OBJECTID > {last_id}",
            outFields=FIELDS,
            outSR=4326,
            orderByFields="OBJECTID",
            resultRecordCount=2000,
        )["features"]
        if not batch:
            break
        for feature in batch:
            row = feature["attributes"]
            if row["OBJECTID"] <= last_id or row["GEOGRAPHIC_TOWN"] != city.upper():
                raise ValueError(f"Unexpected MassGIS page for {city}")
            last_id = row["OBJECTID"]
            number, street = row["FULL_NUMBER_STANDARDIZED"], row["STREET_NAME"]
            if not number or not street or not feature.get("geometry"):
                continue
            label = f"{number} {street.title()}"
            geom = feature["geometry"]
            point = (round(geom["y"], 6), round(geom["x"], 6))
            if not (42.3 < point[0] < 42.5 and -71.24 < point[1] < -71.05):
                raise ValueError(f"Unexpected coordinates in {city}: {point}")
            key = (label, str(row["POSTCODE"] or "")[:5])
            addresses[key].add(point)
            if row["BUILDING_NAME"]:
                names.add((row["BUILDING_NAME"].strip().title(), label, *point))
        total += len(batch)
    if total != expected or not addresses:
        raise ValueError(f"Incomplete MassGIS snapshot for {city}: {total}/{expected}")
    rows = []
    for (label, zipcode), points in sorted(addresses.items()):
        # Use an actual published point for multi-building addresses.
        points = sorted(points)
        rows.append([label, zipcode, *points[len(points) // 2]])
    print(
        f"{city}: {total} source points, {len(rows)} addresses, {len(names)} building references",
        flush=True,
    )
    return city, {"addresses": rows, "places": sorted(names)}


def stations():
    with urlopen(STATIONS, timeout=30) as response:
        data = json.load(response)
    if data.get("links", {}).get("next"):
        raise ValueError("Incomplete MBTA station response")
    rows = []
    for record in data["data"]:
        stop = record["attributes"]
        if stop["municipality"] in CITIES:
            rows.append(
                [
                    record["id"],
                    stop["name"],
                    stop["municipality"],
                    stop["latitude"],
                    stop["longitude"],
                ]
            )
    if not rows:
        raise ValueError("Empty MBTA station inventory")
    return sorted(rows)


def restaurants():
    today = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    where = ("currently_active='1' and status='Issued' and expiration_date >= '" + today +
             "' and application_type in('Common Victualer (without alcohol)','Restaurant (with alcohol)')")
    params = {"$select": "application_id,project_description_or_business_name,application_address,"
                         "application_latitude,application_longitude", "$where": where,
              "$order": "application_id", "$limit": 1000}
    with urlopen(RESTAURANTS + "?" + urlencode(params), timeout=30) as response:
        records = json.load(response)
    if not records or len(records) == params["$limit"]:
        raise ValueError("Incomplete restaurant directory")
    entries = set()
    for record in records:
        name = (record.get("project_description_or_business_name") or "").strip()
        address = (record.get("application_address") or "").split(",")[0].strip().title()
        if not name or not address or not record.get("application_latitude") or not record.get("application_longitude"):
            continue
        lat, lon = (round(float(record["application_" + field]), 6) for field in ("latitude", "longitude"))
        if 42.37 < lat < 42.42 and -71.145 < lon < -71.07:
            entries.add((name, address, lat, lon))
    if not entries:
        raise ValueError("No geocoded restaurant addresses")
    return {"source": RESTAURANTS, "as_of": today, "entries": sorted(entries)}


def main():
    with ThreadPoolExecutor(max_workers=len(CITIES)) as pool:
        cities = dict(pool.map(download_city, CITIES))
    snapshot = {
        "source": SOURCE,
        "retrieved": datetime.now(timezone.utc).isoformat(),
        "cities": cities,
        "stations_source": STATIONS,
        "stations": stations(),
        "venues": VENUES,
        "somerville_restaurants": restaurants(),
    }
    data = gzip.compress(json.dumps(snapshot, separators=(",", ":")).encode(), mtime=0)
    with NamedTemporaryFile(dir=OUTPUT.parent, delete=False) as scratch:
        scratch.write(data)
        os.fchmod(scratch.fileno(), 0o644)
    os.replace(scratch.name, OUTPUT)


if __name__ == "__main__":
    main()
