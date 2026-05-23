# Roadmap

This roadmap keeps the repo aligned with the larger goal: accurate interactive parking rules across Boston-area municipalities.

## Phase 1: Somerville Baseline

- Keep the existing Somerville map working.
- Improve parser tests around Schedule D and Schedule F extraction.
- Show source and confidence fields in API responses and UI details.
- Separate generated data from raw source data.
- Document known false positives from street-name matching.
- Keep Medford public sources reproducible through `data/source_manifest.json`.
- Preserve Medford resident-permit rows as segment candidates instead of whole-street labels.
- Establish curb segments as the target data model before adding more street-level classifications.
- Serve Medford streets in the app while clearly flagging segment-specific permit rows.
- Serve Cambridge streets and GIS meter/accessibility evidence while clearly flagging nearest-street meter matches as segment evidence.

## Phase 2: Segment-Level Somerville

- Parse regulation rows into block ranges where possible.
- Match from/to streets against the street network.
- Store rule geometry or segment references.
- Render separate layers for meters, time limits, resident permit defaults, and no-parking rules.

## Phase 3: Multi-City Data Foundation

- Add municipality-aware data folders and APIs.
- Add a shared rule schema for Boston, Cambridge, Somerville, and Medford.
- Add source metadata for each municipality.
- Update map bounds, search, and filters to work across municipalities.
- Keep municipality-specific builders for sources whose formats differ, such as Medford PDFs and Cambridge GIS layers.

## Phase 4: Boston-Area Coverage

- Import Boston curb, meter, resident-parking, and street-regulation sources.
- Expand Cambridge parking and curb-regulation sources beyond the initial GIS meter/accessibility layers.
- Import Medford parking and street-regulation sources.
- Add confidence badges and source links in the UI.

## Phase 5: Time-Aware Parking Answers

- Add a selected date and time control.
- Evaluate active restrictions for that time.
- Show the controlling rule and inactive upcoming restrictions.
- Add tests for recurring schedules, holidays, overnight rules, and ambiguous source text.

## Open Questions

- Which official source should be treated as authoritative when GIS layers and traffic regulations conflict?
- How should the app handle unsigned streets where permit defaults apply by ordinance?
- What minimum confidence should be required before a block is colored as parkable?
- Should user corrections be accepted, and if so, how should they be reviewed and sourced?
