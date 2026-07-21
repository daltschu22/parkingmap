# Data Pipeline

This document defines the intended shape of parking data ingestion as the project expands beyond Somerville.

## Source Priority

Use sources in this order when available:

1. Official city open-data GIS layers with curb, sign, meter, or regulation geometry.
2. Official traffic regulation tables, PDFs, ordinances, or municipal code.
3. Official parking-meter inventories or permit-zone maps.
4. Manually curated local data with source notes.

## Coverage Areas

Initial and target municipalities:

- Somerville
- Boston
- Cambridge
- Medford

Additional nearby municipalities can be added when their data sources and licensing are understood.

## Desired Raw Data Layout

As the repo grows, prefer municipality-scoped source folders:

```text
data/
  raw/
    somerville/
    boston/
    cambridge/
    medford/
  processed/
    parking_rules/
    street_network/
```

The existing files under `data/` are part of the Somerville prototype and can be migrated into this layout when multi-city ingestion starts.

Public source files are tracked in `data/source_manifest.json`. Fetch them with:

```bash
python3 scripts/fetch_public_sources.py --municipality medford
```

Refresh Somerville's canonical legal source before rebuilding its rules:

```bash
python3 scripts/fetch_public_sources.py --municipality somerville
python3 build_parking_rules.py
```

The generic fetcher validates declared PDF/JSON content before atomically replacing a local artifact. Its sidecar metadata records the requested and resolved URLs, retrieval time, Last-Modified, ETag, byte count, and SHA-256. Builders should copy this provenance into derived outputs and fail closed when the source identity, hash, or expected structure does not match.

Cambridge GIS sources are currently downloaded directly by:

```bash
python3 build_cambridge_data.py
```

That builder emits normalized street geometry, meter-space geometry, accessible-space geometry, and street-level summaries under `data/processed/cambridge/`.

## Desired Rule Model

Derived parking records should eventually support:

- Municipality
- Street name
- Block or segment geometry
- Side of street, when known
- Rule category
- Rule text
- Days and hours
- Date exceptions
- Permit zone, meter zone, or rate metadata
- Source URL or source file
- Source publication date or retrieval date
- Extraction confidence

## Matching Strategy

Use the strongest match available:

1. Geometry-to-geometry matching when source rules include curb or sign geometry.
2. Block-segment matching when rules include from/to streets.
3. Street-side matching when rules include side of street.
4. Street-name matching only as a fallback.

Any UI or API response should expose the match confidence so users know whether a rule is block-specific or approximate.

Never collapse a partial source row into a whole-street rule. If a row mentions an address range, street side, from/to block, distance, intersection, business frontage, private/public split, exception, or similar segment clue, the derived record must stay `partial_or_segment_specific` until geometry matching exists.

Likewise, do not collapse point or polygon evidence into a whole-street status. Cambridge meter polygons are rendered as their own evidence layer and also summarized as `metered_segments_known` street evidence, with `nearest_street_approx` confidence, until curb-side matching exists.

## Rebuild Expectations

Generated files should be reproducible. Scripts should:

- Read raw source files or documented source URLs.
- Normalize street names through shared utilities.
- Emit structured JSON or GeoJSON with source metadata.
- Preserve source hashes, retrieval times, resolved URLs, and parser versions.
- Fail rather than silently reuse text extracted from a different source edition.
- Avoid hand-editing generated outputs except for temporary debugging.
