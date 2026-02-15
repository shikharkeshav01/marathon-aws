import json
import os
import boto3
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

USERS_GLOBAL_COLLECTION = "users-global"
FACE_MATCH_THRESHOLD = 80.0


def ensure_collection_exists(collection_id):
    """Create Rekognition collection if it doesn't exist."""
    try:
        rekognition.describe_collection(CollectionId=collection_id)
        print(f"[INFO] Collection {collection_id} already exists")
    except rekognition.exceptions.ResourceNotFoundException:
        print(f"[INFO] Creating collection {collection_id}")
        rekognition.create_collection(CollectionId=collection_id)


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


def index_profile_photo(profile_s3_key, email):
    """Index user's profile photo to users-global collection."""
    ensure_collection_exists(USERS_GLOBAL_COLLECTION)
    
    # Download image from S3
    local_path = f"/tmp/{email.replace('@', '_')}_profile.jpg"
    s3.download_file(RAW_BUCKET, profile_s3_key, local_path)
    
    with open(local_path, 'rb') as image_file:
        image_bytes = image_file.read()
    
    # Index face to users-global collection
    response = rekognition.index_faces(
        CollectionId=USERS_GLOBAL_COLLECTION,
        Image={'Bytes': image_bytes},
        MaxFaces=1,
        QualityFilter='AUTO',
        DetectionAttributes=['DEFAULT']
    )
    
    face_records = response.get('FaceRecords', [])
    if not face_records:
        raise Exception(f"No face detected in profile photo for {email}")
    
    face_id = face_records[0]['Face']['FaceId']
    print(f"[INFO] Indexed profile photo for {email}, FaceId: {face_id}")
    
    return face_id


def search_event_photos(face_id, event_id):
    """Search for matching faces in event-{eventId} collection."""
    event_collection_id = f"event-{event_id}"
    
    # Check if event collection exists
    try:
        rekognition.describe_collection(CollectionId=event_collection_id)
    except rekognition.exceptions.ResourceNotFoundException:
        print(f"[WARN] Event collection {event_collection_id} does not exist yet")
        return []
    
    # Search for matching faces
    response = rekognition.search_faces(
        CollectionId=event_collection_id,
        FaceId=face_id,
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
        "bibNumber": "5432"  # optional
    }
    """
    print(json.dumps(event))
    
    email = event.get("email")
    phone = event.get("phone")
    profile_s3_key = event.get("profilePhoto")
    event_id = event.get("eventId")
    bib_number = event.get("bibNumber")
    
    if not all([email, phone, profile_s3_key, event_id]):
        raise ValueError("Missing required parameters: email, phone, profilePhoto, eventId")
    
    # Check if user already has a face indexed
    face_id = get_existing_face_id(email)
    
    if not face_id:
        # Index profile photo for the first time
        face_id = index_profile_photo(profile_s3_key, email)
    else:
        print(f"[INFO] Reusing existing FaceId: {face_id}")
    
    # Search for matches in event photos
    matches = search_event_photos(face_id, event_id)
    
    # Store image matches
    if matches:
        store_image_matches(email, event_id, face_id, matches)
    
    # Store user face record
    store_user_face_record(email, event_id, phone, face_id, profile_s3_key, bib_number)
    
    return {
        "statusCode": 200,
        "body": json.dumps({
            "message": "User registered successfully",
            "email": email,
            "eventId": int(event_id),
            "faceId": face_id,
            "matchesFound": len(matches)
        })
    }

