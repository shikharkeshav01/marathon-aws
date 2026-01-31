#!/bin/bash
set -e

COLLECTION_ID="marathon-participants"
REGION="${AWS_REGION:-ap-south-1}"

echo "Setting up AWS Rekognition Collection for Face Matching"
echo "========================================================="
echo ""

# Check if collection already exists
if aws rekognition describe-collection --collection-id $COLLECTION_ID --region $REGION 2>/dev/null; then
    echo "✅ Collection '$COLLECTION_ID' already exists"
    
    # Show collection stats
    FACE_COUNT=$(aws rekognition describe-collection --collection-id $COLLECTION_ID --region $REGION --query 'FaceCount' --output text)
    echo "   Face count: $FACE_COUNT"
else
    echo "Creating Rekognition collection: $COLLECTION_ID"
    aws rekognition create-collection \
        --collection-id $COLLECTION_ID \
        --region $REGION
    
    echo "✅ Collection created successfully"
fi

echo ""
echo "Next steps:"
echo "1. Update Lambda IAM role with Rekognition permissions (see FACE_MATCHING_SETUP.md)"
echo "2. Deploy updated extract_bib_number_handler Lambda"
echo "3. Deploy index_user_profile_image Lambda"
echo "4. Index existing user profile images"
echo ""
echo "To list faces in collection:"
echo "  aws rekognition list-faces --collection-id $COLLECTION_ID --region $REGION"
echo ""
echo "To delete collection (if needed):"
echo "  aws rekognition delete-collection --collection-id $COLLECTION_ID --region $REGION"
