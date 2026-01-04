# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This is an AWS serverless application for processing marathon event photos and generating personalized video reels. It uses Lambda functions orchestrated by Step Functions to:
1. Extract bib numbers from race photos using computer vision (YOLO + EasyOCR)
2. Generate personalized video reels by overlaying participant photos and data on template videos

## Architecture

The system consists of two main Step Functions pipelines:

### 1. process-event-images Pipeline
- **API Gateway** → **Step Function** → Lambdas
- **Lambda1** (list_images_handler): Lists images from Google Drive folder, uploads CSV of participants to DynamoDB
- **Map State** → **Lambda2** (extract_bib_number_handler): Downloads each image, extracts bib numbers using YOLO+EasyOCR, uploads to S3, writes to DynamoDB
- **Lambda3** (image_processing_completion_handler): Updates request status

### 2. generate-event-reels Pipeline
- **API Gateway** → **Step Function** → Lambdas
- **Lambda1** (event_images_bib_extraction_handler): Fetches all bib IDs for an event from DynamoDB
- **Map State** → **Lambda2** (reel_generation_handler): Downloads images and background video from S3, overlays images/text using MoviePy, uploads final reel
- **Lambda3** (reel_generation_completion_handler): Updates request status

## Key Technologies

- **Container Lambdas**: `extract_bib_number_handler` and `reel_generation_handler` use Docker containers (arm64) for ML models (YOLO, EasyOCR, MoviePy)
- **Zip Lambdas**: All other handlers are packaged as zip files
- **DynamoDB Tables**: EventRequests, EventImages, EventReels, EventParticipants
- **S3 Bucket**: marathon-photos (stores images and generated reels)
- **Google Drive Integration**: Service account credentials stored in SSM Parameter Store

## Common Commands

### Deploy Everything
```bash
# Deploy both pipelines
./scripts/deploy.sh both

# Deploy individual pipelines
./scripts/deploy.sh process  # process-event-images only
./scripts/deploy.sh reels    # generate-event-reels only
```

### Deploy Individual Lambda Functions
Each Lambda has a `deploy.sh` script in its directory:
```bash
# Navigate to Lambda directory and deploy
cd lambdas/process_event_images/list_images_handler && ./deploy.sh
cd lambdas/process_event_images/extract_bib_number_handler && ./deploy.sh
cd lambdas/generate_event_reels/reel_generation_handler && ./deploy.sh
```

### Upload Google Service Account to SSM
```bash
./scripts/upload-sa-to-ssm.sh .secrets/sa.json google-service-account
```

### Test Container Lambdas Locally
```bash
# Build Docker image
cd lambdas/process_event_images/extract_bib_number_handler
docker build --platform linux/arm64 -t extract_bib_number_handler .

# Run container
docker run --platform linux/arm64 -p 9000:8080 extract_bib_number_handler

# Invoke locally
curl -XPOST "http://localhost:9000/2015-03-31/functions/function/invocations" \
  -d '{"eventId": "123", "fileId": "xyz"}'
```

## Important Implementation Details

### Container Lambda Architecture
- Both container Lambdas (`extract_bib_number_handler` and `reel_generation_handler`) use **arm64 architecture**
- Base image: `public.ecr.aws/lambda/python:3.13-arm64`
- Architecture cannot be changed after Lambda creation - must delete and recreate if wrong
- Deploy script handles ECR authentication, Docker buildx for arm64, and manifest list prevention

### DynamoDB Schema

**EventRequests**
- PK: RequestId (String)
- Attributes: DriveUrl, EventId, Status, RequestType, CsvKey (for process), ReelS3Key/ReelConfiguration (for reels)

**EventImages**
- PK: Id (String, uuid4)
- Attributes: BibId, EventId, Filename
- GSI: EventId-index (allows querying all images for an event)

**EventReels**
- PK: EventId (Number)
- SK: BibId (String)
- Attributes: EventReelId, ReelPath

**EventParticipants**
- PK: EventId (Number)
- SK: BibId (String)
- Attributes: ParticipantName, TicketName, Phone, Email, CompletionTime (Decimal)

### S3 Key Structure
```
{eventId}/ProcessedImages/{filename}     # Images with detected bibs
{eventId}/UnProcessedImages/{filename}   # Images with no bibs or errors
{eventId}/ProcessedReels/{bibId}.mp4     # Generated reels
```

### Environment Variables
All Lambdas receive:
- `RAW_BUCKET`: marathon-photos
- `EVENT_REQUESTS_TABLE`: EventRequests
- `EVENT_IMAGES_TABLE`: EventImages
- `EVENT_REELS_TABLE`: EventReels
- `EVENT_PARTICIPANTS_TABLE`: EventParticipants
- `GDRIVE_SA_SSM_PARAM`: google-service-account

### Google Service Account Access
Store credentials in SSM Parameter Store (SecureString), not in code:
```python
ssm = boto3.client("ssm")
param_name = os.environ.get("GDRIVE_SA_SSM_PARAM", "google-service-account")
sa_json_str = ssm.get_parameter(Name=param_name, WithDecryption=True)["Parameter"]["Value"]
sa_info = json.loads(sa_json_str)
creds = service_account.Credentials.from_service_account_info(sa_info, scopes=SCOPES)
```

### Computer Vision Pipeline (extract_bib_number_handler)
1. Download image from Google Drive
2. Run YOLO (yolov8n.pt) to detect people
3. For each person detection, crop region and run EasyOCR
4. Filter OCR results for numeric bib numbers (2-5 digits)
5. Validate bib exists in EventParticipants table
6. Upload to S3 (ProcessedImages if bibs found, UnProcessedImages if not)
7. Write entries to EventImages table (one per bib per image)

### Reel Generation Pipeline (reel_generation_handler)
1. Query EventImages to get all images for a bib number
2. Download background video from S3 (reelS3Key)
3. Download participant images from S3
4. Parse reel configuration JSON (supports ${completionTime} and ${participantsName} substitution)
5. Overlay images and text on video using MoviePy
6. Upload result to S3 as `{eventId}/ProcessedReels/{bibId}.mp4`
7. Write entry to EventReels table

### Reel Configuration Format
JSON with `overlays` array containing image and text overlays:
```json
{
  "overlays": [
    {
      "type": "image",
      "x": 100,
      "y": 200,
      "width": 300,
      "height": 400,
      "start_time": 0,
      "end_time": 5
    },
    {
      "type": "text",
      "text": "Name: ${participantsName}",
      "x": 100,
      "y": 500,
      "font_size": 48,
      "color": "#FFFFFF",
      "start_time": 0,
      "end_time": 10
    }
  ]
}
```

## Deployment Script Behavior

### Main Deploy Script (`scripts/deploy.sh`)
1. Creates DynamoDB tables (if not exist) with GSIs
2. Creates IAM roles (lambda-role, sfn-role) with inline policies
3. Calls individual Lambda deploy scripts
4. Creates CloudWatch log groups
5. Deploys Step Functions state machines
6. Returns ARNs of deployed resources

### Lambda Deploy Scripts
- **Zip-based Lambdas**: Package handler + dependencies, upload to Lambda
- **Container-based Lambdas**: Build Docker image, push to ECR, create/update Lambda with image URI
- All scripts support standalone mode (no params) or called-from-main mode (with params)
- Container deploys use buildx with `--provenance=false --sbom=false` to avoid manifest list issues
- Wait for Lambda state=Active and LastUpdateStatus≠InProgress before proceeding

### Step Function Deploy Scripts
- Read definition.json template
- Substitute Lambda ARNs using sed
- Create or update state machine with AWS CLI

## Testing Notes

When testing locally:
- Use `.secrets/` directory for sensitive files (gitignored)
- Container Lambdas require arm64 platform (use `--platform linux/arm64` with Docker)
- Test data should use valid EventId and BibId combinations from DynamoDB
- S3 bucket `marathon-photos` must exist and be accessible

## Common Issues

1. **Architecture Mismatch**: Lambda created with wrong architecture (x86_64 vs arm64)
   - Solution: Deploy script deletes and recreates function with correct architecture

2. **Manifest List Error**: ECR push creates manifest list instead of single image manifest
   - Solution: Use `docker buildx` with `--provenance=false --sbom=false` flags

3. **Lambda Update Conflicts**: Updating function code while configuration update in progress
   - Solution: Deploy script waits for both State=Active and LastUpdateStatus≠InProgress

4. **Bib Extraction Returns Empty**: Valid bib numbers filtered out
   - Cause: Bib number not in EventParticipants table
   - Solution: Ensure CSV uploaded correctly in list_images_handler

5. **Reel Generation Fails**: Not enough images for overlay count
   - Cause: Fewer images found than overlays defined in configuration
   - Solution: Check EventImages table has sufficient entries for the bib number