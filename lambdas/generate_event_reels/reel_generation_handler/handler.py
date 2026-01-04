# processor.py
import os
# Set TMPDIR to /tmp to ensure ffmpeg and moviepy use writable directory in Lambda
os.environ['TMPDIR'] = '/tmp'
os.environ['TEMP'] = '/tmp'
os.environ['TMP'] = '/tmp'

import json, boto3, traceback, mimetypes
from reel_generation import overlay_images_on_video
from boto3.dynamodb.conditions import Key, Attr
import uuid


# DynamoDB (schema: EventId (N) PK, DriveUrl (S), Status (S))
ddb = boto3.resource("dynamodb")

# S3
s3 = boto3.client("s3")
RAW_BUCKET = os.environ["RAW_BUCKET"]


def get_images_from_db(event_id, bib_id):
        table = ddb.Table(os.environ["EVENT_IMAGES_TABLE"])
        response = table.query(
            IndexName='EventId-index',
            KeyConditionExpression=Key('EventId').eq(event_id),
            FilterExpression=Attr('BibId').eq(str(bib_id))
        )
        return [item['Filename'] for item in response.get('Items', [])]

def get_participants_from_db(event_id, bib_id):
    table = ddb.Table(os.environ["EVENT_PARTICIPANTS_TABLE"])
    response = table.query(
        KeyConditionExpression=Key('EventId').eq(event_id) & Key('BibId').eq(bib_id)
    )
    return response.get('Items', [])

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


    request_id = event.get("requestId")
    event_id = event.get("eventId")
    reel_s3_key = event.get("reelS3Key")
    reel_config = event.get("reelConfiguration")
    bib_id = event.get("bibId")
    image_s3_keys=event.get("imageS3Keys")

    participants = get_participants_from_db(event_id, bib_id)
    if len(participants) == 0:
        print(f"BibId {bib_id} not found in event {event_id}")
        return {
            "eventId": event_id,
            "ok": False,
            "error": f"BibId {bib_id} not found in event {event_id}"
        }
    
    print("Participants: ", participants)
    
    completion_time = participants[0].get("CompletionTime")
    participants_name=participants[0].get("ParticipantName")

    reel_config=reel_config.replace("${completionTime}", str(completion_time))
    reel_config=reel_config.replace("${participantsName}", participants_name)
    overlays = json.loads(reel_config).get("overlays")
    print("Reel config: ", reel_config)
    print("Generating reel for bib_id", event.get("bibId"))

    # Download background video
    print("Downloading background video")
    local_video_path = os.path.join("/tmp", os.path.basename(reel_s3_key))
    try:
        s3.download_file(RAW_BUCKET, reel_s3_key, local_video_path)
    except Exception as e:
        print(f"Error downloading video: {e}")
        raise e
    
    image_overlays=[overlay for overlay in overlays if overlay.get("type") == "image"]


    local_image_paths = []
    # Download images
    if image_s3_keys is None:

        filenames = get_images_from_db(event_id, bib_id)
        # if len(filenames) < len(image_overlays):
        #     return {
        #         "eventId": event_id,
        #         "ok": False,
        #         "error": f"Not enough images found for bib_id:{bib_id}"
        #     }
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
        # if len(image_s3_keys) < len(image_overlays):
        #     return {
        #         "eventId": event_id,
        #         "ok": False,
        #         "error": f"Not enough images found for bib_id:{bib_id}"
        #     }
        print("Downloading images")
        for filename in image_s3_keys:
            
            local_image_path = os.path.join("/tmp", filename.split('/')[-1])
            # local_image_path = "/tmp"
            image_s3_key = filename
            try:
                s3.download_file(RAW_BUCKET, image_s3_key, local_image_path)
                local_image_paths.append(local_image_path)
            except Exception as e:
                print(f"Error downloading image {filename}: {e}")
                # Decide whether to fail hard or skip. Failing hard seems appropriate if we need these images.
                raise e


    # for i in range(len(overlays)):
    #     overlays[i]["image_path"] = local_image_paths[i]

    for overlay in overlays:
        if len(local_image_paths) == 0:
            break
        if overlay.get("type") == "image":
            overlay["image_path"] = local_image_paths.pop(0)
    
    for overlay in overlays:
        if overlay.get("type") == "image":
            if "image_path" not in overlay:
                overlays.remove(overlay)
            
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
                'ReelId': event_reel_id,
                'BibId': str(bib_id),
                'EventId': int(event_id),
                'ReelPath': reel_path,
                'RequestId': request_id
            }
        )
    except Exception as e:
        print(f"Error saving to DynamoDB EventReel: {e}")
        raise e

    return {
        "eventId": str(event_id),
        "ok": True
    }    
