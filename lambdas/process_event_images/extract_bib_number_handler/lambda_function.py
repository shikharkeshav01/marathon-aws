# processor.py
import os, json, boto3, traceback, mimetypes
from googleapiclient.discovery import build
from google.oauth2 import service_account
from bib_extraction import detect_and_extract_bibs, DetectionModel, OCRModel
from face_matching import match_faces_to_participants
import uuid

# Model configuration from environment variables
DETECTION_MODEL = DetectionModel(os.environ.get("DETECTION_MODEL", "yolov10n"))
OCR_MODEL = OCRModel(os.environ.get("OCR_MODEL", "easyocr"))

# DynamoDB (schema: EventId (N) PK, DriveUrl (S), Status (S))
ddb = boto3.resource("dynamodb")
# jobs = ddb.Table(os.environ["JOBS_TABLE"])

# S3
s3 = boto3.client("s3")
RAW_BUCKET = os.environ["RAW_BUCKET"]

# Google Drive
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
ssm = boto3.client("ssm")
ssm_param_name = os.environ.get("GDRIVE_SA_SSM_PARAM", "google-service-account")
sa_json_str = ssm.get_parameter(Name=ssm_param_name, WithDecryption=True)["Parameter"]["Value"]
sa_info = json.loads(sa_json_str)
creds = service_account.Credentials.from_service_account_info(
    sa_info,
    scopes=SCOPES
)
drive = build("drive", "v3", credentials=creds)


def extract_bib_numbers(photo, event_id, filename):
    try:
        bib_numbers = detect_and_extract_bibs(
            photo,
            image_name=filename,
            detection_model=DETECTION_MODEL,
            ocr_model=OCR_MODEL
        )
        participants_table = ddb.Table(os.environ["EVENT_PARTICIPANTS_TABLE"])
        validated_bibs = []
        for bib in bib_numbers:
            # Try exact match first, then variations (OCR sometimes adds extra digits)
            candidates = [
                bib,           # exact match
                bib[:-1],      # trim last digit (e.g., 211568 -> 21156)
                bib[1:],       # trim first digit
            ]
            # Filter out empty or too-short candidates
            candidates = [c for c in candidates if len(c) >= 2]

            matched_bib = None
            for candidate in candidates:
                response = participants_table.get_item(Key={"EventId": int(event_id), "BibId": str(candidate)})
                if "Item" in response:
                    matched_bib = candidate
                    break

            if matched_bib:
                validated_bibs.append(matched_bib)
                if matched_bib != bib:
                    print(f"[INFO] File: {filename}, OCR read '{bib}' but matched as '{matched_bib}'")
            else:
                print(f"[WARN] File: {filename}, Bib number {bib} not found in DynamoDB for EventId {event_id}")
        bib_numbers = validated_bibs
        
        # Fallback to face matching if no bib numbers found
        if not bib_numbers or len(bib_numbers) == 0:
            print(f"[FALLBACK] No bib numbers found in {filename}, attempting face matching...")
            matched_participants = match_faces_to_participants(photo, event_id)
            
            if matched_participants:
                # Extract bib IDs from matched participants
                face_matched_bibs = [p['bib_id'] for p in matched_participants if p.get('bib_id')]
                print(f"[SUCCESS] Face matching found {len(face_matched_bibs)} participants: {face_matched_bibs}")
                return face_matched_bibs, 'face'
            else:
                print(f"[INFO] No face matches found for {filename}")
                return [], 'none'
        
        return bib_numbers, 'bib'
        
    except Exception as exc:
        print("[ERROR] Failed to extract bib numbers:", exc)
        raise exc


def add_entry_to_db(event_id, filename, bib_numbers, match_type='bib'):
    """
    Insert a record into DynamoDB for each bib number found in an image.
    Schema:
      EventImageId (String, uuid4)
      BibId       (String)
      EventId     number
      Filename    (String)
      MatchType   (String) - 'bib' or 'face'
    """
    table_name = os.environ["EVENT_IMAGES_TABLE"]
    table = ddb.Table(table_name)
    print(f"Writing {len(bib_numbers)} items to table {table_name} for {filename} (match_type={match_type})")
    for bib_id in bib_numbers:
        event_image_id = str(uuid.uuid4())
        item = {
            "Id": event_image_id,
            "BibId": str(bib_id),
            "EventId": int(event_id),
            "Filename": filename,
            "MatchType": match_type
        }
        table.put_item(Item=item)


def download_file(file_id):
    print(f"Downloading file from Drive. ID: {file_id}")
    # 1) Get file metadata (name + mime type)
    metadata = drive.files().get(
        fileId=file_id,
        fields="name,mimeType"
    ).execute()

    mime_type = metadata["mimeType"]
    filename = metadata["name"]

    # 2) Download image from Google Drive
    data = drive.files().get_media(fileId=file_id).execute()
    print(f"Downloaded {filename}, size: {len(data)} bytes")
    return filename, data, mime_type


def upload_file(s3_key, data):
    print(f"Uploading to S3: Bucket={RAW_BUCKET}, Key={s3_key}")
    # 4) Upload to S3 with correct extension and content type
    s3.put_object(
        Bucket=RAW_BUCKET,
        Key=s3_key,
        Body=data,
        ContentType="image/jpeg"
    )


def file_already_processed(filename, event_id):
    """
    Check if the file already exists in the ProcessedImages directory in S3.
    Returns True if the file exists, False otherwise.
    """
    s3_key = f"{event_id}/ProcessedImages/{filename}"
    try:
        s3.head_object(Bucket=RAW_BUCKET, Key=s3_key)
        print(f"File {filename} already processed (found at {s3_key})")
        return True
    except s3.exceptions.ClientError as e:
        if e.response['Error']['Code'] == '404':
            print(f"File {filename} not yet processed")
            return False
        else:
            # Some other error occurred
            print(f"Error checking if file exists: {e}")
            raise


def lambda_handler(event, context):
    print(json.dumps(event))
    event_id = event.get("eventId")
    file_id = event.get("fileId")

    if event_id is None:
        raise ValueError("Missing eventId")
    if not file_id:
        raise ValueError("Missing fileId")

    # 1) Download image
    filename, data, mime_type = download_file(file_id)

    # 2) Check if file already processed, if yes, return
    if file_already_processed(filename, event_id):
        return {
            "eventId": str(event_id),
            "ok": True
        }

    # 3) Run your processing/model here if needed
    try:
        print("Starting bib extraction...")
        result = extract_bib_numbers(data, event_id, filename)
        
        # Handle tuple return (bib_numbers, match_type) or legacy list return
        if isinstance(result, tuple):
            bib_numbers, match_type = result
        else:
            bib_numbers = result
            match_type = 'bib'
        
        print(f"Match type: {match_type}, Bib numbers found: {bib_numbers}")

        if bib_numbers and len(bib_numbers) > 0:
            s3_key = f"{event_id}/ProcessedImages/{filename}"
        else:
            s3_key = f"{event_id}/UnProcessedImages/{filename}"

        # 4) Upload to S3
        upload_file(s3_key, data)

        add_entry_to_db(event_id, filename, bib_numbers, match_type)

    except Exception as exc:
        print(f"Exception encountered: {traceback.format_exc()}")
        s3_key = f"{event_id}/UnProcessedImages/{filename}"
        upload_file(s3_key, data)
        raise exc

    return {
        "eventId": str(event_id),
        "ok": True
    }
