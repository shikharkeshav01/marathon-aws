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


def index_faces_in_image(bucket, s3_key, collection_id, filename):
    """Index all faces in an image to Rekognition collection using an S3 reference.

    Passing image bytes directly is limited to 5 MB by the Rekognition API.
    Using an S3 reference raises that limit to 15 MB and avoids the constraint.
    """
    try:
        response = rekognition.index_faces(
            CollectionId=collection_id,
            Image={'S3Object': {'Bucket': bucket, 'Name': s3_key}},
            MaxFaces=20,
            QualityFilter='NONE',
            DetectionAttributes=['ALL']
        )

        face_records = response.get('FaceRecords', [])
        print(f"[INFO] Indexed {len(face_records)} faces from {filename}")

        return face_records
    except Exception as e:
        print(f"[ERROR] Failed to index faces in {filename}: {e}")
        raise


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


def upload_to_s3(s3_key, data, content_type='image/jpeg'):
    """Upload image to S3."""
    print(f"[INFO] Uploading to S3: {s3_key} with ContentType: {content_type}")
    s3.put_object(
        Bucket=RAW_BUCKET,
        Key=s3_key,
        Body=data,
        ContentType=content_type
    )


def is_image_already_processed(event_id, filename):
    """Check if image has already been processed by looking for it in S3.

    Checks both IndexedImages (faces found) and UnProcessedImages (no faces).
    This ensures idempotency even if the Lambda is retried.
    """
    # Check if image was successfully indexed (faces found)
    indexed_key = f"{event_id}/IndexedImages/{filename}"
    try:
        s3.head_object(Bucket=RAW_BUCKET, Key=indexed_key)
        print(f"[INFO] Image already indexed in IndexedImages, skipping: {filename}")
        return True, indexed_key
    except s3.exceptions.ClientError:
        pass

    # Check if image was previously processed but had no faces
    unprocessed_key = f"{event_id}/UnProcessedImages/{filename}"
    try:
        s3.head_object(Bucket=RAW_BUCKET, Key=unprocessed_key)
        print(f"[INFO] Image already processed in UnProcessedImages, skipping: {filename}")
        return True, unprocessed_key
    except s3.exceptions.ClientError:
        pass

    return False, None


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
        # Download image from Google Drive to get filename
        filename, image_data, mime_type = download_image_from_drive(file_id)

        # Check if image has already been processed (idempotency check)
        already_processed, existing_s3_key = is_image_already_processed(event_id, filename)
        if already_processed:
            print(f"[SKIP] Image already processed: {filename}")
            return {
                'eventId': str(event_id),
                'filename': filename,
                'facesIndexed': 0,
                's3Key': existing_s3_key,
                'ok': True,
                'skipped': True
            }

        # Upload to S3 first so Rekognition can reference it via S3 (avoids the
        # 5 MB byte-payload limit; S3 reference supports images up to 15 MB).
        temp_s3_key = f"{event_id}/TempImages/{filename}"
        upload_to_s3(temp_s3_key, image_data, mime_type)

        # Index faces using the S3 reference
        face_records = index_faces_in_image(RAW_BUCKET, temp_s3_key, collection_id, filename)

        if face_records:
            final_s3_key = f"{event_id}/IndexedImages/{filename}"
            try:
                s3.copy_object(
                    Bucket=RAW_BUCKET,
                    CopySource={'Bucket': RAW_BUCKET, 'Key': temp_s3_key},
                    Key=final_s3_key
                )
            except s3.exceptions.ClientError as e:
                if e.response['Error']['Code'] == 'NoSuchKey':
                    print(f"[ERROR] Temp file not found: {temp_s3_key}. This may indicate a retry of an already-completed execution.")
                    raise ValueError(f"Temp file {temp_s3_key} does not exist. Image may have already been processed.")
                raise

            store_face_metadata(face_records, event_id, filename, final_s3_key)

            # Remove from UnProcessedImages if it was previously there
            unprocessed_key = f"{event_id}/UnProcessedImages/{filename}"
            try:
                s3.delete_object(Bucket=RAW_BUCKET, Key=unprocessed_key)
                print(f"[INFO] Removed {filename} from UnProcessedImages folder")
            except Exception as e:
                print(f"[WARN] Could not remove from UnProcessedImages: {e}")

            print(f"[SUCCESS] Indexed {len(face_records)} faces from {filename}")
        else:
            final_s3_key = f"{event_id}/UnProcessedImages/{filename}"
            try:
                s3.copy_object(
                    Bucket=RAW_BUCKET,
                    CopySource={'Bucket': RAW_BUCKET, 'Key': temp_s3_key},
                    Key=final_s3_key
                )
            except s3.exceptions.ClientError as e:
                if e.response['Error']['Code'] == 'NoSuchKey':
                    print(f"[ERROR] Temp file not found: {temp_s3_key}. This may indicate a retry of an already-completed execution.")
                    raise ValueError(f"Temp file {temp_s3_key} does not exist. Image may have already been processed.")
                raise
            print(f"[WARN] No faces detected in {filename}")

        # Remove the temporary object now that it has been copied to its final location
        try:
            s3.delete_object(Bucket=RAW_BUCKET, Key=temp_s3_key)
            print(f"[INFO] Deleted temp file: {temp_s3_key}")
        except Exception as e:
            print(f"[WARN] Could not delete temp file {temp_s3_key}: {e}")

        return {
            'eventId': str(event_id),
            'filename': filename,
            'facesIndexed': len(face_records),
            's3Key': final_s3_key,
            'ok': True
        }
        
    except Exception as e:
        print(f"[ERROR] Failed to process image: {e}")
        print(traceback.format_exc())
        raise

