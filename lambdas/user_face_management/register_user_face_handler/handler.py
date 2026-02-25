import json
import os
import boto3
import traceback
from datetime import datetime
from decimal import Decimal

# Initialize AWS clients
s3 = boto3.client("s3")
rekognition = boto3.client("rekognition")
ddb = boto3.resource("dynamodb")

# Environment variables
RAW_BUCKET = os.environ.get("RAW_BUCKET", "marathon-photos")
USER_FACES_TABLE = os.environ.get("USER_FACES_TABLE", "UserFaces")
USER_IMAGE_MATCHES_TABLE = os.environ.get("USER_IMAGE_MATCHES_TABLE", "UserImageMatches")
INDEXED_FACES_TABLE = os.environ.get("INDEXED_FACES_TABLE", "IndexedFaces")
USER_TABLE = os.environ.get("USER_TABLE", "User")

USERS_GLOBAL_COLLECTION = "users-global"
MARATHON_PARTICIPANTS_COLLECTION = os.environ.get("REKOGNITION_COLLECTION_ID", "marathon-participants")
FACE_MATCH_THRESHOLD = 80.0


def ensure_collection_exists(collection_id):
    """Create Rekognition collection if it doesn't exist."""
    try:
        rekognition.describe_collection(CollectionId=collection_id)
        print(f"[INFO] Collection {collection_id} already exists")
    except rekognition.exceptions.ResourceNotFoundException:
        print(f"[INFO] Creating collection {collection_id}")
        rekognition.create_collection(CollectionId=collection_id)


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


def delete_existing_faces_from_collection(email, collection_id):
    """
    Remove existing faces for a user from a specific Rekognition Collection.
    Called before indexing a new face to avoid duplicates.
    """
    try:
        # Encode email same way as when indexing
        external_id = email.replace('@', '__').replace('+', '_')

        response = rekognition.list_faces(
            CollectionId=collection_id,
            MaxResults=100
        )

        face_ids_to_delete = []
        for face in response.get('Faces', []):
            if face.get('ExternalImageId') == external_id:
                face_ids_to_delete.append(face['FaceId'])

        if face_ids_to_delete:
            rekognition.delete_faces(
                CollectionId=collection_id,
                FaceIds=face_ids_to_delete
            )
            print(f"[INFO] Deleted {len(face_ids_to_delete)} existing face(s) for {email} from {collection_id}")
        else:
            print(f"[INFO] No existing faces found for {email} in {collection_id}")

    except rekognition.exceptions.ResourceNotFoundException:
        print(f"[INFO] Collection {collection_id} does not exist yet, skipping deletion")
    except Exception as e:
        print(f"[WARN] Failed to delete existing faces for {email} from {collection_id}: {e}")


def get_existing_face_id(email):
    """Check if user already has a face indexed in users-global collection."""
    table = ddb.Table(USER_FACES_TABLE)

    # Query all records for this email
    response = table.query(
        KeyConditionExpression=boto3.dynamodb.conditions.Key('Email').eq(email)
    )

    items = response.get('Items', [])
    if items:
        # Return the first FaceId found (all should be the same for the same user)
        face_id = items[0].get('FaceId')
        if face_id:
            print(f"[INFO] Found existing FaceId for {email}: {face_id}")
            return face_id

    return None


def index_profile_photo(profile_s3_key, email, s3_bucket=None, image_bytes=None):
    """
    Index user's profile photo to both users-global and marathon-participants collections.
    Also updates User table with indexing status.
    """
    if s3_bucket is None:
        s3_bucket = RAW_BUCKET

    ensure_collection_exists(USERS_GLOBAL_COLLECTION)
    ensure_collection_exists(MARATHON_PARTICIPANTS_COLLECTION)

    # Get ClientId for User table update
    client_id = get_client_id_for_email(email)

    try:
        # Download image from S3 only if not already provided
        if image_bytes is None:
            local_path = f"/tmp/{email.replace('@', '_')}_profile.jpg"
            s3.download_file(s3_bucket, profile_s3_key, local_path)
            with open(local_path, 'rb') as image_file:
                image_bytes = image_file.read()

        # Delete existing faces from both collections before indexing new ones
        delete_existing_faces_from_collection(email, USERS_GLOBAL_COLLECTION)
        delete_existing_faces_from_collection(email, MARATHON_PARTICIPANTS_COLLECTION)

        # Index face to users-global collection (without ExternalImageId)
        response_global = rekognition.index_faces(
            CollectionId=USERS_GLOBAL_COLLECTION,
            Image={'Bytes': image_bytes},
            MaxFaces=1,
            QualityFilter='AUTO',
            DetectionAttributes=['DEFAULT']
        )

        face_records_global = response_global.get('FaceRecords', [])
        if not face_records_global:
            print(f"[WARN] No face detected in profile photo for {email}")
            if client_id:
                update_user_face_status(email, client_id, None, 'no_face_detected')
            raise Exception(f"No face detected in profile photo for {email}")

        face_id = face_records_global[0]['Face']['FaceId']
        confidence = face_records_global[0]['Face']['Confidence']
        print(f"[INFO] Indexed profile photo for {email} to {USERS_GLOBAL_COLLECTION}, FaceId: {face_id}, Confidence: {confidence:.2f}%")

        # Also index to marathon-participants collection with ExternalImageId
        # Rekognition ExternalImageId only allows [a-zA-Z0-9_.\-:]+
        external_id = email.replace('@', '__').replace('+', '_')

        response_marathon = rekognition.index_faces(
            CollectionId=MARATHON_PARTICIPANTS_COLLECTION,
            Image={'Bytes': image_bytes},
            ExternalImageId=external_id,
            MaxFaces=1,
            QualityFilter='AUTO',
            DetectionAttributes=['DEFAULT']
        )

        face_records_marathon = response_marathon.get('FaceRecords', [])
        if face_records_marathon:
            marathon_face_id = face_records_marathon[0]['Face']['FaceId']
            print(f"[INFO] Indexed profile photo for {email} to {MARATHON_PARTICIPANTS_COLLECTION}, FaceId: {marathon_face_id}")

        # Update User table with success status
        if client_id:
            update_user_face_status(email, client_id, face_id, 'indexed')

        return face_id

    except rekognition.exceptions.InvalidParameterException as e:
        error_msg = f"Invalid image for {email}: {str(e)}"
        print(f"[ERROR] {error_msg}")
        if client_id:
            update_user_face_status(email, client_id, None, 'invalid_image')
        raise Exception(error_msg)
    except rekognition.exceptions.ResourceNotFoundException as e:
        error_msg = f"Rekognition collection not found: {str(e)}"
        print(f"[ERROR] {error_msg}")
        if client_id:
            update_user_face_status(email, client_id, None, 'error')
        raise Exception(error_msg)
    except Exception as e:
        error_msg = f"Failed to index face: {str(e)}"
        print(f"[ERROR] {error_msg}")
        print(traceback.format_exc())
        if client_id:
            update_user_face_status(email, client_id, None, 'error')
        raise


def search_event_photos(image_bytes, event_id):
    """Search for matching faces in event-{eventId} collection using profile image bytes."""
    event_collection_id = f"event-{event_id}"

    # Check if event collection exists
    try:
        rekognition.describe_collection(CollectionId=event_collection_id)
    except rekognition.exceptions.ResourceNotFoundException:
        print(f"[WARN] Event collection {event_collection_id} does not exist yet")
        return []

    # Search by image so we're not constrained to a FaceId from the same collection
    response = rekognition.search_faces_by_image(
        CollectionId=event_collection_id,
        Image={'Bytes': image_bytes},
        FaceMatchThreshold=FACE_MATCH_THRESHOLD,
        MaxFaces=1000
    )

    matches = response.get('FaceMatches', [])
    print(f"[INFO] Found {len(matches)} face matches in {event_collection_id}")

    return matches


def store_user_face_record(email, event_id, phone, face_id, profile_s3_key, bib_number=None):
    """Store user registration in UserFaces table."""
    table = ddb.Table(USER_FACES_TABLE)
    
    item = {
        'Email': email,
        'EventId': int(event_id),
        'Phone': phone,
        'FaceId': face_id,
        'ProfileS3Key': profile_s3_key,
        'RegisteredAt': datetime.utcnow().isoformat()
    }
    
    if bib_number:
        item['BibNumber'] = str(bib_number)
    
    table.put_item(Item=item)
    print(f"[INFO] Stored UserFaces record for {email}, EventId: {event_id}")


def store_image_matches(email, event_id, face_id, matches):
    """Store face matches in UserImageMatches table."""
    table = ddb.Table(USER_IMAGE_MATCHES_TABLE)
    indexed_faces_table = ddb.Table(INDEXED_FACES_TABLE)
    
    for match in matches:
        matched_face_id = match['Face']['FaceId']
        similarity = match['Similarity']
        
        # Get image S3 key from IndexedFaces table
        indexed_face = indexed_faces_table.get_item(Key={'FaceId': matched_face_id})
        if 'Item' not in indexed_face:
            print(f"[WARN] Could not find IndexedFaces record for FaceId: {matched_face_id}")
            continue
        
        image_s3_key = indexed_face['Item']['ImageS3Key']
        
        # Store match
        table.put_item(
            Item={
                'Email': email,
                'ImageS3Key': image_s3_key,
                'EventId': int(event_id),
                'FaceId': face_id,
                'MatchedFaceId': matched_face_id,
                'Similarity': Decimal(str(similarity))
            }
        )
    
    print(f"[INFO] Stored {len(matches)} image matches for {email}")


def handler(event, context):
    """
    Register user face and search for matches in event photos.

    Input:
    {
        "email": "user@example.com",
        "phone": "+1234567890",
        "profilePhoto": "profiles/user123.jpg",  # S3 key
        "eventId": 1001,
        "bibNumber": "5432",  # optional
        "s3Bucket": "marathon-photos"  # optional, defaults to RAW_BUCKET
    }
    """
    print(json.dumps(event))

    # Handle AWS_PROXY integration: body is a JSON string inside event['body'].
    # Fall back to event itself for direct invocations (Step Functions, tests, etc.)
    raw_body = event.get('body')
    if raw_body:
        body = json.loads(raw_body) if isinstance(raw_body, str) else raw_body
    else:
        body = event

    try:
        email = body.get("email")
        phone = body.get("phone")
        profile_s3_key = body.get("profilePhoto")
        event_id = body.get("eventId")
        bib_number = body.get("bibNumber")
        s3_bucket = body.get("s3Bucket")

        if not all([email, phone, profile_s3_key, event_id]):
            raise ValueError("Missing required parameters: email, phone, profilePhoto, eventId")

        # Download profile image once — used for both indexing and searching
        bucket = s3_bucket or RAW_BUCKET
        local_path = f"/tmp/{email.replace('@', '_')}_profile.jpg"
        s3.download_file(bucket, profile_s3_key, local_path)
        with open(local_path, 'rb') as f:
            profile_image_bytes = f.read()

        # Check if user already has a face indexed
        face_id = get_existing_face_id(email)

        if not face_id:
            # Index profile photo for the first time
            # This will index to both users-global and marathon-participants collections
            # and update User table with indexing status
            print(f"[INFO] No existing face found for {email}, indexing profile photo...")
            face_id = index_profile_photo(profile_s3_key, email, s3_bucket, image_bytes=profile_image_bytes)
        else:
            print(f"[INFO] Reusing existing FaceId: {face_id}")

        # Search for matches in event photos using the profile image bytes directly,
        # so we are not constrained to a FaceId from the event collection
        matches = search_event_photos(profile_image_bytes, event_id)

        # Store image matches
        if matches:
            store_image_matches(email, event_id, face_id, matches)

        # Store user face record
        store_user_face_record(email, event_id, phone, face_id, profile_s3_key, bib_number)

        return {
            "statusCode": 200,
            "headers": {"Access-Control-Allow-Origin": "*"},
            "body": json.dumps({
                "message": "User registered successfully",
                "email": email,
                "eventId": int(event_id),
                "faceId": face_id,
                "matchesFound": len(matches)
            })
        }

    except ValueError as e:
        print(f"[ERROR] Validation error: {e}")
        return {
            "statusCode": 400,
            "headers": {"Access-Control-Allow-Origin": "*"},
            "body": json.dumps({
                "error": str(e),
                "email": body.get("email")
            })
        }
    except Exception as e:
        print(f"[ERROR] Failed to register user face: {e}")
        print(traceback.format_exc())
        return {
            "statusCode": 500,
            "headers": {"Access-Control-Allow-Origin": "*"},
            "body": json.dumps({
                "error": str(e),
                "email": body.get("email"),
                "traceback": traceback.format_exc()
            })
        }

