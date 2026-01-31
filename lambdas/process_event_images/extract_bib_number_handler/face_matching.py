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
        
        matched_participants = []
        face_matches = response.get('FaceMatches', [])
        
        print(f"[REKOGNITION] Found {len(face_matches)} face matches above {FACE_MATCH_THRESHOLD}% threshold")
        
        if not face_matches:
            print("[INFO] No face matches found in collection")
            return []
        
        participants_table = ddb.Table(os.environ['EVENT_PARTICIPANTS_TABLE'])
        
        for match_result in face_matches:
            face = match_result['Face']
            external_image_id = face.get('ExternalImageId')  # This is the email
            similarity = match_result['Similarity']
            
            if not external_image_id:
                print(f"[WARN] Face {face.get('FaceId')} has no ExternalImageId, skipping")
                continue
            
            print(f"[MATCH] Face matched to email={external_image_id}, similarity={similarity:.2f}%")
            
            # Verify this email is registered for this event
            try:
                response = participants_table.get_item(
                    Key={
                        'EventId': int(event_id),
                        'Email': external_image_id
                    }
                )
                
                if 'Item' in response:
                    participant = response['Item']
                    bib_id = participant.get('BibId')
                    
                    matched_participants.append({
                        'email': external_image_id,
                        'bib_id': bib_id,
                        'similarity': similarity,
                        'client_id': participant.get('ClientId')
                    })
                    print(f"[SUCCESS] Participant validated: email={external_image_id}, bib={bib_id}")
                else:
                    print(f"[WARN] Email {external_image_id} not registered for EventId {event_id}")
                    
            except Exception as e:
                print(f"[ERROR] Failed to query participant {external_image_id}: {e}")
                continue
        
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
