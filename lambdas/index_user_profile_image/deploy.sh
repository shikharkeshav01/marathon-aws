#!/bin/bash
set -e

FUNCTION_NAME="index_user_profile_image"
REGION="ap-south-1"
ROLE_ARN="arn:aws:iam::963311703323:role/lambda-role"
COLLECTION_ID="marathon-participants"
USER_TABLE="User"
PROFILE_IMAGES_BUCKET="marathon-photos"

echo "Deploying $FUNCTION_NAME Lambda..."

# Package Lambda
zip -r function.zip lambda_function.py

# Create or update Lambda function
if aws lambda get-function --function-name $FUNCTION_NAME --region $REGION 2>/dev/null; then
    echo "Updating existing function..."
    aws lambda update-function-code \
        --function-name $FUNCTION_NAME \
        --zip-file fileb://function.zip \
        --region $REGION
    
    aws lambda update-function-configuration \
        --function-name $FUNCTION_NAME \
        --environment "Variables={REKOGNITION_COLLECTION_ID=$COLLECTION_ID,USER_TABLE=$USER_TABLE,PROFILE_IMAGES_BUCKET=$PROFILE_IMAGES_BUCKET}" \
        --timeout 60 \
        --memory-size 512 \
        --region $REGION
else
    echo "Creating new function..."
    aws lambda create-function \
        --function-name $FUNCTION_NAME \
        --runtime python3.11 \
        --role $ROLE_ARN \
        --handler lambda_function.lambda_handler \
        --zip-file fileb://function.zip \
        --timeout 60 \
        --memory-size 512 \
        --environment "Variables={REKOGNITION_COLLECTION_ID=$COLLECTION_ID,USER_TABLE=$USER_TABLE,PROFILE_IMAGES_BUCKET=$PROFILE_IMAGES_BUCKET}" \
        --region $REGION
fi

# Clean up
rm function.zip

echo "✅ Deployment complete!"
echo ""
echo "Next steps:"
echo "1. Create Rekognition collection: aws rekognition create-collection --collection-id $COLLECTION_ID --region $REGION"
echo "2. Grant Lambda permissions: rekognition:IndexFaces, rekognition:ListFaces, rekognition:DeleteFaces"
echo "3. Test with: aws lambda invoke --function-name $FUNCTION_NAME --payload '{\"email\":\"test@example.com\",\"profileImageS3Key\":\"profile-images/test.jpg\",\"s3Bucket\":\"$PROFILE_IMAGES_BUCKET\"}' response.json"
