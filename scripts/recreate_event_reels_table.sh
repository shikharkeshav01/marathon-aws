#!/usr/bin/env bash
set -euo pipefail

# Script to delete and recreate EventReels table with new schema
# WARNING: This will delete all existing data in the EventReels table

REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-ap-south-1}}"
TABLE_NAME="EventReels"

echo "=========================================="
echo "EventReels Table Recreation Script"
echo "=========================================="
echo ""
echo "WARNING: This will delete the EventReels table and all its data!"
echo "New Schema:"
echo "  - Primary Key: ReelId (String/UUID)"
echo "  - Attributes: EventId, BibId, RequestId, ReelPath"
echo "  - GSI: EventId-BibId-index (PK: EventId, SK: BibId)"
echo ""
read -p "Are you sure you want to continue? (yes/no): " confirm

if [ "$confirm" != "yes" ]; then
    echo "Aborted."
    exit 0
fi

# Delete existing table
if aws dynamodb describe-table --table-name "$TABLE_NAME" --region "$REGION" >/dev/null 2>&1; then
    echo ""
    echo "Deleting existing table $TABLE_NAME..."
    aws dynamodb delete-table --table-name "$TABLE_NAME" --region "$REGION" >/dev/null
    
    echo "Waiting for table to be deleted..."
    aws dynamodb wait table-not-exists --table-name "$TABLE_NAME" --region "$REGION"
    echo "Table deleted successfully."
else
    echo ""
    echo "Table $TABLE_NAME does not exist. Proceeding to create it."
fi

# Create new table with updated schema
echo ""
echo "Creating table $TABLE_NAME with new schema..."
aws dynamodb create-table \
    --table-name "$TABLE_NAME" \
    --attribute-definitions \
        AttributeName=ReelId,AttributeType=S \
        AttributeName=EventId,AttributeType=N \
        AttributeName=BibId,AttributeType=S \
    --key-schema AttributeName=ReelId,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST \
    --global-secondary-indexes \
        "[{\"IndexName\":\"EventId-BibId-index\",\"KeySchema\":[{\"AttributeName\":\"EventId\",\"KeyType\":\"HASH\"},{\"AttributeName\":\"BibId\",\"KeyType\":\"RANGE\"}],\"Projection\":{\"ProjectionType\":\"ALL\"}}]" \
    --region "$REGION" >/dev/null

echo ""
echo "=========================================="
echo "✓ Table $TABLE_NAME has been recreated successfully!"
echo "=========================================="
echo ""
echo "Table Details:"
echo "  - Region: $REGION"
echo "  - Primary Key: ReelId (S)"
echo "  - GSI: EventId-BibId-index"
echo ""
echo "To verify the table, run:"
echo "  aws dynamodb describe-table --table-name $TABLE_NAME --region $REGION"
