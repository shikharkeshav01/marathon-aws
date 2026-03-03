import os
import json
import boto3
import traceback
from googleapiclient.discovery import build
from google.oauth2 import service_account

# AWS clients
rekognition = boto3.client('rekognition')
s3 = boto3.client('s3')
ddb = boto3.resource('dynamodb')
ssm = boto3.client('ssm')

# Environment variables
RAW_BUCKET = os.environ['RAW_BUCKET']
INDEXED_FACES_TABLE = os.environ['INDEXED_FACES_TABLE']

# Google Drive setup
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
ssm_param_name = os.environ.get('GDRIVE_SA_SSM_PARAM', 'google-service-account')
sa_json_str = ssm.get_parameter(Name=ssm_param_name, WithDecryption=True)['Parameter']['Value']
sa_info = json.loads(sa_json_str)
creds = service_account.Credentials.from_service_account_info(sa_info, scopes=SCOPES)
drive = build('drive', 'v3', credentials=creds)


def download_image_from_drive(file_id):
    """Download image from Google Drive."""
    print(f"[INFO] Downloading file from Drive. ID: {file_id}")
    metadata = drive.files().get(fileId=file_id, fields='name,mimeType').execute()
    filename = metadata['name']
    mime_type = metadata['mimeType']
    data = drive.files().get_media(fileId=file_id).execute()
    print(f"[INFO] Downloaded {filename}, size: {len(data)} bytes")
    return filename, data, mime_type


def index_faces_in_image(image_bytes, collection_id, event_id, filename):
    """Index all faces in an image to Rekognition collection."""
    try:
        response = rekognition.index_faces(
            CollectionId=collection_id,
            Image={'Bytes': image_bytes},
            MaxFaces=10,
            QualityFilter='NONE',
            DetectionAttributes=['ALL']
        )
        
        face_records = response.get('FaceRecords', [])
        print(f"[INFO] Indexed {len(face_records)} faces from {filename}")
        
        return face_records
    except Exception as e:
        print(f"[ERROR] Failed to index faces in {filename}: {e}")
        return []


def store_face_metadata(face_records, event_id, filename, s3_key):
    """Store face metadata in IndexedFaces DynamoDB table."""
    table = ddb.Table(INDEXED_FACES_TABLE)
    
    for face_record in face_records:
        face_id = face_record['Face']['FaceId']
        confidence = face_record['Face']['Confidence']
        
        item = {
            'FaceId': face_id,
            'EventId': int(event_id),
            'ImageS3Key': s3_key,
            'Filename': filename,
            'Confidence': str(confidence)
        }
        
        table.put_item(Item=item)
        print(f"[INFO] Stored face {face_id} in IndexedFaces table")


def upload_to_s3(s3_key, data):
    """Upload image to S3."""
    print(f"[INFO] Uploading to S3: {s3_key}")
    s3.put_object(
        Bucket=RAW_BUCKET,
        Key=s3_key,
        Body=data,
        ContentType='image/jpeg'
    )


def handler(event, context):
    """
    Lambda handler for indexing faces from event photos.
    
    Expected event:
    {
        "eventId": 123,
        "fileId": "google-drive-file-id"
    }
    """
    print(json.dumps(event))
    
    event_id = event.get('eventId')
    file_id = event.get('fileId')
    
    if not event_id:
        raise ValueError('Missing eventId')
    if not file_id:
        raise ValueError('Missing fileId')
    
    collection_id = f"event-{event_id}"
    
    try:
        # Download image from Google Drive
        filename, image_data, mime_type = download_image_from_drive(file_id)
        
        # Index faces in the image
        face_records = index_faces_in_image(image_data, collection_id, event_id, filename)
        
        if face_records:
            # Upload to S3 in IndexedImages folder
            s3_key = f"{event_id}/IndexedImages/{filename}"
            upload_to_s3(s3_key, image_data)
            
            # Store face metadata in DynamoDB
            store_face_metadata(face_records, event_id, filename, s3_key)
            
            print(f"[SUCCESS] Indexed {len(face_records)} faces from {filename}")
        else:
            # No faces found - upload to UnProcessedImages
            s3_key = f"{event_id}/UnProcessedImages/{filename}"
            upload_to_s3(s3_key, image_data)
            print(f"[WARN] No faces detected in {filename}")
        
        return {
            'eventId': str(event_id),
            'filename': filename,
            'facesIndexed': len(face_records),
            'ok': True
        }
        
    except Exception as e:
        print(f"[ERROR] Failed to process image: {e}")
        print(traceback.format_exc())
        raise

