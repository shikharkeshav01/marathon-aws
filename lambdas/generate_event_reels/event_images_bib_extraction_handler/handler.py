# start_job.py
import os, json, time, boto3
from boto3.dynamodb.conditions import Key


# DynamoDB
ddb = boto3.resource("dynamodb")

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
    "imageS3Keys": []
    }

    """
    print(json.dumps(event))

    request_id = event.get("requestId")
    event_id = event.get("eventId")
    reel_s3_key = event.get("reelS3Key")
    reel_configuration = event.get("reelConfiguration")
    bib_id = event.get("bibId")
    image_s3_keys = event.get("imageS3Keys")
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
            "RequestType": "GENERATE_EVENT_REELS"
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
        
    return {
            "requestId": request_id,
            "eventId": event_id,
            "reelS3Key": reel_s3_key,
            "reelConfiguration": reel_configuration,
            "bibIds": bib_ids,
            "imageS3Keys": image_s3_keys
        }
        



