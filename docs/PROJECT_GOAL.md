# Project Goal

`parkingmap` should become an interactive Greater Boston curb-parking map.

The core user question is:

> Can I park on this street or block, right now or at a chosen time, and what rule makes that true?

The initial implemented coverage is Somerville, MA. The intended coverage area includes Boston, Cambridge, Somerville, Medford, and other nearby municipalities as data becomes available.

## Product Scope

The map should show parking availability and restrictions at street or block level, including:

- Resident-permit parking
- Metered parking
- Time-limited parking
- No-parking and no-standing rules
- Street-cleaning restrictions
- Loading zones
- Accessible parking spaces
- Private-street or private-lot rules
- Other posted curb rules when available from municipal sources

## Accuracy Principles

- Prefer official municipal sources over third-party summaries.
- Preserve source attribution, source date, and extraction method for each rule.
- Represent uncertainty explicitly instead of hiding it.
- Do not imply block-level accuracy when only street-level matching is available.
- Keep derived datasets reproducible from scripts and source files checked into or documented by the repo.

## Current Prototype

The current app uses Somerville street centerlines and a parser for Somerville traffic regulations. It enriches street GeoJSON features with parking categories derived from the PDF.

Current limitation:

- Parking-rule matching is by normalized street name. This can overstate a restriction when only part of a street is affected.

## Target User Experience

Users should be able to:

- Search for a street or address.
- Inspect a block and see whether public parking is likely allowed.
- See the controlling restriction, such as resident permit, meter, time limit, or no parking.
- Filter the map by parking rule type.
- Understand confidence and data freshness before relying on the result.

## Non-Goals For Now

- Real-time occupancy prediction.
- Payment processing for meters.
- Navigation or routing.
- Enforcement guarantee. Posted signs and current law remain the final authority.
