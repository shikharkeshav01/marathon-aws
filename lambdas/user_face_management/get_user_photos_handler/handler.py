import json
import os
import boto3
from boto3.dynamodb.conditions import Key, Attr

# Initialize AWS clients
s3 = boto3.client("s3")
ddb = boto3.resource("dynamodb")

# Environment variables
RAW_BUCKET = os.environ.get("RAW_BUCKET", "marathon-photos")
USER_FACES_TABLE = os.environ.get("USER_FACES_TABLE", "UserFaces")
USER_IMAGE_MATCHES_TABLE = os.environ.get("USER_IMAGE_MATCHES_TABLE", "UserImageMatches")
EVENT_IMAGES_TABLE = os.environ.get("EVENT_IMAGES_TABLE", "EventImages")

# Presigned URL expiration (1 hour)
URL_EXPIRATION = 3600


def get_user_registration(email, event_id):
    """Get user registration record from UserFaces table."""
    table = ddb.Table(USER_FACES_TABLE)
    
    try:
        response = table.get_item(
            Key={
                'Email': email,
                'EventId': int(event_id)
            }
        )
        return response.get('Item')
    except Exception as e:
        print(f"[ERROR] Failed to get user registration: {e}")
        return None


def get_face_matched_photos(email, event_id):
    """Get photos matched by face from UserImageMatches table."""
    table = ddb.Table(USER_IMAGE_MATCHES_TABLE)
    
    try:
        # Query using EventId-Email-index GSI
        response = table.query(
            IndexName='EventId-Email-index',
            KeyConditionExpression=Key('EventId').eq(int(event_id)) & Key('Email').eq(email)
        )
        
        items = response.get('Items', [])
        print(f"[INFO] Found {len(items)} face-matched photos for {email}")
        
        # Extract S3 keys
        return [item['ImageS3Key'] for item in items]
    except Exception as e:
        print(f"[ERROR] Failed to get face-matched photos: {e}")
        return []


def get_bib_matched_photos(event_id, bib_number):
    """Get photos matched by bib number from EventImages table."""
    table = ddb.Table(EVENT_IMAGES_TABLE)
    
    try:
        # Query using EventId-index GSI with BibId filter
        response = table.query(
            IndexName='EventId-index',
            KeyConditionExpression=Key('EventId').eq(int(event_id)),
            FilterExpression=Attr('BibId').eq(str(bib_number))
        )
        
        items = response.get('Items', [])
        print(f"[INFO] Found {len(items)} bib-matched photos for bib {bib_number}")
        
        # Convert filenames to S3 keys
        s3_keys = [f"{event_id}/ProcessedImages/{item['Filename']}" for item in items]
        return s3_keys
    except Exception as e:
        print(f"[ERROR] Failed to get bib-matched photos: {e}")
        return []


def merge_and_deduplicate(face_photos, bib_photos):
    """Merge two lists of S3 keys and deduplicate, tracking source."""
    photo_sources = {}
    
    # Add face-matched photos
    for s3_key in face_photos:
        photo_sources[s3_key] = 'face'
    
    # Add bib-matched photos
    for s3_key in bib_photos:
        if s3_key in photo_sources:
            # Photo matched by both face and bib
            photo_sources[s3_key] = 'both'
        else:
            photo_sources[s3_key] = 'bib'
    
    return photo_sources


def generate_presigned_urls(photo_sources):
    """Generate presigned URLs for all photos."""
    results = []
    
    for s3_key, source in photo_sources.items():
        try:
            url = s3.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': RAW_BUCKET,
                    'Key': s3_key
                },
                ExpiresIn=URL_EXPIRATION
            )
            
            results.append({
                's3Key': s3_key,
                'url': url,
                'source': source
            })
        except Exception as e:
            print(f"[ERROR] Failed to generate presigned URL for {s3_key}: {e}")
    
    return results


def handler(event, context):
    """
    Get all photos for a user (merged face + bib matches).
    
    Input:
    {
        "email": "user@example.com",
        "eventId": 1001
    }
    
    Output:
    {
        "statusCode": 200,
        "body": {
            "email": "user@example.com",
            "eventId": 1001,
            "totalPhotos": 15,
            "photos": [
                {
                    "s3Key": "1001/IndexedImages/photo1.jpg",
                    "url": "https://...",
                    "source": "face"
                },
                {
                    "s3Key": "1001/ProcessedImages/photo2.jpg",
                    "url": "https://...",
                    "source": "bib"
                },
                {
                    "s3Key": "1001/ProcessedImages/photo3.jpg",
                    "url": "https://...",
                    "source": "both"
                }
            ]
        }
    }
    """
    print(json.dumps(event))
    
    email = event.get("email")
    event_id = event.get("eventId")
    
    if not all([email, event_id]):
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "Missing required parameters: email, eventId"})
        }
    
    # Get user registration to check for bib number
    user_registration = get_user_registration(email, event_id)
    
    if not user_registration:
        return {
            "statusCode": 404,
            "body": json.dumps({"error": f"User {email} not registered for event {event_id}"})
        }
    
    # Get face-matched photos (always)
    face_photos = get_face_matched_photos(email, event_id)
    
    # Get bib-matched photos (if user has a bib number)
    bib_photos = []
    bib_number = user_registration.get('BibNumber')
    if bib_number:
        print(f"[INFO] User has bib number: {bib_number}")
        bib_photos = get_bib_matched_photos(event_id, bib_number)
    else:
        print(f"[INFO] User has no bib number, skipping bib-matched photos")
    
    # Merge and deduplicate
    photo_sources = merge_and_deduplicate(face_photos, bib_photos)
    
    # Generate presigned URLs
    photos = generate_presigned_urls(photo_sources)
    
    return {
        "statusCode": 200,
        "body": json.dumps({
            "email": email,
            "eventId": int(event_id),
            "totalPhotos": len(photos),
            "photos": photos
        })
    }

