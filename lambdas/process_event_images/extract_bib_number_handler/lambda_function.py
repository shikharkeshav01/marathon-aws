# processor.py
import os, json, boto3, traceback, mimetypes
from googleapiclient.discovery import build
from google.oauth2 import service_account
from bib_extraction import detect_and_tabulate_bibs_easyocr
import uuid

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


def extract_bib_numbers(photo):
    try:
        bib_numbers = detect_and_tabulate_bibs_easyocr(photo, image_name="s3_object")
    except Exception as exc:
        print("[ERROR] Failed to extract bib numbers:", exc)
        bib_numbers = []
    return bib_numbers



def add_entry_to_db(event_id, filename, bib_numbers):
    """
    Insert a record into DynamoDB for each bib number found in an image.
    Schema:
      EventImageId (String, uuid4)
      BibId       (String)
      EventId     (String or Number)
      Filename    (String)
    """
    table_name = os.environ["BIB_IMAGES_TABLE"]
    table = ddb.Table(table_name)
    print(f"Writing {len(bib_numbers)} items to table {table_name} for {filename}")
    for bib_id in bib_numbers:
        event_image_id = str(uuid.uuid4())
        item = {
            "EventImageId": event_image_id,
            "BibId": str(bib_id),
            "EventId": str(event_id),
            "Filename": filename
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

    # 3) Determine file extension
    ext = os.path.splitext(filename)[1]
    if not ext:
        ext = mimetypes.guess_extension(mime_type) or ""

    # 5) Run your processing/model here if needed
    try:
        print("Starting bib extraction...")
        bib_numbers = extract_bib_numbers(data)
        print(f"Bib numbers found: {bib_numbers}")
        
        if bib_numbers or len(bib_numbers) > 0:
            s3_key = f"{event_id}/ProcessedImages/{filename}"
        else:
            s3_key = f"{event_id}/UnProcessedImages/{filename}"

        # 4) Upload to S3
        upload_file(s3_key, data)
        
        add_entry_to_db(event_id, filename, bib_numbers)
    
    except Exception:
        print(f"Exception encountered: {traceback.format_exc()}")
        s3_key = f"{event_id}/UnProcessedImages/{filename}"
        upload_file(s3_key, data)


    return {
        "eventId": str(event_id),
        # "fileId": str(file_id),
        # "s3Bucket": RAW_BUCKET,
        # "s3Key": s3_key,
        "ok": True
    }
    # try:
    #     return generateBibIds(event_id, file_id)
    # except Exception as e:
    #     print(f"Error: {e}")
    #     return {
    #         "eventId": event_id,
            
    #         "ok": False,
    #         "error": str(e)
    #     }

    
