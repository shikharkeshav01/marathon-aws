import json
import os
import boto3
import traceback

# Initialize AWS clients
rekognition = boto3.client("rekognition")

# Environment variables
RAW_BUCKET = os.environ.get("RAW_BUCKET", "marathon-photos")


def detect_faces(bucket, s3_key):
    """
    Use Rekognition DetectFaces to find all faces in the image.
    Returns the list of FaceDetail objects.
    """
    response = rekognition.detect_faces(
        Image={
            "S3Object": {
                "Bucket": bucket,
                "Name": s3_key,
            }
        },
        Attributes=["DEFAULT"],
    )
    return response.get("FaceDetails", [])


def handler(event, context):
    """
    Validate that a profile image contains exactly one face.

    Input:
    {
        "s3Key": "profiles/user123.jpg",
        "bucket": "marathon-photos"   # optional, defaults to RAW_BUCKET
    }

    Output (success - exactly 1 face):
    {
        "statusCode": 200,
        "body": {
            "valid": true,
            "facesDetected": 1,
            "confidence": 99.8
        }
    }

    Output (error - 0 or >1 faces):
    {
        "statusCode": 400,
        "body": {
            "valid": false,
            "facesDetected": 0,
            "error": "No face detected in the image. Please upload a clear photo showing your face."
        }
    }
    """
    print(json.dumps(event))

    # Handle AWS_PROXY integration: body is a JSON string inside event['body'].
    # Fall back to event itself for direct invocations (Step Functions, tests, etc.)
    raw_body = event.get("body")
    if raw_body:
        body = json.loads(raw_body) if isinstance(raw_body, str) else raw_body
    else:
        body = event

    try:
        s3_key = body.get("s3Key")
        bucket = body.get("bucket") or RAW_BUCKET

        if not s3_key:
            return {
                "statusCode": 400,
                "headers": {"Access-Control-Allow-Origin": "*"},
                "body": json.dumps({"valid": False, "error": "Missing required parameter: s3Key"}),
            }

        print(f"[INFO] Validating profile image s3://{bucket}/{s3_key}")

        face_details = detect_faces(bucket, s3_key)
        faces_detected = len(face_details)

        print(f"[INFO] Detected {faces_detected} face(s) in s3://{bucket}/{s3_key}")

        if faces_detected == 0:
            return {
                "statusCode": 400,
                "headers": {"Access-Control-Allow-Origin": "*"},
                "body": json.dumps({
                    "valid": False,
                    "facesDetected": 0,
                    "error": "No face detected in the image. Please upload a clear photo showing your face.",
                }),
            }

        if faces_detected > 1:
            return {
                "statusCode": 400,
                "headers": {"Access-Control-Allow-Origin": "*"},
                "body": json.dumps({
                    "valid": False,
                    "facesDetected": faces_detected,
                    "error": f"Multiple faces detected ({faces_detected}). Profile image must contain exactly one face.",
                }),
            }

        # Exactly one face
        confidence = face_details[0].get("Confidence", 0)
        print(f"[INFO] Single face validated with confidence {confidence:.2f}%")

        return {
            "statusCode": 200,
            "headers": {"Access-Control-Allow-Origin": "*"},
            "body": json.dumps({
                "valid": True,
                "facesDetected": 1,
                "confidence": round(confidence, 2),
            }),
        }

    except rekognition.exceptions.InvalidParameterException as e:
        print(f"[ERROR] Invalid image parameter: {e}")
        return {
            "statusCode": 400,
            "headers": {"Access-Control-Allow-Origin": "*"},
            "body": json.dumps({"valid": False, "error": f"Invalid image: {str(e)}"}),
        }
    except rekognition.exceptions.InvalidS3ObjectException as e:
        print(f"[ERROR] S3 object not found or inaccessible: {e}")
        return {
            "statusCode": 400,
            "headers": {"Access-Control-Allow-Origin": "*"},
            "body": json.dumps({"valid": False, "error": f"Image not found in S3: {str(e)}"}),
        }
    except Exception as e:
        print(f"[ERROR] Failed to validate profile image: {e}")
        print(traceback.format_exc())
        return {
            "statusCode": 500,
            "headers": {"Access-Control-Allow-Origin": "*"},
            "body": json.dumps({"valid": False, "error": str(e)}),
        }

