# start_job.py
import os, json, time, boto3
from datetime import datetime
from boto3.dynamodb.conditions import Key


# DynamoDB
ddb = boto3.resource("dynamodb")

# S3
s3 = boto3.client("s3")
RAW_BUCKET = os.environ.get("RAW_BUCKET", "marathon-photos")

def get_bib_ids_for_event(event_id: str) -> list[str]:
    bib_table = ddb.Table(os.environ["EVENT_IMAGES_TABLE"])

    bib_ids = set()
    last_key = None


    while True:
        kwargs = {
            "IndexName": "EventId-index",
            "KeyConditionExpression": Key("EventId").eq(event_id),
            # "ProjectionExpression": "BibId",
        }
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key

        resp = bib_table.query(**kwargs)

        for item in resp.get("Items", []):
            bib = item.get("BibId")
            if bib:
                bib_ids.add(str(bib))

        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break

    return sorted(bib_ids)






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
        bib_ids=get_bib_ids_for_event(event_id)
    else:
        item["BibId"] = bib_id
        bib_ids=[bib_id]

    if not image_s3_keys:
        table.put_item(
        Item=item
    )
    else:
        item["ImageS3Keys"] = image_s3_keys
        table.put_item(
        Item=item
        )

    # Store bib IDs in S3 to avoid Step Functions 256KB payload limit
    # The Step Function uses a Distributed Map that reads items directly from S3
    items = [{"bibId": bib_id} for bib_id in bib_ids]
    manifest_key = f"{event_id}/manifests/reels_{request_id}.json"
    s3.put_object(
        Bucket=RAW_BUCKET,
        Key=manifest_key,
        Body=json.dumps(items),
        ContentType="application/json"
    )

    print(f"Stored manifest with {len(bib_ids)} bib IDs in s3://{RAW_BUCKET}/{manifest_key}")

    # Return only metadata — the Distributed Map reads items from S3 directly
    return {
            "requestId": request_id,
            "eventId": event_id,
            "reelS3Key": reel_s3_key,
            "reelConfiguration": reel_configuration,
            "manifestBucket": RAW_BUCKET,
            "manifestKey": manifest_key,
            "totalBibs": len(bib_ids),
            "imageS3Keys": image_s3_keys,
            "maxImageCount": max_image_count
        }
        



