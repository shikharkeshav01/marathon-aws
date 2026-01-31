#!/usr/bin/env python3
"""
Test script to check if a face in an image matches against Rekognition collection.
"""
import boto3
import sys

COLLECTION_ID = "marathon-participants"
REGION = "ap-south-1"

rekognition = boto3.client('rekognition', region_name=REGION)


def test_face_match(image_path, threshold=85.0):
    """
    Test if faces in the image match against the Rekognition collection.
    
    Args:
        image_path: Path to the image file
        threshold: Minimum similarity threshold (0-100)
    
    Returns:
        List of matches with email and similarity score
    """
    print(f"Testing face match for: {image_path}")
    print(f"Collection: {COLLECTION_ID}")
    print(f"Threshold: {threshold}%")
    print("-" * 60)
    
    # Read image file
    with open(image_path, 'rb') as image_file:
        image_bytes = image_file.read()
    
    print(f"Image size: {len(image_bytes)} bytes")
    
    # First, detect faces in the image
    print("\n1. Detecting faces in image...")
    try:
        detect_response = rekognition.detect_faces(
            Image={'Bytes': image_bytes},
            Attributes=['DEFAULT']
        )
        
        faces = detect_response.get('FaceDetails', [])
        print(f"   Found {len(faces)} face(s) in the image")
        
        for idx, face in enumerate(faces):
            bbox = face['BoundingBox']
            confidence = face['Confidence']
            print(f"   Face {idx+1}: confidence={confidence:.2f}%, bbox={bbox}")
    
    except Exception as e:
        print(f"   Error detecting faces: {e}")
        return []
    
    # Search for matching faces in collection
    print(f"\n2. Searching for matches in collection '{COLLECTION_ID}'...")
    try:
        search_response = rekognition.search_faces_by_image(
            CollectionId=COLLECTION_ID,
            Image={'Bytes': image_bytes},
            MaxFaces=10,
            FaceMatchThreshold=threshold
        )
        
        face_matches = search_response.get('FaceMatches', [])
        print(f"   Found {len(face_matches)} match(es) above {threshold}% threshold")
        
        results = []
        for idx, match in enumerate(face_matches):
            face = match['Face']
            external_id = face.get('ExternalImageId', 'N/A')
            similarity = match['Similarity']
            face_id = face.get('FaceId')
            
            # Decode email (__ back to @)
            email = external_id.replace('__', '@')
            
            print(f"\n   Match {idx+1}:")
            print(f"      Email: {email}")
            print(f"      Similarity: {similarity:.2f}%")
            print(f"      FaceId: {face_id}")
            print(f"      ExternalImageId: {external_id}")
            
            results.append({
                'email': email,
                'similarity': similarity,
                'face_id': face_id,
                'external_id': external_id
            })
        
        return results
        
    except rekognition.exceptions.InvalidParameterException as e:
        print(f"   No faces detected in image: {e}")
        return []
    except rekognition.exceptions.ResourceNotFoundException as e:
        print(f"   Collection not found: {e}")
        print(f"   Create with: aws rekognition create-collection --collection-id {COLLECTION_ID} --region {REGION}")
        return []
    except Exception as e:
        print(f"   Error searching faces: {e}")
        return []


def list_indexed_faces():
    """List all faces currently in the collection."""
    print("\n3. Listing all indexed faces in collection...")
    try:
        response = rekognition.list_faces(
            CollectionId=COLLECTION_ID,
            MaxResults=100
        )
        
        faces = response.get('Faces', [])
        print(f"   Total faces in collection: {len(faces)}")
        
        for idx, face in enumerate(faces):
            external_id = face.get('ExternalImageId', 'N/A')
            email = external_id.replace('__', '@')
            confidence = face.get('Confidence', 0)
            face_id = face.get('FaceId')
            
            print(f"   {idx+1}. {email} (confidence={confidence:.2f}%, FaceId={face_id})")
        
    except Exception as e:
        print(f"   Error listing faces: {e}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python test_face_match.py <image_path> [threshold]")
        print("Example: python test_face_match.py /Users/sunny/Downloads/SUN_5960.jpg 85")
        sys.exit(1)
    
    image_path = sys.argv[1]
    threshold = float(sys.argv[2]) if len(sys.argv) > 2 else 85.0
    
    # List indexed faces first
    list_indexed_faces()
    
    print("\n" + "=" * 60)
    
    # Test face matching
    matches = test_face_match(image_path, threshold)
    
    print("\n" + "=" * 60)
    print("SUMMARY:")
    if matches:
        print(f"✅ Found {len(matches)} match(es):")
        for match in matches:
            print(f"   - {match['email']} ({match['similarity']:.2f}% similarity)")
    else:
        print("❌ No matches found")
    print("=" * 60)
