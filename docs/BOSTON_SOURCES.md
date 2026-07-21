# Boston Sources

Boston is a planned coverage area. The official sources identified here are registered in `data/source_manifest.json`, but none should be presented as a complete curb-rule inventory yet.

## Recommended Initial Stack

- IPS parking meter ArcGIS layer: official point evidence for meter locations and identities.
- Street Cleaning in Boston: official schedule lookup/download, with posted signs controlling when the website differs.
- City of Boston Traffic Rules and Regulations: current legal baseline and revision index.
- Resident Parking Permits: official program and neighborhood guidance.
- Boston Curb Lab: monitor for the announced public curb-data-standard API.

## Ingestion Rules

- Preserve meter points as point evidence; do not mark an entire street metered from one point.
- Convert cleaning schedules, permit zones, and legal restrictions to side-specific from/to spans where the source supports them.
- Store effective dates and supersession for temporary or revised rules.
- Keep unmatched locations `Unknown`.
- Do not make the Curb Lab API a production dependency until a public endpoint and stable schema are verifiably released.
