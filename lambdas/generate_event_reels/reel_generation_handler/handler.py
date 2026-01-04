# processor.py
import os, json, boto3, traceback, mimetypes
from reel_generation import overlay_images_on_video
from boto3.dynamodb.conditions import Key, Attr
import uuid


# DynamoDB (schema: EventId (N) PK, DriveUrl (S), Status (S))
ddb = boto3.resource("dynamodb")

# S3
s3 = boto3.client("s3")
RAW_BUCKET = os.environ.get("RAW_BUCKET", "")


def generate_reel_local(
    video_path: str,
    image_paths: list[str],
    reel_config_json: str,
    output_path: str,
    template_vars: dict[str, str] | None = None
) -> str:
    """
    Core reel generation logic that works with local files only.

    Args:
        video_path: Path to the background video file (local)
        image_paths: List of paths to image files (local)
        reel_config_json: JSON string with reel configuration (overlays array)
        output_path: Where to save the generated video (local)
        template_vars: Optional dict of template variable substitutions
                      e.g., {"completionTime": "02:45:30", "participantsName": "John Doe"}

    Returns:
        Path to the generated video file

    Example:
        generate_reel_local(
            video_path="/path/to/background.mp4",
            image_paths=["/path/to/img1.jpg", "/path/to/img2.jpg"],
            reel_config_json='{"overlays": [...]}',
            output_path="/path/to/output.mp4",
            template_vars={"participantsName": "John Doe", "completionTime": "02:30:15"}
        )
    """
    # Apply template variable substitutions if provided
    config_str = reel_config_json
    if template_vars:
        for key, value in template_vars.items():
            config_str = config_str.replace(f"${{{key}}}", str(value))

    # Parse config
    overlays = json.loads(config_str).get("overlays", [])

    # Create a copy of image_paths to avoid modifying the input list
    available_images = image_paths.copy()

    # Assign image paths to image overlays, and filter out overlays without images
    overlays_with_images = []
    for overlay in overlays:
        overlay_type = overlay.get("type", "image")

        if overlay_type == "image_stack":
            # Assign ALL available images to this overlay
            if available_images:
                overlay["image_paths"] = available_images.copy()
                overlays_with_images.append(overlay)
                # Clear available images since they're all used
                available_images = []
            else:
                print(f"Warning: Skipping image_stack overlay (no images available)")
        elif overlay_type == "image":
            if available_images:
                # Assign image to this overlay
                overlay["image_path"] = available_images.pop(0)
                overlays_with_images.append(overlay)
            else:
                # No more images available, skip this image overlay
                print(f"Warning: Skipping image overlay (no more images available)")
        else:
            # Non-image overlay (e.g., text), always include
            overlays_with_images.append(overlay)

    # Generate the reel
    print(f"Generating reel with {len(overlays_with_images)} overlays: {output_path}")
    overlay_images_on_video(video_path, overlays_with_images, output_path)

    return output_path


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
    AWS Lambda handler for generating reels.

    event = {
            "requestId": request_id,
            "eventId": event_id,
            "reelS3Key": reel_s3_key,
            "reelConfiguration": reel_configuration,
            "bibId": bib_id,
            "imageS3Keys": image_s3_keys
        }
    """
    event_id = event.get("eventId")
    request_id = event.get("requestId")
    reel_s3_key = event.get("reelS3Key")
    reel_config = event.get("reelConfiguration")
    bib_id = event.get("bibId")
    image_s3_keys = event.get("imageS3Keys")

    # Get participant data for template variable substitution
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
    participants_name = participants[0].get("ParticipantName")

    # Download background video from S3
    print("Downloading background video")
    local_video_path = os.path.join("/tmp", os.path.basename(reel_s3_key))
    try:
        s3.download_file(RAW_BUCKET, reel_s3_key, local_video_path)
    except Exception as e:
        print(f"Error downloading video: {e}")
        raise e

    # Download images from S3
    local_image_paths = []
    if image_s3_keys is None:
        # Get images from database
        filenames = get_images_from_db(event_id, bib_id)
        print(f"Downloading {len(filenames)} images from database")
        for filename in filenames:
            local_image_path = os.path.join("/tmp", filename)
            image_s3_key = f"{event_id}/ProcessedImages/{filename}"
            try:
                s3.download_file(RAW_BUCKET, image_s3_key, local_image_path)
                local_image_paths.append(local_image_path)
            except Exception as e:
                print(f"Error downloading image {filename}: {e}")
                raise e
    else:
        # Use provided S3 keys
        print(f"Downloading {len(image_s3_keys)} images from provided S3 keys")
        for s3_key in image_s3_keys:
            local_image_path = os.path.join("/tmp", s3_key.split('/')[-1])
            try:
                s3.download_file(RAW_BUCKET, s3_key, local_image_path)
                local_image_paths.append(local_image_path)
            except Exception as e:
                print(f"Error downloading image {s3_key}: {e}")
                raise e

    # Generate reel using core logic
    output_path = os.path.join("/tmp", f"{bib_id}.mp4")

    try:
        generate_reel_local(
            video_path=local_video_path,
            image_paths=local_image_paths,
            reel_config_json=reel_config,
            output_path=output_path,
            template_vars={
                "completionTime": str(completion_time),
                "participantsName": participants_name
            }
        )
    except ValueError as e:
        print(f"Error generating reel: {e}")
        return {
            "eventId": event_id,
            "ok": False,
            "error": str(e)
        }

    # Upload processed reel to S3
    print("Uploading processed reel")

    event_reel_id = str(uuid.uuid4())
    s3_output_key = f"{event_id}/ProcessedReels/{bib_id}_{event_reel_id}.mp4"
    s3.upload_file(output_path, RAW_BUCKET, s3_output_key)

    # Write to DynamoDB EventReel table
    try:
        event_reel_table = ddb.Table(os.environ["EVENT_REELS_TABLE"])
        event_reel_table.put_item(
            Item={
                'ReelId': event_reel_id,
                'BibId': str(bib_id),
                'EventId': int(event_id),
                'ReelPath': s3_output_key,
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
