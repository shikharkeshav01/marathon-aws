# start_job.py
import os, json, time, boto3
from googleapiclient.discovery import build
from google.oauth2 import service_account
from boto3.dynamodb.conditions import Key

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


def file_view_url(file_id: str) -> str:
    # Good “URL” for humans and for systems that just want an identifier URL
    return f"https://drive.google.com/file/d/{file_id}/view"


# DynamoDB
ddb = boto3.resource("dynamodb")
table = ddb.Table(os.environ["EVENT_REQUESTS_TABLE"])  # keep env var name as-is

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
    folder_id = normalize_drive_id(gdrive_folder_url)

    # Save minimal record in DynamoDB
    table.put_item(
        Item={
            "RequestId": request_id,  # Partition Key
            "DriveUrl": str(gdrive_folder_url),
            "EventId": int(event_id),
            "Status": "IN_PROGRESS",
            "RequestType": "PROCESS_EVENT_IMAGES"
        }
    )

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
                "fileId": fid,
                "imageUrl": file_view_url(fid)
            })

        page_token = res.get("nextPageToken")
        if not page_token:
            break

    # Output is shaped for a Step Functions Map state (ItemsPath -> $.items)
    return {
        "requestId": request_id,
        "eventId": int(event_id),
        "driveFolderUrl": str(gdrive_folder_url),
        "items": image_items,
        "total": len(image_items)
    }


