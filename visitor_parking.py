"""Compact visitor parking choices backed by a space, facility, or signed rule."""

import re
from collections import defaultdict


def public_time_limit(restriction: str) -> bool:
    text = restriction.casefold()
    if re.search(
        r"no parking|no stopping|tow zone|rescind|remov|proposed|seasonal|pick.?up|drop.?off|loading|charging|police|school|handicap|accessible",
        text,
    ):
        return False
    return bool(
        re.search(r"(?:\d+|½)\s*[- ]?\s*(?:hours?|hrs?|h|minutes?|mins?)\b", text)
    )


def somerville_options(streets, rules, normalize, source):
    # Only use a reference intersection shared by the named streets in the GIS.
    # An offset in a regulation is not itself an exact mapped curb or routing point.
    nodes = defaultdict(set)
    for feature in streets:
        name = normalize(feature["properties"].get("STNAME"))
        geom = feature["geometry"]
        lines = (
            geom["coordinates"]
            if geom["type"] == "MultiLineString"
            else [geom["coordinates"]]
        )
        for line in lines:
            for x, y, *_ in (line[0], line[-1]):
                nodes[name].add((round(x, 6), round(y, 6)))

    locations = defaultdict(set)
    for street, endpoints in nodes.items():
        for endpoint in endpoints:
            locations[endpoint].add(street)
    for street, record in rules.items():
        crossings = defaultdict(set)
        for endpoint in nodes.get(street, {}):
            for other in locations[endpoint] - {street}:
                crossings[other].add(endpoint)
        for schedule, kind in (("f", "meter"), ("d", "limited")):
            for row in record.get(f"schedule_{schedule}_rows", []):
                restriction = row["restriction"]
                if kind == "meter" and row.get("action") not in {
                    "installation",
                    "meter_listed",
                }:
                    continue
                if kind == "limited" and not public_time_limit(restriction):
                    continue
                if not row["side"] or re.search(
                    r"loading|charg(?:ing|er)|commercial|sporting event|school|disabled|handicap|taxi|bus stop|pick.?up|drop.?off",
                    restriction,
                    re.I,
                ):
                    continue
                if re.search(r"(?:permit parking|rpp) only\s*$", restriction, re.I):
                    continue
                anchor = None
                for text in (row["from"], row["to"]):
                    words = normalize(text).split()
                    # Strip only positional prefixes so a longer cross street cannot
                    # be mistaken for a shorter one with the same suffix.
                    candidate = re.sub(
                        r"^(?:AT |(?:APPROX(?:IMATELY)? )?\d+ (?:FT|FEET) (?:NORTH|SOUTH|EAST|WEST) OF )",
                        "",
                        " ".join(words),
                    )
                    candidate = re.sub(r" AV$", " AVE", candidate)
                    if len(crossings.get(candidate, ())) == 1:
                        anchor = candidate
                        break
                if not anchor:
                    continue
                x, y = next(iter(crossings[anchor]))
                limits = " → ".join(t for t in (row["from"], row["to"]) if t)
                yield dict(
                    kind=kind,
                    name=street.title(),
                    city="Somerville",
                    point=[y, x],
                    where=f"{row['side']} side · {limits}",
                    rule=restriction,
                    anchor=anchor.title(),
                    source=f"{source}#page={row['page']}",
                    citation=f"Schedule {schedule.upper()} · p. {row['page']}",
                )


def cambridge_options(features, source):
    # Choose one actual in-service space per block and tariff for the overview.
    # The full polygons for all individual spaces remain available at street zoom.
    seen = set()
    for f in features:
        p = f["properties"]
        if str(p.get("STATUS", "")).casefold() != "in service":
            continue
        key = (
            p.get("MATCHED_SEGMENT_ID") or p["SPACE_ID"],
            p.get("RATE"),
            p.get("MAX_TIME"),
            p.get("OPERATION_HOURS"),
        )
        if key in seen:
            continue
        seen.add(key)
        geom = f["geometry"]
        ring = (
            geom["coordinates"][0]
            if geom["type"] == "MultiPolygon"
            else geom["coordinates"]
        )[0]
        pts = ring[:-1] if ring[0] == ring[-1] else ring
        lon, lat = (sum(pt[i] for pt in pts) / len(pts) for i in (0, 1))
        yield dict(
            kind="meter",
            name=p.get("MATCHED_STREET") or "Meter space",
            city="Cambridge",
            point=[round(lat, 6), round(lon, 6)],
            where=f"Space {p['SPACE_ID']}",
            rule=" · ".join(
                str(v) for v in (p.get("MAX_TIME"), p.get("OPERATION_HOURS")) if v
            ),
            rate=p.get("RATE"),
            source=source,
            citation="Cambridge meter inventory",
        )


def facility_options(features):
    for f in features:
        p = f["properties"]
        regs = p.get("PARKING_REGULATIONS") or ""
        special = p.get("SPECIAL_RESTRICTIONS")
        if special in ("No", "None", "N/A"):
            special = None
        # These facilities have a published visitor tariff, including a daily reset.
        free = regs.startswith("0-3 hours free;")
        x, y = f["geometry"]["coordinates"][:2]
        yield dict(
            kind="free" if free else "lot",
            name=p["NAME"],
            city=p["MUNICIPALITY"],
            point=[y, x],
            where=p.get("ADDRESS") or p["FACILITY_TYPE"],
            rule=regs or p["PUBLIC_ACCESS"],
            facility=p["FACILITY_ID"],
            restrictions=special,
            access=p["PUBLIC_ACCESS"],
            source=p.get("DETAILS_URL") or p.get("SOURCE_URL"),
            citation="Facility details",
        )
