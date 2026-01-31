# Face Matching Setup Guide

This guide explains how to set up and use the face matching feature for marathon event images.

## Overview

When bib numbers cannot be detected in event images, the system falls back to face matching using AWS Rekognition. This matches faces in event photos against user profile images stored in the User table.

## Architecture

### Components

1. **User Table (DynamoDB)**
   - `Email` (PK): User's email address
   - `ProfileImage`: S3 key of profile image
   - `ClientId`: User identifier
   - `FaceIndexStatus`: Status of face indexing (indexed, no_face_detected, invalid_image, error)
   - `RekognitionFaceId`: AWS Rekognition Face ID

2. **EVENT_PARTICIPANTS_TABLE (DynamoDB)**
   - Links EventId + Email to BibId
   - Used to validate face matches belong to event participants

3. **EVENT_IMAGES_TABLE (DynamoDB)**
   - Now includes `MatchType` field: 'bib' or 'face'
   - Tracks how each image was matched

4. **AWS Rekognition Collection**
   - Collection ID: `marathon-participants`
   - Stores face embeddings indexed by email (ExternalImageId)

### Lambda Functions

1. **extract_bib_number_handler** (Modified)
   - Tries bib number extraction first
   - Falls back to face matching if no bibs found
   - Stores results with match type

2. **index_user_profile_image** (New)
   - Indexes user profile images into Rekognition
   - Triggered when users upload/update profile images
   - Updates User table with indexing status

## Setup Instructions

### 1. Create Rekognition Collection

```bash
aws rekognition create-collection \
  --collection-id marathon-participants \
  --region us-east-1
```

### 2. Update IAM Permissions

Add these permissions to your Lambda execution role:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "rekognition:DetectFaces",
        "rekognition:SearchFacesByImage",
        "rekognition:IndexFaces",
        "rekognition:ListFaces",
        "rekognition:DeleteFaces"
      ],
      "Resource": "*"
    }
  ]
}
```

### 3. Update User Table Schema

Add these optional fields to your User table:
- `FaceIndexStatus` (String): Status of face indexing
- `RekognitionFaceId` (String): Rekognition Face ID

### 4. Update EVENT_IMAGES_TABLE Schema

Add this field:
- `MatchType` (String): 'bib' or 'face'

### 5. Deploy Updated Lambda

```bash
cd lambdas/process_event_images/extract_bib_number_handler
./deploy.sh
```

Add these environment variables to the Lambda:
- `REKOGNITION_COLLECTION_ID`: marathon-participants
- `FACE_MATCH_THRESHOLD`: 85.0 (adjust as needed)
- `MAX_FACES_TO_DETECT`: 10

### 6. Deploy Profile Image Indexing Lambda

```bash
cd lambdas/index_user_profile_image
chmod +x deploy.sh
./deploy.sh
```

Update the deploy script with your:
- AWS Account ID
- IAM Role ARN
- Region
- S3 bucket name for profile images

### 7. Index Existing User Profile Images

Run this script to index all existing users:

```python
import boto3

dynamodb = boto3.resource('dynamodb')
lambda_client = boto3.client('lambda')

user_table = dynamodb.Table('Users')
response = user_table.scan()

for user in response['Items']:
    if user.get('ProfileImage'):
        lambda_client.invoke(
            FunctionName='IndexUserProfileImage',
            InvocationType='Event',  # Async
            Payload=json.dumps({
                'email': user['Email'],
                'profileImageS3Key': user['ProfileImage'],
                's3Bucket': 'marathon-user-profiles'
            })
        )
        print(f"Queued indexing for {user['Email']}")
```

## Usage

### Indexing Profile Images

**Option 1: Direct Lambda Invocation**

```bash
aws lambda invoke \
  --function-name IndexUserProfileImage \
  --payload '{"email":"user@example.com","profileImageS3Key":"profile-images/user123.jpg","s3Bucket":"marathon-user-profiles"}' \
  response.json
```

**Option 2: S3 Event Trigger** (Recommended)

Configure S3 to trigger the Lambda when profile images are uploaded:

```bash
aws s3api put-bucket-notification-configuration \
  --bucket marathon-user-profiles \
  --notification-configuration '{
    "LambdaFunctionConfigurations": [{
      "LambdaFunctionArn": "arn:aws:lambda:REGION:ACCOUNT:function:IndexUserProfileImage",
      "Events": ["s3:ObjectCreated:*"],
      "Filter": {
        "Key": {
          "FilterRules": [{"Name": "prefix", "Value": "profile-images/"}]
        }
      }
    }]
  }'
```

### Processing Event Images

No changes needed! The existing workflow automatically falls back to face matching:

1. Image uploaded to S3
2. Lambda extracts bib numbers via OCR
3. **If no bibs found**: Lambda attempts face matching
4. Results stored in EVENT_IMAGES_TABLE with `MatchType`

## Configuration

### Environment Variables

**extract_bib_number_handler:**
- `REKOGNITION_COLLECTION_ID`: Collection ID (default: marathon-participants)
- `FACE_MATCH_THRESHOLD`: Minimum similarity score 0-100 (default: 85.0)
- `MAX_FACES_TO_DETECT`: Max faces per image (default: 10)
- `EVENT_PARTICIPANTS_TABLE`: DynamoDB table name
- `EVENT_IMAGES_TABLE`: DynamoDB table name

**index_user_profile_image:**
- `REKOGNITION_COLLECTION_ID`: Collection ID (default: marathon-participants)
- `USER_TABLE`: User table name
- `PROFILE_IMAGES_BUCKET`: S3 bucket for profile images

### Tuning Face Match Threshold

- **85-90%**: Recommended for production (good balance)
- **90-95%**: Stricter matching (fewer false positives)
- **75-85%**: More lenient (more matches, some false positives)

Test with your dataset and adjust based on accuracy vs coverage needs.

## Monitoring

### CloudWatch Logs

**Successful face match:**
```
[FALLBACK] No bib numbers found in IMG_1234.jpg, attempting face matching...
[REKOGNITION] Found 2 face matches above 85.0% threshold
[MATCH] Face matched to email=user@example.com, similarity=92.45%
[SUCCESS] Participant validated: email=user@example.com, bib=12345
[SUCCESS] Face matching found 1 participants: ['12345']
```

**No matches:**
```
[FALLBACK] No bib numbers found in IMG_5678.jpg, attempting face matching...
[REKOGNITION] Found 0 face matches above 85.0% threshold
[INFO] No face matches found for IMG_5678.jpg
```

### DynamoDB Queries

**Get face-matched images:**
```python
table.scan(
    FilterExpression='MatchType = :type',
    ExpressionAttributeValues={':type': 'face'}
)
```

**Check user face index status:**
```python
user_table.get_item(Key={'Email': 'user@example.com'})
# Check FaceIndexStatus and RekognitionFaceId fields
```

## Troubleshooting

### "Collection not found" error

Create the collection:
```bash
aws rekognition create-collection --collection-id marathon-participants
```

### "No face detected in profile image"

- Ensure profile image has a clear, frontal face
- Face should be at least 40x40 pixels
- Good lighting and resolution
- Use `QualityFilter='AUTO'` to enforce quality standards

### Face matches not working

1. Check if user's face is indexed:
   ```bash
   aws rekognition list-faces --collection-id marathon-participants
   ```

2. Verify user is registered for the event in EVENT_PARTICIPANTS_TABLE

3. Check face match threshold (try lowering temporarily for testing)

4. Review CloudWatch logs for detailed error messages

### High false positive rate

- Increase `FACE_MATCH_THRESHOLD` (e.g., 90 or 95)
- Ensure profile images are recent and high quality
- Consider adding additional validation logic

## Cost Considerations

**AWS Rekognition Pricing (as of 2024):**
- Index Faces: $0.001 per image
- Search Faces: $0.001 per image searched
- Storage: $0.01 per 1,000 faces per month

**Example costs for 10,000 users:**
- Initial indexing: $10
- Monthly storage: $0.10
- Processing 100,000 event images: $100

Much cheaper than manual tagging!

## Best Practices

1. **Profile Image Guidelines:**
   - Clear, frontal face
   - Good lighting
   - Recent photo (< 2 years old)
   - Minimum 200x200 pixels
   - No sunglasses or face coverings

2. **Re-index on Profile Updates:**
   - Automatically trigger indexing when users update profile images
   - Delete old faces before indexing new ones (handled automatically)

3. **Validate Event Participation:**
   - Always cross-reference face matches with EVENT_PARTICIPANTS_TABLE
   - Only match participants registered for that specific event

4. **Monitor Match Quality:**
   - Track match rates and similarity scores
   - Adjust threshold based on accuracy metrics
   - Review false positives/negatives

5. **Privacy Considerations:**
   - Inform users their profile images will be used for face matching
   - Provide opt-out mechanism if needed
   - Delete faces from collection when users delete accounts

## API Reference

### face_matching.py Functions

**`detect_faces_in_image(image_bytes)`**
- Detects faces in image
- Returns list of face details with bounding boxes

**`match_faces_to_participants(image_bytes, event_id)`**
- Matches faces against collection
- Validates against event participants
- Returns list of matched participants with bib IDs

**`index_user_profile_image(email, profile_image_s3_key, s3_bucket)`**
- Indexes user's profile image
- Returns FaceId or error

**`delete_user_face_from_collection(email)`**
- Removes user's face from collection
- Returns success status

## Next Steps

1. Test with sample images
2. Index existing user profiles
3. Monitor match accuracy
4. Adjust threshold as needed
5. Set up S3 event triggers for automatic profile indexing
