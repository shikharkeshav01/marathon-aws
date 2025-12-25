# processor.py
import os, json, boto3, traceback, mimetypes
from googleapiclient.discovery import build
from google.oauth2 import service_account
from bib_extraction import detect_and_tabulate_bibs_easyocr
from reel_generation import overlay_images_on_video
from boto3.dynamodb.conditions import Key, Attr
import uuid


# DynamoDB (schema: EventId (N) PK, DriveUrl (S), Status (S))
ddb = boto3.resource("dynamodb")
# jobs = ddb.Table(os.environ["JOBS_TABLE"])

# S3
s3 = boto3.client("s3")
RAW_BUCKET = os.environ["RAW_BUCKET"]

# Google Drive
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
creds = service_account.Credentials.from_service_account_file(
    os.environ["GDRIVE_SA_PATH"],
    scopes=SCOPES
)
drive = build("drive", "v3", credentials=creds)

def get_images_from_db(event_id, bib_id):
        table = ddb.Table(os.environ["EVENT_IMAGES_TABLE"])
        response = table.query(
            IndexName='EventId-index',
            KeyConditionExpression=Key('EventId').eq(event_id),
            FilterExpression=Attr('BibId').eq(str(bib_id))
        )
        return [item['filename'] for item in response.get('Items', [])]

def handler(event, context):
    """
    event = {
            "requestId": request_id,
            "eventId": event_id,
            "reelS3Key": reel_s3_key,
            "reelConfiguration": reel_configuration,
            "bibId": bib_id,
            "imageS3Keys": image_s3_keys
        }
    """

    
    print("Generating reel for bib_id", event.get("bibId"))
    event_id = event.get("eventId")
    reel_s3_key = event.get("reelS3Key")
    reel_config = event.get("reelConfiguration")
    bib_id = event.get("bibId")
    image_s3_key=event.get("imageS3Keys")
    overlays = json.loads(reel_config).get("overlays")

    # Download background video
    print("Downloading background video")
    local_video_path = os.path.join("/tmp", os.path.basename(reel_s3_key))
    try:
        s3.download_file(RAW_BUCKET, reel_s3_key, local_video_path)
    except Exception as e:
        print(f"Error downloading video: {e}")
        raise e


    local_image_paths = []
    # Download images
    if image_s3_key is None:

        filenames = get_images_from_db(event_id, bib_id)
        if len(filenames) < len(overlays):
            return {
                "eventId": event_id,
                "ok": False,
                "error": f"Not enough images found for bib_id{bib_id}"
            }
        print("Downloading images")
        for filename in filenames:
            local_image_path = os.path.join("/tmp", filename)
            image_s3_key = f"{event_id}/ProcessedImages/{filename}"
            try:
                s3.download_file(RAW_BUCKET, image_s3_key, local_image_path)
                local_image_paths.append(local_image_path)
            except Exception as e:
                print(f"Error downloading image {filename}: {e}")
                # Decide whether to fail hard or skip. Failing hard seems appropriate if we need these images.
                raise e
    
    else:
        if len(filenames) < len(overlays):
            return {
                "eventId": event_id,
                "ok": False,
                "error": f"Not enough images found for bib_id{bib_id}"
            }
        print("Downloading images")
        for filename in image_s3_key:
            local_image_path = os.path.join("/tmp", filename)
            image_s3_key = filename
            try:
                s3.download_file(RAW_BUCKET, image_s3_key, local_image_path)
                local_image_paths.append(local_image_path)
            except Exception as e:
                print(f"Error downloading image {filename}: {e}")
                # Decide whether to fail hard or skip. Failing hard seems appropriate if we need these images.
                raise e


    for i in range(len(overlays)):
        overlays[i]["image_path"] = local_image_paths[i]
    
    # output_path = os.path.join("/tmp", f"{event_id}/ProcessedReels/{bib_id}.mp4")
    output_path = os.path.join("/tmp", f"{bib_id}.mp4")

    print("Overlaying images on video")
    overlay_images_on_video(local_video_path, overlays, output_path)
    print("Uploading processed reel")
    s3.upload_file(output_path, RAW_BUCKET, f"{event_id}/ProcessedReels/{bib_id}.mp4")

    # Write to DynamoDB EventReel table
    try:
        event_reel_table = ddb.Table(os.environ["EVENT_REELS_TABLE"])
        event_reel_id = str(uuid.uuid4())
        reel_path = f"{event_id}/ProcessedReels/{bib_id}.mp4"
        
        event_reel_table.put_item(
            Item={
                'EventReelId': event_reel_id,
                'BibId': str(bib_id),
                'EventId': int(event_id),
                'ReelPath': reel_path
            }
        )
    except Exception as e:
        print(f"Error saving to DynamoDB EventReel: {e}")
        raise e

    return {
        "eventId": str(event_id),
        "ok": True
    }    
