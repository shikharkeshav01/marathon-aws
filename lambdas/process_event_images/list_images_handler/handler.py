# start_job.py
import boto3
import csv
import json
import os
from datetime import datetime
from decimal import Decimal, InvalidOperation

from google.oauth2 import service_account
from googleapiclient.discovery import build


def normalize_drive_id(raw: str) -> str:
    # strip querystring/fragments
    raw = raw.split("?")[0].split("#")[0]

    # handle common URL formats
    if "/folders/" in raw:
        return raw.split("/folders/")[1].split("/")[0]
    if "drive.google.com/open" in raw and "id=" in raw:
        # .../open?id=FOLDER_ID
        return raw.split("id=")[1].split("&")[0]
    if "/file/d/" in raw:
        # if user accidentally passes a file link
        return raw.split("/file/d/")[1].split("/")[0]

    # fallback: last path segment
    return raw.rstrip("/").split("/")[-1]


# DynamoDB
ddb = boto3.resource("dynamodb")
table = ddb.Table(os.environ["EVENT_REQUESTS_TABLE"])  # keep env var name as-is
participants_table = ddb.Table(os.environ.get("EVENT_PARTICIPANTS_TABLE", "EventParticipants"))
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

def handler(event, context):
    print(json.dumps(event))
    # Extract variables from the event
    request_id = event.get("requestId")
    event_id = event.get("eventId")
    gdrive_folder_url = event.get("gdriveFolderUrl")
    csv_key = event.get("csvKey")
    processing_mode = event.get("processingMode", "bib")  # Default to "bib" for backward compatibility
    folder_id = normalize_drive_id(gdrive_folder_url)

    # Save minimal record in DynamoDB
    item = {
        "RequestId": request_id,  # Partition Key
        "DriveUrl": str(gdrive_folder_url),
        "EventId": int(event_id),
        "Status": "IN_PROGRESS",
        "RequestType": "PROCESS_EVENT_IMAGES",
        "ProcessingMode": processing_mode,
        "CreatedAt": datetime.utcnow().isoformat()
    }
    if csv_key:
        item["CsvKey"] = csv_key

    table.put_item(Item=item)

    # Only process CSV for bib mode
    if processing_mode == "bib" and csv_key:
        local_csv_path = "/tmp/participants.csv"
        s3.download_file(RAW_BUCKET, csv_key, local_csv_path)

        # Read CSV file with built-in csv module and store participants in DynamoDB
        with open(local_csv_path, 'r', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)

            # Store each participant in EventParticipants table
            for row in reader:
                try:
                    completion_time = row.get("Completion Time", "").strip()
                    participants_table.put_item(
                        Item={
                            "EventId": int(event_id),  # Partition Key
                            "BibId": str(row["Bib No"]),  # Sort Key
                            "ParticipantName": str(row["Participant Name"]),
                            "TicketName": str(row["Ticket Name"]),
                            "Phone": str(row["Phone"]),
                            "Email": str(row["Email"]),
                            "CompletionTime": completion_time
                        }
                    )
                except Exception as e:
                    print(f"Error storing participant {row.get('Bib No', 'unknown')}: {e}")
                    raise e
    elif processing_mode == "face":
        print(f"[INFO] Face mode - skipping CSV processing")
    else:
        print(f"[WARN] Unknown processing mode: {processing_mode}, defaulting to bib")

    # List images in the folder and return URLs for Step Functions Map
    image_items = []
    page_token = None

    while True:
        res = drive.files().list(
            q=f"'{folder_id}' in parents and mimeType contains 'image/' and trashed=false",
            fields="nextPageToken, files(id)",
            pageToken=page_token
        ).execute()       

        for f in res.get("files", []):
            fid = f["id"]
            image_items.append({
                "fileId": fid
            })

        page_token = res.get("nextPageToken")
        if not page_token:
            break

    # Split items into chunks of 500 to avoid Step Functions 256KB limit
    CHUNK_SIZE = 500
    chunks = []
    for i in range(0, len(image_items), CHUNK_SIZE):
        chunk = image_items[i:i + CHUNK_SIZE]
        chunks.append(chunk)

    # Output is shaped for nested Map states (ItemsPath -> $.chunks)
    return {
        "requestId": request_id,
        "eventId": int(event_id),
        "driveFolderUrl": str(gdrive_folder_url),
        "processingMode": processing_mode,
        "chunks": chunks,
        "totalImages": len(image_items),
        "totalChunks": len(chunks)
    }


