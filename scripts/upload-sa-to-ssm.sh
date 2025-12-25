#!/usr/bin/env bash
set -euo pipefail

# Upload Google service account JSON to SSM Parameter Store
# Usage: ./upload-sa-to-ssm.sh [path-to-sa.json] [parameter-name]

SA_FILE="${1:-.secrets/sa.json}"
PARAM_NAME="${2:-google-service-account}"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-ap-south-1}}"

if [[ ! -f "${SA_FILE}" ]]; then
  echo "Error: Service account file not found at ${SA_FILE}" >&2
  echo "Usage: $0 [path-to-sa.json] [parameter-name]" >&2
  echo "" >&2
  echo "Example:" >&2
  echo "  $0 .secrets/sa.json google-service-account" >&2
  exit 1
fi

echo "Uploading ${SA_FILE} to SSM Parameter Store as ${PARAM_NAME}..."

# Check if parameter exists
if aws ssm get-parameter --name "${PARAM_NAME}" --region "${REGION}" >/dev/null 2>&1; then
  echo "Parameter ${PARAM_NAME} exists. Updating..."
  aws ssm put-parameter \
    --name "${PARAM_NAME}" \
    --value "file://${SA_FILE}" \
    --type "SecureString" \
    --overwrite \
    --region "${REGION}" >/dev/null
else
  echo "Creating new parameter ${PARAM_NAME}..."
  aws ssm put-parameter \
    --name "${PARAM_NAME}" \
    --value "file://${SA_FILE}" \
    --type "SecureString" \
    --region "${REGION}" >/dev/null
fi

echo "✓ Successfully uploaded to SSM Parameter Store: ${PARAM_NAME}"
echo ""
echo "To use in Lambda, set environment variable:"
echo "  GDRIVE_SA_SSM_PARAM=${PARAM_NAME}"

