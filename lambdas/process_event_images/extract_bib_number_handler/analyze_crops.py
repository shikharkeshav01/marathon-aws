#!/usr/bin/env python3
"""Analyze all crops to find potential bibs."""

import cv2
import easyocr
import sys
import os

def analyze_crops(debug_dir):
    reader = easyocr.Reader(['en'], gpu=False)
    
    # Get all crop files
    crops = sorted([f for f in os.listdir(debug_dir) if f.endswith('_crop.jpg')])
    
    print(f"\n=== Analyzing {len(crops)} crops from {debug_dir} ===\n")
    
    for crop_file in crops:
        path = os.path.join(debug_dir, crop_file)
        img = cv2.imread(path)
        
        if img is None or img.size == 0:
            continue
        
        # Run OCR
        results = reader.readtext(img, detail=1)
        
        # Filter for anything with digits
        has_digits = [f"{text} ({conf:.2f})" for bbox, text, conf in results if any(c.isdigit() for c in text)]
        
        if has_digits:
            print(f"{crop_file}:")
            for item in has_digits:
                print(f"  {item}")
            print()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python analyze_crops.py <debug_dir>")
        sys.exit(1)
    
    analyze_crops(sys.argv[1])

