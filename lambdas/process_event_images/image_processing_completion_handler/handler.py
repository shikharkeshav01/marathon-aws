import boto3, os
from typing import Any, Dict, List

ddb = boto3.resource("dynamodb")
table = ddb.Table(os.environ["EVENT_REQUESTS_TABLE"])

def main(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    {
        "requestId": null,
        "eventId": 2000,
        "processedCount": 0
    }
    """
    request_id=event.get("requestId")


    table.update_item(
        Key={"RequestId": request_id},
        UpdateExpression="SET #status = :status",
        ExpressionAttributeNames={"#status": "Status"},
        ExpressionAttributeValues={
            ":status": "SUCCESS",
        },
    )
    

    return {
        "requestId": event.get("requestId"),
        "eventId": event.get("eventId"),
    }

