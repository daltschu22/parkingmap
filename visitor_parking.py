"""Compact visitor parking choices backed by a space, facility, or signed rule."""

import calendar
import re
from collections import defaultdict

from curb_sections import match_section
from destinations import exact_address


# Only convert explicitly published time spans to daily restrictions.
CLOCK = r"(1[0-2]|0?[1-9])(?::([0-5]\d))?\s*([ap])m?"
SPAN = re.compile(rf"(?<![\d:]){CLOCK}\s*(?:-|to)\s*{CLOCK}\b", re.I)
PERMIT_ONLY = re.compile(r"\b(?:rpp(?: only)?|permit(?: parking)? only)(?: from)?\s*$", re.I)


def time_window(match):
    def minutes(hour, minute, half):
        return (int(hour) % 12 + (12 if half.lower() == "p" else 0)) * 60 + int(minute or 0)
    return [minutes(*match.group(1, 2, 3)), minutes(*match.group(4, 5, 6))]


def daily_hours(rule):
    result = {}
    for match in SPAN.finditer(rule):
        if PERMIT_ONLY.search(rule[:match.start()]):
            result["permit_window"] = time_window(match)
        elif "visitor_window" not in result:
            result["visitor_window"] = time_window(match)
    if re.search(r"\bmon(?:day)?\s*[-–]\s*fri(?:day)?\b", rule, re.I) and "visitor_window" in result:
        result["visitor_days"] = [1, 2, 3, 4, 5]
        return result
    # Preserve other day-qualified schedules in the original text.
    if re.search(r"\b(?:mon|tue|wed|thu|fri|sat|sun)", rule, re.I):
        return {}
    return result


def meter_schedule(hours):
    """Only return a payment schedule when every published clause is understood."""
    schedule = []
    for clause in (hours or "").replace("–", "-").split(";"):
        clause = clause.strip()
        span = SPAN.match(clause)
        if not span:
            return None
        days = re.sub(r"[ ()-]", "", clause[span.end():]).lower()
        masks = {"": [1, 2, 3, 4, 5, 6], "monfri": [1, 2, 3, 4, 5],
                 "monsat": [1, 2, 3, 4, 5, 6], "sat": [6]}
        if days not in masks:
            return None
        schedule.append({"days": masks[days], "window": time_window(span)})
    return schedule


def closure_hours(restrictions):
    # The city's lot inventory publishes closures as weekday/time and optional seasons.
    if not restrictions or not restrictions.startswith("No Parking-"):
        return {}
    clause = restrictions.split(";")[0]
    weekdays = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")
    day = next((i for i, name in enumerate(weekdays) if name + "s" in clause), None)
    span = SPAN.search(clause)
    if day is None or not span:
        return {}
    months = [i for i, name in enumerate(calendar.month_name) if name and name in clause]
    if months and len(months) != 2:
        return {}
    return {"closure": {"day": day, "months": months, "weeks": [1, 3] if "1st & 3rd" in clause else [],
                        "window": time_window(span)}}


SUNDAY_EXCEPTION = re.compile(
    r"\b(?:except(?:ing)?|exclud(?:ing|es))\s+sundays?\b|"
    r"\bsundays?(?:\s*(?:&|and)\s*holidays?)?\s+excluded\b", re.I
)


def medford_sunday_rule(rows):
    """Only a street-wide, explicit exception can be applied to every block."""
    if rows and all(r.get("scope") == "street_level_time_window" and
                    SUNDAY_EXCEPTION.search(r.get("restriction_text", "")) for r in rows):
        return rows[0]
    return None


def medford_sunday_options(streets, rules, normalize, source):
    # Publish one reference per named street. It identifies a permit exception only.
    for street, rows in rules.items():
        for row in rows:
            match = re.fullmatch(r"24/7\. Sundays excluded in front of ([0-9, and]+) (.+) only\.",
                                 row.get("restriction_text", ""), re.I)
            if not match or normalize(match[2]) != street:
                continue
            for number in re.findall(r"\d+", match[1]):
                address = f"{number} {row['street_name']}"
                point = exact_address(address, "Medford")
                if point:
                    yield dict(kind="sunday", name=row["street_name"], city="Medford", point=point,
                               anchor=address, anchor_type="address", where=f"In front of {address}",
                               rule=row["restriction_text"], source=f"{source}#page={row['page']}",
                               citation=f"Medford permit list · p. {row['page']}")
    seen = set()
    for feature in streets:
        props = feature["properties"]
        name = props.get("STNAME") or ""
        key = normalize(name)
        if key in seen or props.get("OWNERSHIP") != "Public":
            continue
        row = medford_sunday_rule(rules.get(key))
        if not row:
            continue
        line = feature["geometry"]["coordinates"]
        a, b = line[(len(line) - 1) // 2], line[len(line) // 2]
        point = [round((a[i] + b[i]) / 2, 6) for i in (1, 0)]
        seen.add(key)
        yield dict(kind="sunday", name=name.title(), city="Medford", point=point,
                   anchor=name.title(), anchor_type="street", where="Street reference · check the signed curb",
                   rule=row["restriction_text"], source=f"{source}#page={row['page']}",
                   citation=f"Medford permit list · p. {row['page']}")


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
    outgoing = defaultdict(list)
    ownership = defaultdict(set)
    for feature in streets:
        name = normalize(feature["properties"].get("STNAME"))
        ownership[name].add(feature["properties"].get("OWNERSHIP"))
        geom = feature["geometry"]
        lines = (
            geom["coordinates"]
            if geom["type"] == "MultiLineString"
            else [geom["coordinates"]]
        )
        for line in lines:
            for x, y, *_ in (line[0], line[-1]):
                nodes[name].add((round(x, 6), round(y, 6)))
            for oriented in (line, list(reversed(line))):
                node = tuple(round(v, 6) for v in oriented[0][:2])
                outgoing[name, node].append(oriented)

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
                if re.search(
                    r"loading|charg(?:ing|er)|commercial|sporting event|school|disabled|handicap|taxi|bus stop|pick.?up|drop.?off",
                    restriction,
                    re.I,
                ):
                    continue
                if re.search(
                    r"(?:permit parking|rpp) only\s*$|temporary|for \d+ days",
                    restriction,
                    re.I,
                ):
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
                limits = " → ".join(t for t in (row["from"], row["to"]) if t)
                anchor_type = "intersection"
                traces = []
                if anchor and row["side"]:
                    x, y = next(iter(crossings[anchor]))
                    where = f"{row['side']} side · {limits}"
                    traces = match_section(row, anchor, outgoing[street, (x, y)], crossings, normalize)
                elif (
                    number := re.fullmatch(r"#?(\d+[A-Za-z]?)", row["from"].strip())
                ) and not row["to"]:
                    if row.get("action") == "installation":
                        continue
                    anchor = f"{number[1]} {street.title()}"
                    point = exact_address(anchor, "Somerville")
                    if point is None:
                        continue
                    y, x = point
                    anchor_type = "address"
                    where = (
                        f"{row['side']} side · near {anchor}"
                        if row["side"]
                        else f"Near {anchor}"
                    )
                else:
                    continue
                yield dict(
                    kind=kind,
                    name=street.title(),
                    city="Somerville",
                    point=[y, x],
                    where=where,
                    rule=restriction,
                    anchor=anchor.title(),
                    anchor_type=anchor_type,
                    sunday_permit_exception=ownership[street] == {"Public"},
                    **daily_hours(restriction),
                    **({"section_traces": traces} if traces else {}),
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
            meter_schedule=meter_schedule(p.get("OPERATION_HOURS")),
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
            **(daily_hours(regs) if p["MUNICIPALITY"] == "Somerville" else {}),
            **closure_hours(special),
            access=p["PUBLIC_ACCESS"],
            source=p.get("DETAILS_URL") or p.get("SOURCE_URL"),
            citation="Facility details",
        )
