import os
import boto3
from typing import List, Dict

rekognition = boto3.client('rekognition')
ddb = boto3.resource('dynamodb')

COLLECTION_ID = os.environ.get('REKOGNITION_COLLECTION_ID', 'marathon-participants')
FACE_MATCH_THRESHOLD = float(os.environ.get('FACE_MATCH_THRESHOLD', '85.0'))
MAX_FACES = int(os.environ.get('MAX_FACES_TO_DETECT', '10'))


def detect_faces_in_image(image_bytes: bytes) -> List[Dict]:
    """
    Detect faces in an image using AWS Rekognition.
    
    Args:
        image_bytes: Raw image bytes
        
    Returns:
        List of face details with bounding boxes and confidence scores
    """
    try:
        response = rekognition.detect_faces(
            Image={'Bytes': image_bytes},
            Attributes=['DEFAULT']
        )
        
        faces = response.get('FaceDetails', [])
        print(f"[REKOGNITION] Detected {len(faces)} faces in image")
        
        for idx, face in enumerate(faces):
            bbox = face['BoundingBox']
            confidence = face['Confidence']
            print(f"  Face {idx+1}: confidence={confidence:.2f}%, bbox={bbox}")
        
        return faces
        
    except Exception as e:
        print(f"[ERROR] Failed to detect faces: {e}")
        return []


def match_faces_to_participants(image_bytes: bytes, event_id: int) -> List[Dict]:
    """
    Match faces in event image against Rekognition Collection and validate
    against event participants.
    
    Args:
        image_bytes: Raw image bytes
        event_id: Event ID to validate participants against
        
    Returns:
        List of matched participants with email, bib_id, and similarity score
    """
    try:
        response = rekognition.search_faces_by_image(
            CollectionId=COLLECTION_ID,
            Image={'Bytes': image_bytes},
            MaxFaces=MAX_FACES,
            FaceMatchThreshold=FACE_MATCH_THRESHOLD
        )

        # Log detected face in the search image
        searched_face = response.get('SearchedFace', {})
        if searched_face:
            searched_face_bbox = response.get('SearchedFaceBoundingBox', {})
            searched_face_confidence = response.get('SearchedFaceConfidence', 'N/A')
            print(f"[DETECTED_FACE] Face detected in image: BoundingBox={searched_face_bbox}, Confidence={searched_face_confidence}")

        matched_participants = []
        face_matches = response.get('FaceMatches', [])

        print(f"[REKOGNITION] Found {len(face_matches)} face matches above {FACE_MATCH_THRESHOLD}% threshold")

        if not face_matches:
            print("[INFO] No face matches found in collection")
            return []

        # Log all face matches with details
        print("[FACE_MATCHES] Details of all matched faces:")
        for idx, match_result in enumerate(face_matches, 1):
            face = match_result['Face']
            face_id = face.get('FaceId')
            external_id = face.get('ExternalImageId', 'N/A')
            similarity = match_result['Similarity']
            confidence = face.get('Confidence', 'N/A')
            print(f"  Face {idx}: FaceId={face_id}, ExternalImageId={external_id}, Similarity={similarity:.2f}%, Confidence={confidence}")

        participants_table = ddb.Table(os.environ['EVENT_PARTICIPANTS_TABLE'])

        for idx, match_result in enumerate(face_matches, 1):
            face = match_result['Face']
            face_id = face.get('FaceId')
            external_image_id = face.get('ExternalImageId')  # This is the escaped email
            similarity = match_result['Similarity']

            print(f"\n[PROCESSING] Face {idx}/{len(face_matches)}: FaceId={face_id}")

            if not external_image_id:
                print(f"[WARN] Face {face_id} has no ExternalImageId, skipping")
                continue

            # Unescape the email: undo the transformation from index_user_profile_image
            # Original: email.replace('@', '__').replace('+', '_')
            # Reverse: Replace __ with @
            # Note: We don't reverse _ to + because we can't distinguish between
            # original underscores and escaped plus signs. Emails with + are rare.
            actual_email = external_image_id.replace('__', '@')

            print(f"[MATCH] Face matched to external_id={external_image_id}, email={actual_email}, similarity={similarity:.2f}%")

            # Query participant by EventId and Email using GSI
            try:
                print(f"[QUERY] Querying EventId={event_id}, Email={actual_email} using EventId-Email-index")
                response = participants_table.query(
                    IndexName='EventId-Email-index',
                    KeyConditionExpression='EventId = :event_id AND Email = :email',
                    ExpressionAttributeValues={
                        ':event_id': int(event_id),
                        ':email': actual_email
                    }
                )

                items = response.get('Items', [])
                print(f"[QUERY_RESULT] Found {len(items)} participant(s) for email {actual_email}")

                if items:
                    participant = items[0]  # Should only be one match
                    bib_id = participant.get('BibId')
                    participant_name = participant.get('ParticipantName', 'N/A')

                    matched_participants.append({
                        'email': actual_email,
                        'bib_id': bib_id,
                        'similarity': similarity,
                        'client_id': participant.get('ClientId')
                    })
                    print(f"[SUCCESS] Participant validated: FaceId={face_id}, Email={actual_email}, BibId={bib_id}, Name={participant_name}, Similarity={similarity:.2f}%")
                else:
                    print(f"[WARN] Email {actual_email} not registered for EventId {event_id}")

            except Exception as e:
                print(f"[ERROR] Failed to query participant {actual_email}: {e}")
                import traceback
                print(f"[ERROR] Traceback: {traceback.format_exc()}")
                continue

        print(f"\n[SUMMARY] Total matched participants: {len(matched_participants)}")
        for idx, match in enumerate(matched_participants, 1):
            print(f"  Match {idx}: BibId={match['bib_id']}, Email={match['email']}, Similarity={match['similarity']:.2f}%")

        return matched_participants

    except rekognition.exceptions.InvalidParameterException as e:
        print(f"[INFO] No faces detected in image: {e}")
        return []
    except rekognition.exceptions.ResourceNotFoundException as e:
        print(f"[ERROR] Rekognition collection '{COLLECTION_ID}' not found: {e}")
        print(f"[INFO] Create collection with: aws rekognition create-collection --collection-id {COLLECTION_ID}")
        return []
    except Exception as e:
        print(f"[ERROR] Face matching failed: {e}")
        return []
