# Curb Segment Strategy

Street-level labels are too coarse for parking. A single meter, loading zone, school-zone sign, or resident-permit segment should not color a whole street.

The target unit of truth is a curb segment: a side-specific span along a street with a start, end, rule set, source, and confidence.

## Target Model

Each curb rule should eventually describe:

- Municipality
- Street name
- Side of street
- Start anchor, such as an address, cross street, distance, sign, or coordinate
- End anchor
- Geometry or linear reference along a street centerline
- Rule category
- Rule text
- Active days and times
- Permit zone or meter metadata
- Source file or URL
- Extraction method
- Confidence

## Source Hierarchy

Use the most precise source first:

1. Curb/sign/meter GIS points or curb-line geometry.
2. Official regulation rows with from/to streets, address ranges, sides, or distances.
3. Official maps that can be georeferenced or digitized.
4. OpenStreetMap features for signs, meters, parking lanes, and street furniture.
5. Street-level imagery and sign recognition, after official sources are exhausted.

## Matching Stages

1. Preserve row-level source records without collapsing them to streets.
2. Parse anchors from text: `from`, `to`, `between`, address ranges, side, distance, and exceptions.
3. Match anchors to street centerline segments.
4. Project matched spans to left/right curb sides.
5. Split overlapping spans by priority and active time.
6. Render confidence and source lineage in the UI.

## Image And Field Data

The DGX node can become useful for sign and meter extraction, but it should not be the first source of truth.

Good uses:

- OCR parking signs from street-level imagery.
- Detect meters, kiosks, loading signs, accessible signs, and curb paint.
- Compare detected sign text against official regulation rows.
- Flag contradictions or stale municipal data for review.

Risks:

- Imagery can be stale.
- Sign text can be occluded or unreadable.
- Parking rules may depend on ordinance defaults not visible on a sign.
- A model can detect a sign but still fail to assign the correct curb span.

## Immediate Rule

Any source row with an address range, side-of-street phrase, from/to block, distance, exception, or business/residential frontage split must remain segment-specific until a geometry matcher resolves it.
