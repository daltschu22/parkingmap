# Street-Level Imagery Sources

Imagery is evidence for verification and gap-filling. It should not overwrite official parking rules without confidence, source, capture date, and review notes.

## Mapillary

Mapillary is the primary starting point because it has street-level imagery, API access, OSM workflows, and traffic-sign/object detection support.

Get a client token from:

```text
https://www.mapillary.com/dashboard/developers
```

Run a small metadata pull:

```bash
export MAPILLARY_ACCESS_TOKEN="..."
python3 scripts/build_imagery_index.py --provider mapillary --municipality somerville --limit 25 --radius-meters 35 --photos-per-sample 2
```

Output:

```text
data/processed/imagery/street_imagery_index.geojson
```

The index stores image IDs, thumbnail/preview URLs, capture timestamps, heading, source URL, and the sampled street segment. It does not download images.

## KartaView

KartaView, formerly OpenStreetCam/OpenStreetView, is available as a no-token fallback:

```bash
python3 scripts/build_imagery_index.py --provider kartaview --municipality somerville --limit 25 --radius-meters 35 --photos-per-sample 2
```

Anonymous KartaView usage has modest rate limits, so keep initial runs small.

## Next Pipeline Stage

After metadata coverage looks useful:

- add an image downloader that stores source/license/capture metadata next to each image
- run sign/meter/curb OCR and detection on GPU
- project detections back to curb or street segments
- emit `data/processed/imagery/parking_sign_evidence.geojson`

See [SPARK_IMAGE_WORKLOAD.md](SPARK_IMAGE_WORKLOAD.md) for the DGX Spark batch plan.
