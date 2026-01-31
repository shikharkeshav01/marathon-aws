import os
import json
import boto3
import traceback

rekognition = boto3.client('rekognition')
s3 = boto3.client('s3')
ddb = boto3.resource('dynamodb')

COLLECTION_ID = os.environ.get('REKOGNITION_COLLECTION_ID', 'marathon-participants')
USER_TABLE = os.environ['USER_TABLE']


def index_user_profile_image(email, profile_image_s3_key, s3_bucket):
    """
    Index a user's profile image into Rekognition Collection.
    
    Args:
        email: User's email (used as ExternalImageId)
        profile_image_s3_key: S3 key of the profile image
        s3_bucket: S3 bucket name
        
    Returns:
        Dict with success status and FaceId or error message
    """
    try:
        # First, delete any existing faces for this user
        print(f"[INFO] Checking for existing faces for {email}...")
        delete_existing_faces(email)
        
        # Index the new face
        # Rekognition ExternalImageId only allows [a-zA-Z0-9_.\-:]+
        # Replace @ with __ (double underscore) to make it valid
        external_id = email.replace('@', '__').replace('+', '_')
        
        print(f"[INFO] Indexing face for {email} (ExternalImageId: {external_id}) from s3://{s3_bucket}/{profile_image_s3_key}")
        response = rekognition.index_faces(
            CollectionId=COLLECTION_ID,
            Image={
                'S3Object': {
                    'Bucket': s3_bucket,
                    'Name': profile_image_s3_key
                }
            },
            ExternalImageId=external_id,
            MaxFaces=1,
            QualityFilter='AUTO',
            DetectionAttributes=['DEFAULT']
        )
        
        face_records = response.get('FaceRecords', [])
        
        if face_records:
            face_id = face_records[0]['Face']['FaceId']
            confidence = face_records[0]['Face']['Confidence']
            print(f"[SUCCESS] Indexed face for {email}: FaceId={face_id}, Confidence={confidence:.2f}%")
            
            # Get ClientId from User table for update
            client_id = get_client_id_for_email(email)
            
            # Update User table with indexing status
            if client_id:
                update_user_face_status(email, client_id, face_id, 'indexed')
            
            return {
                'success': True,
                'faceId': face_id,
                'confidence': confidence,
                'email': email
            }
        else:
            print(f"[WARN] No face detected in profile image for {email}")
            client_id = get_client_id_for_email(email)
            if client_id:
                update_user_face_status(email, client_id, None, 'no_face_detected')
            
            return {
                'success': False,
                'error': 'No face detected in image',
                'email': email
            }
            
    except rekognition.exceptions.InvalidParameterException as e:
        error_msg = f"Invalid image for {email}: {str(e)}"
        print(f"[ERROR] {error_msg}")
        client_id = get_client_id_for_email(email)
        if client_id:
            update_user_face_status(email, client_id, None, 'invalid_image')
        return {
            'success': False,
            'error': error_msg,
            'email': email
        }
    except rekognition.exceptions.ResourceNotFoundException as e:
        error_msg = f"Rekognition collection '{COLLECTION_ID}' not found"
        print(f"[ERROR] {error_msg}: {e}")
        print(f"[INFO] Create collection with: aws rekognition create-collection --collection-id {COLLECTION_ID}")
        return {
            'success': False,
            'error': error_msg,
            'email': email
        }
    except Exception as e:
        error_msg = f"Failed to index face: {str(e)}"
        print(f"[ERROR] {error_msg}")
        print(traceback.format_exc())
        client_id = get_client_id_for_email(email)
        if client_id:
            update_user_face_status(email, client_id, None, 'error')
        return {
            'success': False,
            'error': error_msg,
            'email': email
        }


def delete_existing_faces(email):
    """
    Remove existing faces for a user from the Rekognition Collection.
    Called before indexing a new face to avoid duplicates.
    """
    try:
        # Encode email same way as when indexing
        external_id = email.replace('@', '__').replace('+', '_')
        
        response = rekognition.list_faces(
            CollectionId=COLLECTION_ID,
            MaxResults=100
        )
        
        face_ids_to_delete = []
        for face in response.get('Faces', []):
            if face.get('ExternalImageId') == external_id:
                face_ids_to_delete.append(face['FaceId'])
        
        if face_ids_to_delete:
            rekognition.delete_faces(
                CollectionId=COLLECTION_ID,
                FaceIds=face_ids_to_delete
            )
            print(f"[INFO] Deleted {len(face_ids_to_delete)} existing face(s) for {email}")
        else:
            print(f"[INFO] No existing faces found for {email}")
            
    except Exception as e:
        print(f"[WARN] Failed to delete existing faces for {email}: {e}")


def get_client_id_for_email(email):
    """Get ClientId for a given email from User table."""
    try:
        table = ddb.Table(USER_TABLE)
        response = table.scan()
        for item in response.get('Items', []):
            if item.get('Email') == email:
                return item.get('ClientId')
        return None
    except Exception as e:
        print(f"[ERROR] Failed to get ClientId for {email}: {e}")
        return None


def update_user_face_status(email, client_id, face_id, status):
    """
    Update User table with face indexing status.
    
    Args:
        email: User's email
        client_id: User's ClientId
        face_id: Rekognition FaceId (or None)
        status: 'indexed', 'no_face_detected', 'invalid_image', 'error'
    """
    try:
        table = ddb.Table(USER_TABLE)
        update_expr = "SET FaceIndexStatus = :status"
        expr_values = {':status': status}
        
        if face_id:
            update_expr += ", RekognitionFaceId = :face_id"
            expr_values[':face_id'] = face_id
        
        table.update_item(
            Key={'Email': email, 'ClientId': client_id},
            UpdateExpression=update_expr,
            ExpressionAttributeValues=expr_values
        )
        print(f"[INFO] Updated User table for {email}: status={status}")
        
    except Exception as e:
        print(f"[WARN] Failed to update User table for {email}: {e}")


def lambda_handler(event, context):
    """
    Lambda handler to index user profile images into Rekognition.
    
    Expected event format:
    {
        "email": "user@example.com",
        "profileImageS3Key": "profile-images/user123.jpg",
        "s3Bucket": "marathon-user-profiles"
    }
    
    Or triggered by S3 event when profile image is uploaded:
    {
        "Records": [{
            "s3": {
                "bucket": {"name": "..."},
                "object": {"key": "profile-images/user@example.com.jpg"}
            }
        }]
    }
    """
    print(json.dumps(event))
    
    try:
        # Handle direct invocation
        if 'email' in event:
            email = event['email']
            profile_image_s3_key = event['profileImageS3Key']
            s3_bucket = event.get('s3Bucket', os.environ.get('PROFILE_IMAGES_BUCKET'))
            
        # Handle S3 event trigger
        elif 'Records' in event and len(event['Records']) > 0:
            from urllib.parse import unquote
            
            record = event['Records'][0]
            s3_bucket = record['s3']['bucket']['name']
            profile_image_s3_key_encoded = record['s3']['object']['key']
            
            # URL decode the S3 key (S3 events have URL-encoded keys)
            profile_image_s3_key = unquote(profile_image_s3_key_encoded)
            
            # Extract email from S3 key or User table lookup
            # Assuming key format: profile-images/{email}.jpg
            print(f"[INFO] Extracting email from S3 key: {profile_image_s3_key}")
            email = extract_email_from_s3_key(profile_image_s3_key_encoded)
            print(f"[INFO] Extracted email: {email}")
            
            if not email:
                print(f"[ERROR] Could not extract email from S3 key: {profile_image_s3_key}")
                return {
                    'statusCode': 400,
                    'body': json.dumps({'error': 'Could not extract email from S3 key'})
                }
        else:
            return {
                'statusCode': 400,
                'body': json.dumps({'error': 'Invalid event format'})
            }
        
        if not email or not profile_image_s3_key or not s3_bucket:
            print(f"[ERROR] Missing parameters: email={email}, s3_key={profile_image_s3_key}, bucket={s3_bucket}")
            return {
                'statusCode': 400,
                'body': json.dumps({
                    'error': 'Missing required parameters: email, profileImageS3Key, s3Bucket'
                })
            }
        
        # Index the face
        print(f"[INFO] Calling index_user_profile_image for {email}")
        result = index_user_profile_image(email, profile_image_s3_key, s3_bucket)
        print(f"[INFO] Indexing result: {result}")
        
        return {
            'statusCode': 200 if result['success'] else 400,
            'body': json.dumps(result)
        }
        
    except Exception as e:
        print(f"[ERROR] Lambda handler failed: {e}")
        print(traceback.format_exc())
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': str(e),
                'traceback': traceback.format_exc()
            })
        }


def extract_email_from_s3_key(s3_key):
    """
    Extract email from S3 key. Adjust this based on your S3 key naming convention.
    
    Examples:
    - profile-images/user@example.com.jpg -> user@example.com
    - users/123/profile.jpg -> lookup in User table by ClientId
    """
    try:
        from urllib.parse import unquote
        
        # URL decode the S3 key (handles %40 -> @, etc.)
        s3_key_decoded = unquote(s3_key)
        
        # Simple extraction: filename without extension
        filename = s3_key_decoded.split('/')[-1]
        email = filename.rsplit('.', 1)[0]
        
        print(f"[DEBUG] S3 key: {s3_key} -> decoded: {s3_key_decoded}")
        print(f"[DEBUG] Extracted filename: {filename}, email candidate: {email}")
        
        # Validate it looks like an email
        if '@' in email:
            print(f"[DEBUG] Email found in filename: {email}")
            return email
        
        # Otherwise, try to lookup in User table by ProfileImage S3 key
        print(f"[DEBUG] Looking up in User table for ProfileImage: {s3_key_decoded}")
        table = ddb.Table(USER_TABLE)
        response = table.scan()
        
        print(f"[DEBUG] Scan response: Count={response.get('Count', 0)}")
        
        # Filter in Python - check both encoded and decoded versions
        for item in response.get('Items', []):
            profile_image = item.get('ProfileImage', '')
            print(f"[DEBUG] Checking item: Email={item.get('Email')}, ProfileImage={profile_image}")
            if profile_image == s3_key or profile_image == s3_key_decoded:
                found_email = item['Email']
                print(f"[DEBUG] Found matching email: {found_email}")
                return found_email
        
        print(f"[DEBUG] No matching User found for ProfileImage: {s3_key}")
        return None
        
    except Exception as e:
        print(f"[ERROR] Failed to extract email from S3 key {s3_key}: {e}")
        print(traceback.format_exc())
        return None
