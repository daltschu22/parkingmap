"""Build a shared, source-backed public parking facility layer.

Cambridge publishes municipal lot/garage points as GeoJSON. Somerville embeds a
detailed Google My Maps KML on its official Parking Department page. Assembly
Row separately publishes operator-maintained visitor garage/lot pages and map
points. The source formats stay provider-specific here, while the emitted
feature properties use one shared schema for the map.
"""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


BASE_DIR = Path(__file__).parent
OUTPUT_PATH = BASE_DIR / "data" / "processed" / "public_parking_facilities.geojson"
SOMERVILLE_RAW_PATH = (
    BASE_DIR / "data" / "raw" / "somerville" / "municipal-parking-lots.kml"
)
SOMERVILLE_PAGE_RAW_PATH = (
    BASE_DIR / "data" / "raw" / "somerville" / "parking-department.html"
)
CAMBRIDGE_DETAILS_RAW_PATH = (
    BASE_DIR / "data" / "raw" / "cambridge" / "public-parking.html"
)
ASSEMBLY_RAW_DIR = (
    BASE_DIR / "data" / "raw" / "somerville" / "assembly-row-parking"
)
ASSEMBLY_PAGE_RAW_PATH = ASSEMBLY_RAW_DIR / "index.txt"
ASSEMBLY_MARKERS_RAW_PATH = ASSEMBLY_RAW_DIR / "markers.json"

CAMBRIDGE_LOTS_URL = (
    "https://raw.githubusercontent.com/cambridgegis/cambridgegis_data/main/"
    "Traffic/Municipal_Parking_Lots/TRAFFIC_MunicipalParkingLots.geojson"
)
CAMBRIDGE_SOURCE_URL = (
    "https://www.cambridgema.gov/gis/gisdatadictionary/traffic/"
    "traffic_municipalparkinglots"
)
CAMBRIDGE_DETAILS_URL = "https://www.cambridgema.gov/iwantto/parkacarincambridge"

SOMERVILLE_PARKING_PAGE_URL = (
    "https://www.somervillema.gov/departments/parking-department"
)
SOMERVILLE_LOTS_KML_URL = (
    "https://www.google.com/maps/d/kml?"
    "mid=1Vs3VLhrTWksBWwmPl6lA-fHoPbzRH5Y&forcekml=1"
)
SOMERVILLE_LOTS_MAP_ID = "1Vs3VLhrTWksBWwmPl6lA-fHoPbzRH5Y"

ASSEMBLY_PARKING_URL = "https://www.assemblyparking.com/"
ASSEMBLY_MARKERS_URL = "https://www.assemblyparking.com/wp-json/wpgmza/v1/markers"
ASSEMBLY_GARAGE_REGULATIONS = (
    "0-3 hours free; 3-4 hours $3; 4-5 hours $5; 5-6 hours $15; "
    "6+ hours $27. Each new business day begins at 5 a.m.; staying past "
    "5 a.m. incurs another day's fees and the free 3 hours no longer applies."
)
ASSEMBLY_MARKETPLACE_REGULATIONS = (
    "Assembly Marketplace customers receive up to 3 hours of complimentary "
    "outdoor parking. Vehicles parked longer than 3 hours are subject to towing."
)

# The operator's public map API supplies exact points. Its address field is
# incomplete for two facilities and has a typo for Artisan West, so current
# driver-facing addresses are validated against the corresponding detail pages.
ASSEMBLY_FACILITIES = {
    "assembly-marketplace": {
        "marker_id": "225",
        "map_id": "101",
        "marker_title": "Marketplace Garage",
        "name": "Assembly Marketplace",
        "facility_type": "Public Visitor Parking Lot",
        "address": "Assembly Marketplace, Somerville, MA 02145",
        "details_url": f"{ASSEMBLY_PARKING_URL}where-to-park/assembly-marketplace/",
        "regulations": ASSEMBLY_MARKETPLACE_REGULATIONS,
        "evidence": (
            "Assembly Marketplace customers are offered convenient outdoor parking",
            "Complimentary parking is available for 3-hours",
            "subject to being towed",
        ),
    },
    "canal-street-garage": {
        "marker_id": "228",
        "map_id": "102",
        "marker_title": "Canal Street Garage",
        "name": "Canal Street Garage",
        "facility_type": "Public Parking Garage",
        "address": "449 Canal Street, Somerville, MA 02145",
        "details_url": f"{ASSEMBLY_PARKING_URL}where-to-park/canal-street-garage/",
        "regulations": ASSEMBLY_GARAGE_REGULATIONS,
        "evidence": ("449 Canal Street", "0 - 3 Hours", "6+ Hours", "$27.00"),
    },
    "mass-general-brigham-garage": {
        "marker_id": "222",
        "map_id": "99",
        "marker_title": "MGB Garage",
        "name": "Mass General Brigham Garage",
        "facility_type": "Public Parking Garage",
        "address": "255 Grand Union Boulevard, Somerville, MA 02145",
        "details_url": f"{ASSEMBLY_PARKING_URL}where-to-park/partners-garage/",
        "regulations": ASSEMBLY_GARAGE_REGULATIONS,
        "evidence": (
            "Mass General Brigham Garage Parking",
            "255 Grand Union Blvd",
            "0 - 3 Hours",
            "$27.00",
        ),
    },
    "foley-street-garage": {
        "marker_id": "280",
        "map_id": "146",
        "marker_title": "Foley Street Garage",
        "name": "Foley Street Garage",
        "facility_type": "Public Parking Garage",
        "address": "350 Foley Street, Somerville, MA 02145",
        "details_url": f"{ASSEMBLY_PARKING_URL}where-to-park/foley-street-garage/",
        "regulations": ASSEMBLY_GARAGE_REGULATIONS,
        "evidence": ("350 Foley St", "0 - 3 Hours", "6+ Hours", "$27.00"),
    },
    "artisan-west-garage": {
        "marker_id": "129",
        "map_id": "43",
        "marker_title": "Artisan West Garage",
        "name": "Artisan West Garage",
        "facility_type": "Public Parking Garage",
        "address": "355 Artisan Way, Somerville, MA 02145",
        "details_url": f"{ASSEMBLY_PARKING_URL}where-to-park/artisian-west-garage/",
        "regulations": ASSEMBLY_GARAGE_REGULATIONS,
        "evidence": ("355 Artisan Way", "0 - 3 Hours", "6+ Hours", "$27.00"),
    },
    "artisan-east-garage": {
        "marker_id": "123",
        "map_id": "41",
        "marker_title": "Artisan East Garage",
        "name": "Artisan East Garage",
        "facility_type": "Public Parking Garage",
        "address": "451 Artisan Way, Somerville, MA 02145",
        "details_url": f"{ASSEMBLY_PARKING_URL}where-to-park/artisian-east-garage/",
        "regulations": ASSEMBLY_GARAGE_REGULATIONS,
        "evidence": ("451 Artisan Way", "0 - 3 Hours", "6+ Hours", "$27.00"),
    },
    "great-river-garage": {
        "marker_id": "126",
        "map_id": "42",
        "marker_title": "Great River Garage",
        "name": "Great River Garage",
        "facility_type": "Public Parking Garage",
        "address": "333 Great River Road, Somerville, MA 02145",
        "details_url": f"{ASSEMBLY_PARKING_URL}where-to-park/great-river-garage/",
        "regulations": ASSEMBLY_GARAGE_REGULATIONS,
        "evidence": ("333 Great River Rd", "0 - 3 Hours", "6+ Hours", "$27.00"),
    },
}

KML_NAMESPACE = {"kml": "http://www.opengis.net/kml/2.2"}

# The GIS point layer retains assessor/parcel addresses that differ from the
# current driver-facing entrances on Cambridge's official parking page.
CAMBRIDGE_CURRENT_ADDRESSES = {
    "Municipal Lot 2": "110 Mt. Auburn Street",
    "Municipal Lot 4": "96 Bishop Allen Drive",
    "Municipal Lot 5": "84 Bishop Allen Drive",
    "Municipal Lot 6": "36 Bishop Allen Drive",
    "Municipal Lot 8": "375 Green Street",
    "Municipal Lot 9": "9 Pleasant Street",
    "Municipal Lot 11": "984 Cambridge Street",
    "Municipal Lot 12": "Macarelli Way / 9 Warren Street",
    "Municipal Lot 14": "15 Springfield Street",
    "Green Street Parking Garage": "260 Green Street",
    "East Cambridge/First Street Parking Garage": (
        "First Street entrance across from 10 Spring Street"
    ),
}

CAMBRIDGE_PARKING_REGULATIONS = {
    "Municipal Lot 2": (
        "Payment required Monday-Saturday except holidays. 8 a.m.-6 p.m.: "
        "2-hour maximum, $3.00/hour; 6-10 p.m.: 4-hour maximum, $3.00/hour."
    ),
    "Municipal Lot 4": (
        "Payment required Monday-Saturday except holidays. 8 a.m.-6 p.m.: "
        "2-hour maximum, $1.25/hour; 6-10 p.m.: 4-hour maximum, $2.00/hour."
    ),
    "Municipal Lot 5": (
        "Payment required Monday-Saturday except holidays. 8 a.m.-6 p.m.: "
        "2-hour maximum, $1.25/hour; 6-10 p.m.: 4-hour maximum, $2.00/hour."
    ),
    "Municipal Lot 6": (
        "Payment required Monday-Saturday except holidays. 8 a.m.-6 p.m.: "
        "4-hour maximum, $1.25/hour; 6-10 p.m.: 4-hour maximum, $2.00/hour."
    ),
    "Municipal Lot 8": (
        "Payment required Monday-Saturday except holidays. 8 a.m.-6 p.m.: "
        "2-hour maximum, $1.25/hour; 6-10 p.m.: 4-hour maximum, $2.00/hour."
    ),
    "Municipal Lot 9": (
        "Payment required Monday-Saturday except holidays. 8 a.m.-6 p.m.: "
        "2-hour maximum, $1.25/hour; 6-10 p.m.: 4-hour maximum, $2.00/hour."
    ),
    "Municipal Lot 11": (
        "Payment required Monday-Saturday except holidays, 8 a.m.-10 p.m.: "
        "2-hour maximum, $1.25/hour. Cambridge resident permits do not pay after 6 p.m."
    ),
    "Municipal Lot 12": (
        "Payment required Monday-Saturday except holidays, 8 a.m.-6 p.m.: "
        "4-hour maximum, $0.50/hour."
    ),
    "Municipal Lot 14": (
        "Payment required Monday-Saturday except holidays. 8 a.m.-6 p.m.: "
        "2-hour maximum, $1.25/hour; 6-10 p.m.: 4-hour maximum, $2.00/hour."
    ),
    "Green Street Parking Garage": (
        "Open 24/7. Hourly, night, and weekend rates vary; see the official parking page."
    ),
    "East Cambridge/First Street Parking Garage": (
        "Open 24/7. Hourly, night, and weekend rates vary; see the official parking page."
    ),
}

CAMBRIDGE_EV_FACILITIES = {
    "Municipal Lot 5",
    "Municipal Lot 8",
    "Municipal Lot 9",
    "Municipal Lot 12",
    "East Cambridge/First Street Parking Garage",
}

CAMBRIDGE_DETAILS_EVIDENCE = {
    "110 Mt. Auburn Street",
    "96 Bishop Allen Drive",
    "84 Bishop Allen Drive",
    "36 Bishop Allen Drive",
    "375 Green Street",
    "9 Pleasant Street",
    "984 Cambridge Street",
    "Macarelli Way / 9 Warren Street",
    "15 Springfield Street",
    "Across from 10 Spring St",
    "260 Green St",
    "2-hour maximum | $3.00 per hour",
    "2-hour maximum | $1.25 per hour",
    "4-hour maximum | $1.25 per hour",
    "4-hour maximum | $2.00 per hour",
    "4-hour maximum | $0.50 per hour",
    "The garage is open 24/7",
}


class SourceValidationError(ValueError):
    """Raised before generated output is replaced by an implausible source."""


def fetch_bytes(url: str) -> tuple[bytes, dict]:
    request = Request(
        url,
        headers={
            "User-Agent": (
                "parkingmap public parking facility builder; official-source audit"
            )
        },
    )
    with urlopen(request, timeout=60) as response:
        content = response.read()
        if not content:
            raise SourceValidationError(f"Downloaded an empty source: {url}")
        metadata = {
            "url": url,
            "final_url": response.geturl(),
            "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
            "content_type": response.headers.get("Content-Type"),
            "last_modified": response.headers.get("Last-Modified"),
            "etag": response.headers.get("ETag"),
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        return content, metadata


def clean_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def slug(value: object) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", clean_text(value).casefold()).strip("-")
    return normalized or "unknown"


def parse_integer(value: object) -> int | None:
    text = clean_text(value)
    if not text:
        return None
    try:
        parsed = float(text)
    except ValueError:
        return None
    return int(parsed) if parsed.is_integer() and parsed >= 0 else None


def parse_cambridge_facilities(payload: dict) -> list[dict]:
    if payload.get("type") != "FeatureCollection":
        raise SourceValidationError("Cambridge municipal parking source is not GeoJSON")

    features = []
    for source_feature in payload.get("features", []):
        geometry = source_feature.get("geometry") or {}
        props = source_feature.get("properties") or {}
        coordinates = geometry.get("coordinates") or []
        if geometry.get("type") != "Point" or len(coordinates) < 2:
            continue

        name = clean_text(props.get("SiteName"))
        facility_type = clean_text(props.get("Type"))
        global_id = clean_text(props.get("GlobalID"))
        gis_address = clean_text(props.get("Address"))
        if not name or not facility_type or not global_id:
            continue
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [float(coordinates[0]), float(coordinates[1])],
                },
                "properties": {
                    "FACILITY_ID": f"cambridge:{global_id.strip('{}').casefold()}",
                    "MUNICIPALITY": "Cambridge",
                    "NAME": name,
                    "FACILITY_TYPE": facility_type,
                    "ADDRESS": CAMBRIDGE_CURRENT_ADDRESSES.get(name, gis_address),
                    "GIS_ADDRESS": gis_address,
                    "OWNERSHIP_TYPE": "Municipal",
                    "OPERATOR": "City of Cambridge",
                    "PUBLIC_ACCESS": "City-managed paid public parking",
                    "PARKING_REGULATIONS": CAMBRIDGE_PARKING_REGULATIONS.get(
                        name,
                        "Paid public parking; check posted rates, hours, and time limits.",
                    ),
                    "TOTAL_SPACES": None,
                    "ACCESSIBLE_PARKING": (
                        "No payment is required at lots for a visible disability "
                        "plate or placard; check posted facility information."
                        if facility_type == "Municipal Parking Lot"
                        else "See posted garage information"
                    ),
                    "EV_CHARGING": (
                        "Available"
                        if name in CAMBRIDGE_EV_FACILITIES
                        else "Not listed on the official parking page"
                    ),
                    "SPECIAL_RESTRICTIONS": "",
                    "SNOW_EMERGENCY_PARKING": "See current City guidance",
                    "SOURCE_TITLE": "Cambridge GIS Municipal Parking Lots",
                    "SOURCE_URL": CAMBRIDGE_SOURCE_URL,
                    "DETAILS_URL": CAMBRIDGE_DETAILS_URL,
                    "SOURCE_FORMAT": "geojson",
                    "SOURCE_CONFIDENCE": "high",
                },
            }
        )

    type_counts = Counter(
        feature["properties"]["FACILITY_TYPE"] for feature in features
    )
    if len(features) != 11:
        raise SourceValidationError(
            f"Expected 11 Cambridge municipal facilities, received {len(features)}"
        )
    if type_counts != {
        "Municipal Parking Lot": 9,
        "Municipal Parking Garage": 2,
    }:
        raise SourceValidationError(
            f"Unexpected Cambridge facility types: {dict(type_counts)}"
        )
    return features


def visible_html_text(content: bytes) -> str:
    class VisibleTextParser(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.hidden_depth = 0
            self.parts: list[str] = []

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag.casefold() in {"script", "style", "template", "noscript"}:
                self.hidden_depth += 1

        def handle_endtag(self, tag: str) -> None:
            if (
                tag.casefold() in {"script", "style", "template", "noscript"}
                and self.hidden_depth
            ):
                self.hidden_depth -= 1

        def handle_data(self, data: str) -> None:
            if not self.hidden_depth:
                self.parts.append(data)

    parser = VisibleTextParser()
    parser.feed(content.decode("utf-8", errors="replace"))
    return clean_text(unescape(" ".join(parser.parts)))


def visible_text_snapshot(content: bytes) -> bytes:
    """Preserve source evidence without third-party scripts or configuration."""
    return (visible_html_text(content) + "\n").encode()


def preserved_metadata(metadata: dict, content: bytes, preservation: str) -> dict:
    """Describe both the fetched response and the safe persisted representation."""
    return {
        **metadata,
        "source_bytes": metadata["bytes"],
        "source_sha256": metadata["sha256"],
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "preservation": preservation,
    }


def validate_cambridge_details_page(content: bytes) -> None:
    """Ensure the driver-facing rates used above still appear on the City page."""
    prefix = content[:4096].lstrip().lower()
    if b"<html" not in prefix and b"<!doctype html" not in prefix:
        raise SourceValidationError("Cambridge public parking details source is not HTML")
    page_text = visible_html_text(content).casefold()
    missing = [
        evidence
        for evidence in sorted(CAMBRIDGE_DETAILS_EVIDENCE)
        if clean_text(evidence).casefold() not in page_text
    ]
    if missing:
        raise SourceValidationError(
            "Cambridge public parking details changed; missing expected text: "
            + ", ".join(missing)
        )


def _kml_data(placemark: ET.Element) -> dict[str, str]:
    values = {}
    for node in placemark.findall(".//kml:Data", KML_NAMESPACE):
        name = clean_text(node.attrib.get("name"))
        value = clean_text(node.findtext("kml:value", namespaces=KML_NAMESPACE))
        if name:
            values[name] = value
    return values


def _kml_point(placemark: ET.Element) -> list[float] | None:
    raw = clean_text(
        placemark.findtext(".//kml:Point/kml:coordinates", namespaces=KML_NAMESPACE)
    )
    parts = raw.split(",")
    if len(parts) < 2:
        return None
    try:
        return [float(parts[0]), float(parts[1])]
    except ValueError:
        return None


def parse_somerville_facilities(content: bytes) -> list[dict]:
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise SourceValidationError("Somerville municipal lot KML is invalid") from exc

    features = []
    for placemark in root.findall(".//kml:Placemark", KML_NAMESPACE):
        name = clean_text(placemark.findtext("kml:name", namespaces=KML_NAMESPACE))
        coordinates = _kml_point(placemark)
        values = _kml_data(placemark)
        if not name or not coordinates:
            continue

        total_spaces = parse_integer(values.get("Total Number of Spaces"))
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": coordinates},
                "properties": {
                    "FACILITY_ID": f"somerville:{slug(name)}",
                    "MUNICIPALITY": "Somerville",
                    "NAME": name,
                    "FACILITY_TYPE": "Municipal Parking Lot",
                    "ADDRESS": clean_text(values.get("description")),
                    "OWNERSHIP_TYPE": "Municipal",
                    "OPERATOR": "City of Somerville",
                    "PUBLIC_ACCESS": "Public parking with facility-specific restrictions",
                    "PARKING_REGULATIONS": clean_text(
                        values.get("Parking Regulations")
                    ),
                    "TOTAL_SPACES": total_spaces,
                    "ACCESSIBLE_PARKING": clean_text(
                        values.get("Accessible Parking")
                    ),
                    "EV_CHARGING": clean_text(
                        values.get("Electric Vehicle Charging")
                    ),
                    "SPECIAL_RESTRICTIONS": clean_text(
                        values.get("Special Restrictions")
                    ),
                    "SNOW_EMERGENCY_PARKING": clean_text(
                        values.get("Is Parking Allow During a Snow Emergency?")
                    ),
                    "SOURCE_TITLE": "Somerville Map of Municipal Parking Lots",
                    "SOURCE_URL": SOMERVILLE_PARKING_PAGE_URL,
                    "DETAILS_URL": SOMERVILLE_PARKING_PAGE_URL,
                    "SOURCE_FORMAT": "official-page-linked-kml",
                    "SOURCE_CONFIDENCE": "high",
                },
            }
        )

    if len(features) != 13:
        raise SourceValidationError(
            f"Expected 13 Somerville municipal lots, received {len(features)}"
        )
    if any(feature["properties"]["TOTAL_SPACES"] is None for feature in features):
        raise SourceValidationError("Somerville lot capacity is missing or invalid")
    return features


def validate_somerville_authority_page(content: bytes) -> None:
    """Confirm the official page still publishes the map being ingested."""
    prefix = content[:4096].lstrip().lower()
    if b"<html" not in prefix and b"<!doctype html" not in prefix:
        raise SourceValidationError("Somerville Parking Department source is not HTML")
    if SOMERVILLE_LOTS_MAP_ID.encode() not in content:
        raise SourceValidationError(
            "Somerville's official page no longer embeds the expected municipal lot map"
        )


def validate_assembly_authority_page(content: bytes) -> None:
    """Confirm the operator still links every facility being published."""
    prefix = content[:4096].lstrip().lower()
    if b"<html" not in prefix and b"<!doctype html" not in prefix:
        raise SourceValidationError("Assembly Row parking source is not HTML")
    decoded = content.decode("utf-8", errors="replace")
    missing = [
        urlsplit(config["details_url"]).path
        for config in ASSEMBLY_FACILITIES.values()
        if urlsplit(config["details_url"]).path not in decoded
    ]
    if missing:
        raise SourceValidationError(
            "Assembly Row parking index changed; missing facility links: "
            + ", ".join(missing)
        )


def validate_assembly_detail_page(content: bytes, config: dict) -> None:
    prefix = content[:4096].lstrip().lower()
    if b"<html" not in prefix and b"<!doctype html" not in prefix:
        raise SourceValidationError(
            f"Assembly Row detail source is not HTML: {config['name']}"
        )
    page_text = visible_html_text(content).casefold()
    missing = [
        evidence
        for evidence in config["evidence"]
        if clean_text(evidence).casefold() not in page_text
    ]
    if missing:
        raise SourceValidationError(
            f"Assembly Row parking details changed for {config['name']}; "
            "missing expected text: " + ", ".join(missing)
        )


def parse_assembly_facilities(payload: object) -> list[dict]:
    if not isinstance(payload, list):
        raise SourceValidationError("Assembly Row parking marker source is not a list")

    markers = {
        (clean_text(marker.get("map_id")), clean_text(marker.get("id"))): marker
        for marker in payload
        if isinstance(marker, dict)
    }
    features = []
    for key, config in ASSEMBLY_FACILITIES.items():
        marker = markers.get((config["map_id"], config["marker_id"]))
        if not marker:
            raise SourceValidationError(
                f"Assembly Row map marker is missing for {config['name']}"
            )
        if clean_text(marker.get("title")) != config["marker_title"]:
            raise SourceValidationError(
                f"Assembly Row marker title changed for {config['name']}"
            )
        try:
            coordinates = [float(marker["lng"]), float(marker["lat"])]
        except (KeyError, TypeError, ValueError) as exc:
            raise SourceValidationError(
                f"Assembly Row marker coordinate is invalid for {config['name']}"
            ) from exc

        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": coordinates},
                "properties": {
                    "FACILITY_ID": f"somerville:assembly-row:{key}",
                    "MUNICIPALITY": "Somerville",
                    "NAME": config["name"],
                    "FACILITY_TYPE": config["facility_type"],
                    "ADDRESS": config["address"],
                    "OWNERSHIP_TYPE": "Private facility with public visitor access",
                    "OPERATOR": "Assembly Row Parking (SP Plus)",
                    "PUBLIC_ACCESS": (
                        "Assembly Marketplace customer parking"
                        if key == "assembly-marketplace"
                        else "Public visitor parking for Assembly Row"
                    ),
                    "PARKING_REGULATIONS": config["regulations"],
                    "TOTAL_SPACES": None,
                    "ACCESSIBLE_PARKING": "See posted facility information",
                    "EV_CHARGING": "Not verified in the operator source",
                    "SPECIAL_RESTRICTIONS": "",
                    "SNOW_EMERGENCY_PARKING": "Not listed",
                    "SOURCE_TITLE": "Assembly Row Parking",
                    "SOURCE_URL": ASSEMBLY_PARKING_URL,
                    "DETAILS_URL": config["details_url"],
                    "SOURCE_FORMAT": "operator-html-and-map-json",
                    "SOURCE_CONFIDENCE": "high",
                },
            }
        )

    type_counts = Counter(
        feature["properties"]["FACILITY_TYPE"] for feature in features
    )
    if type_counts != {
        "Public Parking Garage": 6,
        "Public Visitor Parking Lot": 1,
    }:
        raise SourceValidationError(
            f"Unexpected Assembly Row facility types: {dict(type_counts)}"
        )
    return features


def validate_combined(features: list[dict]) -> None:
    ids = [feature["properties"]["FACILITY_ID"] for feature in features]
    if len(ids) != len(set(ids)):
        raise SourceValidationError("Public parking facility IDs are not unique")
    for feature in features:
        lon, lat = feature["geometry"]["coordinates"]
        if not (-71.20 < lon < -70.95 and 42.30 < lat < 42.50):
            raise SourceValidationError(
                f"Facility coordinate is outside the expected region: {lon}, {lat}"
            )


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        handle.write(content)
        temp_path = Path(handle.name)
    temp_path.replace(path)


def build() -> tuple[dict, list[tuple[Path, bytes, dict]]]:
    cambridge_content, cambridge_metadata = fetch_bytes(CAMBRIDGE_LOTS_URL)
    cambridge_details_content, cambridge_details_metadata = fetch_bytes(
        CAMBRIDGE_DETAILS_URL
    )
    somerville_page_content, somerville_page_metadata = fetch_bytes(
        SOMERVILLE_PARKING_PAGE_URL
    )
    somerville_content, somerville_metadata = fetch_bytes(SOMERVILLE_LOTS_KML_URL)
    assembly_page_content, assembly_page_metadata = fetch_bytes(ASSEMBLY_PARKING_URL)
    assembly_markers_content, assembly_markers_metadata = fetch_bytes(
        ASSEMBLY_MARKERS_URL
    )
    assembly_details = {}
    for key, config in ASSEMBLY_FACILITIES.items():
        content, metadata = fetch_bytes(config["details_url"])
        validate_assembly_detail_page(content, config)
        assembly_details[key] = (content, metadata)
    try:
        cambridge_payload = json.loads(cambridge_content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceValidationError("Cambridge municipal lot GeoJSON is invalid") from exc
    try:
        assembly_markers_payload = json.loads(assembly_markers_content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceValidationError("Assembly Row parking markers JSON is invalid") from exc

    validate_cambridge_details_page(cambridge_details_content)
    validate_somerville_authority_page(somerville_page_content)
    validate_assembly_authority_page(assembly_page_content)
    cambridge_features = parse_cambridge_facilities(cambridge_payload)
    somerville_municipal_features = parse_somerville_facilities(somerville_content)
    assembly_features = parse_assembly_facilities(assembly_markers_payload)
    marker_lookup = {
        (clean_text(marker.get("map_id")), clean_text(marker.get("id"))): marker
        for marker in assembly_markers_payload
        if isinstance(marker, dict)
    }
    selected_assembly_markers = [
        marker_lookup[(config["map_id"], config["marker_id"])]
        for config in ASSEMBLY_FACILITIES.values()
    ]
    assembly_page_snapshot = visible_text_snapshot(assembly_page_content)
    assembly_markers_snapshot = (
        json.dumps(selected_assembly_markers, indent=2) + "\n"
    ).encode()
    assembly_detail_snapshots = {
        key: visible_text_snapshot(content)
        for key, (content, _) in assembly_details.items()
    }
    assembly_page_preserved_metadata = preserved_metadata(
        assembly_page_metadata, assembly_page_snapshot, "visible-text-snapshot"
    )
    assembly_markers_preserved_metadata = preserved_metadata(
        assembly_markers_metadata,
        assembly_markers_snapshot,
        "selected-seven-marker-records",
    )
    assembly_details_preserved_metadata = {
        key: preserved_metadata(
            metadata, assembly_detail_snapshots[key], "visible-text-snapshot"
        )
        for key, (_, metadata) in assembly_details.items()
    }
    somerville_features = somerville_municipal_features + assembly_features
    features = cambridge_features + somerville_features
    validate_combined(features)

    now = datetime.now(timezone.utc).isoformat()
    output = {
        "type": "FeatureCollection",
        "properties": {
            "generated_at_utc": now,
            "feature_count": len(features),
            "municipality_counts": {
                "Cambridge": len(cambridge_features),
                "Somerville": len(somerville_features),
            },
            "sources": {
                "cambridge": {
                    **cambridge_metadata,
                    "authority_url": CAMBRIDGE_SOURCE_URL,
                    "details_url": CAMBRIDGE_DETAILS_URL,
                },
                "cambridge_details": cambridge_details_metadata,
                "somerville_authority_page": somerville_page_metadata,
                "somerville_lots": {
                    **somerville_metadata,
                    "authority_url": SOMERVILLE_PARKING_PAGE_URL,
                },
                "assembly_parking_page": assembly_page_preserved_metadata,
                "assembly_parking_markers": assembly_markers_preserved_metadata,
                "assembly_parking_details": assembly_details_preserved_metadata,
            },
            "note": (
                "Facilities are public parking locations, not a promise that a space is "
                "currently available. Posted rates, hours, permits, temporary closures, "
                "and other restrictions control."
            ),
        },
        "features": features,
    }
    somerville_raw_metadata = {
        "id": "somerville_municipal_parking_lots_kml",
        "title": "Somerville Map of Municipal Parking Lots",
        "municipality": "somerville",
        "category": "public_parking_facilities",
        "authority_url": SOMERVILLE_PARKING_PAGE_URL,
        "destination": str(SOMERVILLE_RAW_PATH.relative_to(BASE_DIR)),
        **somerville_metadata,
    }
    somerville_page_raw_metadata = {
        "id": "somerville_parking_department_page",
        "title": "Somerville Parking Department",
        "municipality": "somerville",
        "category": "source_index",
        "destination": str(SOMERVILLE_PAGE_RAW_PATH.relative_to(BASE_DIR)),
        **somerville_page_metadata,
    }
    cambridge_details_raw_metadata = {
        "id": "cambridge_public_parking_details",
        "title": "Cambridge Public Parking Lots and Garages",
        "municipality": "cambridge",
        "category": "public_parking_facilities",
        "destination": str(CAMBRIDGE_DETAILS_RAW_PATH.relative_to(BASE_DIR)),
        **cambridge_details_metadata,
    }
    assembly_page_raw_metadata = {
        "id": "assembly_row_public_parking",
        "title": "Assembly Row Parking",
        "municipality": "somerville",
        "category": "public_parking_facilities",
        "destination": str(ASSEMBLY_PAGE_RAW_PATH.relative_to(BASE_DIR)),
        **assembly_page_preserved_metadata,
    }
    assembly_markers_raw_metadata = {
        "id": "assembly_row_parking_markers",
        "title": "Assembly Row Parking Map Markers",
        "municipality": "somerville",
        "category": "public_parking_facilities",
        "destination": str(ASSEMBLY_MARKERS_RAW_PATH.relative_to(BASE_DIR)),
        **assembly_markers_preserved_metadata,
    }
    artifacts = [
        (
            CAMBRIDGE_DETAILS_RAW_PATH,
            cambridge_details_content,
            cambridge_details_raw_metadata,
        ),
        (SOMERVILLE_RAW_PATH, somerville_content, somerville_raw_metadata),
        (
            SOMERVILLE_PAGE_RAW_PATH,
            somerville_page_content,
            somerville_page_raw_metadata,
        ),
        (
            ASSEMBLY_PAGE_RAW_PATH,
            assembly_page_snapshot,
            assembly_page_raw_metadata,
        ),
        (
            ASSEMBLY_MARKERS_RAW_PATH,
            assembly_markers_snapshot,
            assembly_markers_raw_metadata,
        ),
    ]
    for key, config in ASSEMBLY_FACILITIES.items():
        detail_path = ASSEMBLY_RAW_DIR / f"{key}.txt"
        detail_metadata = {
            "id": f"assembly_row_parking_{key.replace('-', '_')}",
            "title": config["name"],
            "municipality": "somerville",
            "category": "public_parking_facilities",
            "destination": str(detail_path.relative_to(BASE_DIR)),
            **assembly_details_preserved_metadata[key],
        }
        artifacts.append(
            (detail_path, assembly_detail_snapshots[key], detail_metadata)
        )
    return output, artifacts


def main() -> None:
    output, artifacts = build()
    for path, content, metadata in artifacts:
        atomic_write(path, content)
        atomic_write(
            path.with_suffix(path.suffix + ".metadata.json"),
            (json.dumps(metadata, indent=2) + "\n").encode(),
        )
    atomic_write(OUTPUT_PATH, (json.dumps(output, indent=2) + "\n").encode())
    print(f"Wrote {OUTPUT_PATH}")
    for path, _, _ in artifacts:
        print(f"Wrote {path}")
    print(f"Public parking facilities: {output['properties']['feature_count']}")
    print(
        "Municipalities: "
        f"{output['properties']['municipality_counts']}"
    )


if __name__ == "__main__":
    main()
