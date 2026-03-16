#!/usr/bin/env python3
"""Debug OCR to understand why F is not detected."""

import cv2
import numpy as np
import easyocr
import sys

def test_different_preprocessing(crop_path):
    """Test different preprocessing techniques on the crop."""
    
    # Load the crop
    img = cv2.imread(crop_path)
    if img is None:
        print(f"Failed to load {crop_path}")
        return
    
    # Initialize EasyOCR
    reader = easyocr.Reader(['en'], gpu=False)
    
    print(f"\n=== Testing different preprocessing on {crop_path} ===\n")
    
    # Test 1: Original color image
    print("1. Original color image:")
    results = reader.readtext(img, detail=1)
    for bbox, text, conf in results:
        print(f"   '{text}' (conf={conf:.2f})")
    
    # Test 2: Grayscale (current approach)
    print("\n2. Grayscale:")
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    results = reader.readtext(gray, detail=1)
    for bbox, text, conf in results:
        print(f"   '{text}' (conf={conf:.2f})")
    
    # Test 3: Binary threshold
    print("\n3. Binary threshold (Otsu):")
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    cv2.imwrite('/tmp/debug_binary.jpg', binary)
    results = reader.readtext(binary, detail=1)
    for bbox, text, conf in results:
        print(f"   '{text}' (conf={conf:.2f})")
    
    # Test 4: Adaptive threshold
    print("\n4. Adaptive threshold:")
    adaptive = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, 
                                     cv2.THRESH_BINARY, 11, 2)
    cv2.imwrite('/tmp/debug_adaptive.jpg', adaptive)
    results = reader.readtext(adaptive, detail=1)
    for bbox, text, conf in results:
        print(f"   '{text}' (conf={conf:.2f})")
    
    # Test 5: Contrast enhancement (CLAHE)
    print("\n5. CLAHE (contrast enhancement):")
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
    enhanced = clahe.apply(gray)
    cv2.imwrite('/tmp/debug_clahe.jpg', enhanced)
    results = reader.readtext(enhanced, detail=1)
    for bbox, text, conf in results:
        print(f"   '{text}' (conf={conf:.2f})")
    
    # Test 6: Inverted
    print("\n6. Inverted grayscale:")
    inverted = cv2.bitwise_not(gray)
    cv2.imwrite('/tmp/debug_inverted.jpg', inverted)
    results = reader.readtext(inverted, detail=1)
    for bbox, text, conf in results:
        print(f"   '{text}' (conf={conf:.2f})")
    
    # Test 7: Sharpened
    print("\n7. Sharpened:")
    kernel = np.array([[-1,-1,-1], [-1,9,-1], [-1,-1,-1]])
    sharpened = cv2.filter2D(gray, -1, kernel)
    cv2.imwrite('/tmp/debug_sharpened.jpg', sharpened)
    results = reader.readtext(sharpened, detail=1)
    for bbox, text, conf in results:
        print(f"   '{text}' (conf={conf:.2f})")
    
    print("\nDebug images saved to /tmp/debug_*.jpg")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python debug_ocr.py <crop_image_path>")
        sys.exit(1)
    
    test_different_preprocessing(sys.argv[1])

