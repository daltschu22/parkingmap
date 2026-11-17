"""Locate a published Somerville curb range on a single unambiguous street segment."""

import math
import re

# Local east/north meters per degree at Somerville. Only used over single blocks.
EAST = 111_320 * math.cos(math.radians(42.39))
NORTH = 111_132
DIRECTIONS = {"NORTH": (0, 1), "SOUTH": (0, -1), "EAST": (1, 0), "WEST": (-1, 0)}
OFFSET = re.compile(r"(\d+) (?:FT|FEET) (NORTH|SOUTH|EAST|WEST)(?: OF (.+))?")


def _distance(a, b):
    return math.hypot((b[0] - a[0]) * EAST, (b[1] - a[1]) * NORTH)


def _slice(line, start, end):
    points = []
    walked = 0
    for a, b in zip(line, line[1:]):
        length = _distance(a, b)
        if length and walked < end and walked + length > start:
            for pos in (max(start, walked), min(end, walked + length)):
                t = (pos - walked) / length
                p = (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
                if not points or points[-1] != p:
                    points.append(p)
        walked += length
    return points if walked >= end and len(points) >= 2 else []


def _side_trace(line, side):
    # Offset the drawn line toward the named side. Street width is not surveyed.
    sx, sy = DIRECTIONS[side]
    points = []
    for i, (lon, lat) in enumerate(line):
        a, b = line[max(0, i - 1)], line[min(len(line) - 1, i + 1)]
        length = _distance(a, b)
        if not length:
            return []
        nx, ny = -(b[1] - a[1]) * NORTH / length, (b[0] - a[0]) * EAST / length
        alignment = nx * sx + ny * sy
        if abs(alignment) < 0.4:
            return []
        width = math.copysign(4, alignment)
        points.append([round(lat + width * ny / NORTH, 7), round(lon + width * nx / EAST, 7)])
    return points


def match_section(row, crossing, outgoing, crossings, normalize):
    side = row["side"].strip().upper()
    if side not in {*DIRECTIONS, "BOTH"}:
        return []
    start, end = (re.sub(r"\bAV\b", "AVE", normalize(row[key])) for key in ("from", "to"))
    if not end:
        return []
    # Accept two named intersections only if they delimit a single GIS line.
    if start == crossing and len(crossings.get(end, ())) == 1:
        target = next(iter(crossings[end]))
        paths = [line for line in outgoing if tuple(round(v, 6) for v in line[-1][:2]) == target]
    else:
        bounds = []
        direction = None
        for text in (start, end):
            if text == crossing:
                bounds.append(0)
                continue
            match = OFFSET.fullmatch(text)
            if not match or (match[3] and match[3] != crossing) or (direction and direction != match[2]):
                return []
            direction = match[2]
            bounds.append(int(match[1]) * 0.3048)
        if not direction or bounds[0] == bounds[1]:
            return []
        dx, dy = DIRECTIONS[direction]
        paths = []
        for line in outgoing:
            a = line[0]
            b = next((p for p in line[1:] if _distance(a, p) >= 1), None)
            if b:
                projection = (b[0] - a[0]) * EAST * dx + (b[1] - a[1]) * NORTH * dy
                if projection > 0.5 * _distance(a, b):
                    paths.append(line)
        if len(paths) != 1:
            return []
        clipped = _slice(paths[0], *sorted(bounds))
        if not clipped:
            return []
        paths = [clipped]
    if len(paths) != 1:
        return []
    line = paths[0]
    if side == "BOTH":
        # Pick opposite normal directions perpendicular to this block.
        horizontal = abs(line[-1][0] - line[0][0]) * EAST > abs(line[-1][1] - line[0][1]) * NORTH
        sides = ("NORTH", "SOUTH") if horizontal else ("EAST", "WEST")
    else:
        sides = (side,)
    traces = [_side_trace(line, s) for s in sides]
    return traces if all(traces) else []
