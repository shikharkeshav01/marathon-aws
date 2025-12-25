# Marathon AWS Deployment

AWS Lambda and Step Functions deployment for event image processing and reel generation pipelines.

## Architecture

Two main pipelines:
1. **process-event-images**: API Gateway → Step Function → Lambda1 (list_images_handler) → Map(Lambda2: extract_bib_number_handler) → Lambda3 (image_processing_completion_handler)
2. **generate-event-reels**: API Gateway → Step Function → Lambda1 (event_images_bib_extraction_handler) → Map(Lambda2: reel_generation_handler) → Lambda3 (reel_generation_completion_handler)

## Prerequisites

- AWS CLI configured with appropriate permissions
- Python 3.13
- Docker (for container-based Lambda2 functions)
- zip utility

## Setup

### 1. Store Google Service Account JSON in SSM

Place your Google service account JSON file in `.secrets/sa.json` (this directory is gitignored) and upload it to SSM:

```bash
# Create .secrets directory and place your sa.json there
mkdir -p .secrets
# Copy your service account JSON to .secrets/sa.json

# Upload to SSM Parameter Store
./scripts/upload-sa-to-ssm.sh .secrets/sa.json google-service-account
```

The script will create/update the SSM parameter `google-service-account` as a SecureString.

### 2. Deploy Infrastructure

Deploy all pipelines:
```bash
./scripts/deploy.sh both
```

Deploy individual pipeline:
```bash
./scripts/deploy.sh process  # process-event-images only
./scripts/deploy.sh reels    # generate-event-reels only
```

### 3. Deploy Individual Lambda Functions

Each Lambda function can be deployed individually:

```bash
# Process event images Lambdas
cd lambdas/process_event_images/list_images_handler && ./deploy.sh
cd lambdas/process_event_images/extract_bib_number_handler && ./deploy.sh
cd lambdas/process_event_images/image_processing_completion_handler && ./deploy.sh

# Generate event reels Lambdas
cd lambdas/generate_event_reels/event_images_bib_extraction_handler && ./deploy.sh
cd lambdas/generate_event_reels/reel_generation_handler && ./deploy.sh
cd lambdas/generate_event_reels/reel_generation_completion_handler && ./deploy.sh
```

## Lambda Code Updates

To use the service account from SSM in your Lambda code, update your handlers to fetch from SSM:

```python
import os
import json
import boto3
from google.oauth2 import service_account

# Get SSM parameter name from environment
ssm_param_name = os.environ.get("GDRIVE_SA_SSM_PARAM", "google-service-account")

# Fetch service account JSON from SSM
ssm = boto3.client("ssm")
sa_json_str = ssm.get_parameter(Name=ssm_param_name, WithDecryption=True)["Parameter"]["Value"]
sa_info = json.loads(sa_json_str)

# Create credentials from the JSON
creds = service_account.Credentials.from_service_account_info(
    sa_info,
    scopes=["https://www.googleapis.com/auth/drive.readonly"]
)
```

## Environment Variables

Lambdas automatically receive:
- `EVENT_REQUESTS_TABLE`: DynamoDB table name for event requests
- `EVENT_IMAGES_TABLE`: DynamoDB table name for event images
- `EVENT_REELS_TABLE`: DynamoDB table name for event reels
- `GDRIVE_SA_SSM_PARAM`: SSM parameter name for Google service account (default: `google-service-account`)

## Notes

- Container-based Lambda2 functions (`extract_bib_number_handler` and `reel_generation_handler`) require a `Dockerfile` in their directories
- DynamoDB tables are created automatically with PAY_PER_REQUEST billing
- IAM roles are created automatically with necessary permissions (DynamoDB, SSM, CloudWatch Logs)
- For Google service account JSON, store it in Secrets Manager/SSM and fetch in code; do not commit it. Add local copies under `.secrets/` and keep `.gitignore` updated.
