#!/usr/bin/env bash
set -euo pipefail

# Script to add EventId-index GSI to EventImages table if it doesn't exist
# Usage: ./scripts/add_event_id_gsi.sh [region]

REGION="${1:-${AWS_REGION:-${AWS_DEFAULT_REGION:-ap-south-1}}}"
TABLE_NAME="EventImages"
INDEX_NAME="EventId-index"

echo "Checking table ${TABLE_NAME} in region ${REGION}..."

# Check if table exists
if ! aws dynamodb describe-table --table-name "${TABLE_NAME}" --region "${REGION}" >/dev/null 2>&1; then
    echo "Error: Table ${TABLE_NAME} does not exist."
    exit 1
fi

# Check if index exists
PROJECTION_TYPE=$(aws dynamodb describe-table --table-name "${TABLE_NAME}" --region "${REGION}" \
    --query "Table.GlobalSecondaryIndexes[?IndexName=='${INDEX_NAME}'].Projection.ProjectionType" \
    --output text)

if [[ "${PROJECTION_TYPE}" != "None" && -n "${PROJECTION_TYPE}" ]]; then
    echo "Index ${INDEX_NAME} already exists on table ${TABLE_NAME}."
    exit 0
fi

echo "Creating index ${INDEX_NAME} on table ${TABLE_NAME}..."

# Update table to add GSI
# Note: We must verify if AttributeDefinitions for EventId already exists or needs to be added.
# However, adding it to AttributeDefinitions again is generally harmless or handled by the CLI if we are just careful.
# Actually, update-table requires AttributeDefinitions for the keys used in the new index.

aws dynamodb update-table \
    --table-name "${TABLE_NAME}" \
    --attribute-definitions AttributeName=EventId,AttributeType=N \
    --global-secondary-index-updates \
    "[{\"Create\":{\"IndexName\": \"${INDEX_NAME}\", \"KeySchema\":[{\"AttributeName\":\"EventId\",\"KeyType\":\"HASH\"}], \"Projection\":{\"ProjectionType\":\"ALL\"}}}]" \
    --region "${REGION}"

echo "Index creation initiated. This may take some time. Check status with:"
echo "aws dynamodb describe-table --table-name ${TABLE_NAME} --region ${REGION}"
