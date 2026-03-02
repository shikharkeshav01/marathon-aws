# start_job.py
import os, json, time, boto3
from datetime import datetime
from boto3.dynamodb.conditions import Key


# DynamoDB
ddb = boto3.resource("dynamodb")

# S3
s3 = boto3.client("s3")
RAW_BUCKET = os.environ.get("RAW_BUCKET", "marathon-photos")

def get_participants_for_event(event_id) -> list[dict]:
    participants_table = ddb.Table(os.environ["EVENT_PARTICIPANTS_TABLE"])

    participants = {}
    last_key = None

    while True:
        kwargs = {
            "KeyConditionExpression": Key("EventId").eq(int(event_id)),
        }
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key

        resp = participants_table.query(**kwargs)

        for item in resp.get("Items", []):
            bib = item.get("BibId")
            if bib:
                participants[str(bib)] = {"bibId": str(bib), "email": item.get("Email") or ""}

        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break

    return [participants[k] for k in sorted(participants)]






def main(event, context):

    """
    {
    "requestId": "dasd",
    "eventId": 5000,
    "reelConfiguration": "ds",
    "reelS3Key": "",
    "bibId": null,
    "imageS3Keys": [],
    "maxImageCount": 6
    }

    """
    print(json.dumps(event))

    request_id = event.get("requestId")
    event_id = event.get("eventId")
    reel_s3_key = event.get("reelS3Key")
    reel_configuration = event.get("reelConfiguration")
    bib_id = event.get("bibId")
    image_s3_keys = event.get("imageS3Keys")
    max_image_count = event.get("maxImageCount", 6)
    if not reel_s3_key:
        raise ValueError("Missing required field: reelS3Key")
    if not reel_configuration:
        raise ValueError("Missing required field: reelConfiguration")
    table=ddb.Table(os.environ["EVENT_REQUESTS_TABLE"])
    item = {
            "RequestId": request_id,
            "EventId": int(event_id),  # Partition Key
            "ReelS3Key": reel_s3_key,
            "ReelConfiguration": reel_configuration,
            "Status": "IN_PROGRESS",
            "RequestType": "GENERATE_EVENT_REELS",
            "CreatedAt": datetime.utcnow().isoformat()
        }

    if not bib_id:
        participants = get_participants_for_event(event_id)
    else:
        item["BibId"] = bib_id
        single = {"bibId": str(bib_id)}
        participants = [single]

    if not image_s3_keys:
        table.put_item(Item=item)
    else:
        item["ImageS3Keys"] = image_s3_keys
        table.put_item(Item=item)

    # Store participants in S3 to avoid Step Functions 256KB payload limit
    # The Step Function uses a Distributed Map that reads items directly from S3
    manifest_key = f"{event_id}/manifests/reels_{request_id}.json"
    s3.put_object(
        Bucket=RAW_BUCKET,
        Key=manifest_key,
        Body=json.dumps(participants),
        ContentType="application/json"
    )

    print(f"Stored manifest with {len(participants)} participants in s3://{RAW_BUCKET}/{manifest_key}")

    # Return only metadata — the Distributed Map reads items from S3 directly
    return {
            "requestId": request_id,
            "eventId": event_id,
            "reelS3Key": reel_s3_key,
            "reelConfiguration": reel_configuration,
            "manifestBucket": RAW_BUCKET,
            "manifestKey": manifest_key,
            "totalBibs": len(participants),
            "imageS3Keys": image_s3_keys,
            "maxImageCount": max_image_count
        }
        



