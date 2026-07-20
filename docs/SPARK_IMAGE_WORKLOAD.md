# Spark Image Workload Plan

This project uses `spark` as the GPU batch host for street-level image analysis. This is not Apache Spark; it is the DGX Spark host configured in `home-ansible`.

## Goal

Turn street-level imagery into structured parking evidence:

- parking regulation signs
- meters and pay stations
- curb paint and lane markings
- loading, accessible, bus, fire-lane, hydrant, and no-parking cues

Image output is evidence. It does not overwrite official municipal rules by itself.

## Current Test Dataset

Salem Street in Medford is the first full-street test set.

```text
data/processed/imagery/medford_salem_street_mapillary_index.geojson
data/processed/imagery/assets/medford_salem_street_mapillary_index/
data/processed/imagery/assets/medford_salem_street_mapillary_index/manifest.json
```

Current size:

```text
328 Mapillary metadata rows
289 unique downloaded preview images
26 MB
```

First YOLO-World Spark run:

```text
Job: medford_salem_street_20260523T171618Z
Images: 289
Detections: 203
Errors: 0
Runtime: 9.736 seconds after dependency/model setup
Output: data/processed/imagery/spark_runs/medford_salem_street_20260523T171618Z/
Canonical GeoJSON: data/processed/imagery/parking_image_evidence.geojson
```

Detection counts:

```text
107 resident permit parking sign
40 bus stop sign
25 parking sign
20 fire hydrant
10 street cleaning sign
1 parking meter
```

This first pass is intentionally noisy. At least one high-confidence sign crop was a route sign mislabeled as a resident permit sign. Treat these detections as candidates for OCR/review, not validated parking facts.

## Parking-Sign Reference Layer

Before OCRing arbitrary sign crops, pull Mapillary traffic-sign map features for the target area. These are sign/map-object features already extracted from Mapillary imagery and linked back to contributing image IDs.

For Medford Center / Salem Street:

```bash
MAPILLARY_ACCESS_TOKEN="..." python3 scripts/build_mapillary_sign_index.py \
  --bbox -71.1100 42.4175 -71.0965 42.4218 \
  --output data/processed/imagery/medford_center_parking_sign_features.geojson

cp data/processed/imagery/medford_center_parking_sign_features.geojson \
  data/processed/imagery/mapillary_parking_sign_features.geojson
```

Current Medford Center pull:

```text
26 parking-related Mapillary features
10 regulatory--no-parking--g2
7 regulatory--no-parking-or-no-stopping--g1
4 information--parking--g1
2 object--parking-meter
1 regulatory--parking-restrictions--g1
1 regulatory--no-parking-or-no-stopping--g3
1 object--traffic-sign--information-parking
```

Then join the reference layer to Spark detections by Mapillary linked image ID:

```bash
python3 scripts/build_reference_matched_detections.py \
  --run-dir data/processed/imagery/spark_runs/medford_salem_street_20260523T171618Z \
  --reference-signs data/processed/imagery/medford_center_parking_sign_features.geojson \
  --min-confidence 0.08

cp data/processed/imagery/spark_runs/medford_salem_street_20260523T171618Z/reference_matches/reference_matched_detections.geojson \
  data/processed/imagery/reference_matched_detections.geojson
```

Current reference-matched output:

```text
12 sign-like Spark detections matched to parking-specific Mapillary sign features
10 matched regulatory--no-parking--g2
2 matched regulatory--no-parking-or-no-stopping--g1
```

Use this smaller matched set as the first OCR queue. The larger raw detection set remains useful for finding signs Mapillary did not classify.

## Workload Shape

### Stage 1: Build Local Dataset

Run from the repo on the workstation:

```bash
MAPILLARY_ACCESS_TOKEN="..." python3 scripts/build_imagery_index.py \
  --provider mapillary \
  --municipality medford \
  --street "Salem Street" \
  --exclude-highways \
  --radius-meters 70 \
  --photos-per-sample 20 \
  --output data/processed/imagery/medford_salem_street_mapillary_index.geojson

python3 scripts/download_imagery_assets.py \
  --index data/processed/imagery/medford_salem_street_mapillary_index.geojson \
  --limit 1000
```

### Stage 2: Copy Dataset To Spark

Use one job directory per run.

```bash
JOB=medford_salem_street_$(date -u +%Y%m%dT%H%M%SZ)
ssh altschud@spark "mkdir -p ~/parkingmap-imagery/jobs/$JOB"

rsync -a \
  data/processed/imagery/medford_salem_street_mapillary_index.geojson \
  data/processed/imagery/assets/medford_salem_street_mapillary_index/ \
  altschud@spark:~/parkingmap-imagery/jobs/$JOB/
```

### Stage 3: Run GPU Inference

Start with off-the-shelf models. Do not train a custom model until we know the failure modes.

Recommended first pass:

- detector: open-vocabulary or traffic-sign-capable detector
- OCR: sign crop OCR
- classifier/parser: normalize OCR text into parking rule candidates

Detection classes to request:

```text
parking sign
no parking sign
resident permit parking sign
street cleaning sign
loading zone sign
bus stop sign
accessible parking sign
meter
parking meter
pay station
fire hydrant
curb paint
yellow curb
red curb
white curb
bike lane
crosswalk
driveway
```

The Spark job should emit raw detections first, before any parking-rule interpretation:

```text
outputs/detections.jsonl
outputs/sign_crops/
outputs/ocr.jsonl
outputs/parking_image_evidence.geojson
outputs/run_manifest.json
```

### Stage 4: Normalize Evidence

Each evidence row should include:

```json
{
  "provider": "mapillary",
  "image_id": "183873246925245",
  "image_path": "mapillary_183873246925245.jpg",
  "captured_at": 1506972780100,
  "street_name": "SALEM STREET",
  "detected_object": "parking_sign",
  "bbox_xyxy": [0, 0, 0, 0],
  "object_confidence": 0.0,
  "ocr_text": "",
  "ocr_confidence": 0.0,
  "parsed_rule": {
    "category": "unknown",
    "days": null,
    "hours": null,
    "permit_zone": null
  },
  "geometry": {
    "type": "Point",
    "coordinates": [-71.0, 42.0]
  },
  "projection_confidence": "image_location_only"
}
```

## Projection Back To Parking Map

First pass:

- Assign each detection to the image GPS point.
- Attach image heading and the sampled street segment from the manifest.
- Mark projection confidence as `image_location_only`.

Second pass:

- Use detection horizontal position in the image plus camera heading to estimate left/right curb side.
- Match the detection to the nearest street segment within 30-50 meters.
- Mark projection confidence as `nearest_segment_heading_approx`.

Do not mark a whole street as no-parking, metered, or permit-only from a single image. The evidence should create reviewable curb/sign points.

## How Spark Should Run It

Preferred runtime:

- containerized Python with CUDA/PyTorch
- mounted job directory
- no repo mutation on Spark except writing `outputs/`

Example skeleton:

```bash
ssh altschud@spark
cd ~/parkingmap-imagery/jobs/$JOB

docker run --rm --gpus all --ipc=host \
  -v "$PWD:/work" \
  -w /work \
  parkingmap-vision:latest \
  python /work/run_image_inference.py \
    --manifest manifest.json \
    --images . \
    --output outputs
```

If a local container image does not exist yet, build it on Spark from a small `Dockerfile.vision` that installs:

- PyTorch CUDA stack
- image model dependencies
- OCR dependency
- geospatial JSON utilities

## Model Plan

### Pass A: Detection Only

Goal: find whether the dataset has useful signs/meters.

Outputs:

- object class
- bounding box
- confidence
- image id
- image location and heading

### Pass B: Sign Crop OCR

Run OCR only on sign-like crops. Prioritize crops matched to Mapillary parking-sign reference features, then fall back to unmatched sign-like crops when coverage is thin. This keeps OCR cheap and reduces false text from storefronts and route signs.

Outputs:

- raw OCR text
- OCR confidence
- crop path
- linked detection id

### Pass C: Parking Rule Parsing

After detection and OCR, run the review-gated interpretation stage:

```bash
python3 scripts/interpret_parking_rules.py \
  --run-dir data/processed/imagery/spark_runs/<job-id>
```

This writes:

```text
dataset/parking_rule_candidates.csv
dataset/parking_rule_candidates.jsonl
dataset/parking_rule_candidates.geojson
dataset/parking_rule_candidates.html
dataset/parking_rule_candidates_summary.json
```

Each rule candidate includes:

- `parking_allowed`: `False`, `conditional`, or `unknown`
- `rule_type`: such as `no_parking`, `no_parking_or_no_stopping`, or `parking_information`
- `days` and `time_windows`, empty when OCR did not reliably expose them
- `permit_required`, usually `unknown` unless OCR/source evidence supports it
- `tow_zone`, `street_sweeping`, and `no_stopping` flags
- `confidence`
- image/source/reference links
- `review_required: true`

Do not promote these candidates directly to legal curb rules. They are a review queue and a structured bridge between image evidence and source-backed parking rules.

Normalize OCR text into tentative rule fields:

- no parking
- resident permit
- meter/time limit
- loading/commercial
- accessible
- bus/fire/hydrant restriction
- active days/times
- permit zone

This can be a deterministic parser first, then a local LLM pass for messy sign text.

## Acceptance Criteria For First Spark Run

The first Spark run is successful if it produces:

- detections for at least a few visible signs/meters on Salem Street
- crop images for detected signs
- OCR text for at least one sign-like crop
- a GeoJSON evidence file that the app can render as points
- a run manifest with model names, versions, input manifest checksum, and command line

## Follow-Up App Integration

Add an endpoint:

```text
GET /api/parking-evidence/imagery/detections
```

Render:

- sign detections as point markers
- meter detections as point markers
- OCR text in marker popups
- confidence and capture date in marker details

Only after manual review should evidence be promoted into curb-rule candidates.
