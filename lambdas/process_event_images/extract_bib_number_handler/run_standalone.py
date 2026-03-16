#!/usr/bin/env python3
"""Standalone runner for bib extraction with debug output."""

from bib_extraction import detect_and_extract_bibs

if __name__ == "__main__":
    import sys
    import os
    
    if len(sys.argv) < 2:
        print("Usage: python run_standalone.py <image_path> [ocr_conf_threshold] [debug_output_dir]")
        sys.exit(1)

    image_path = sys.argv[1]
    ocr_conf = float(sys.argv[2]) if len(sys.argv) > 2 else 0.8
    debug_dir = sys.argv[3] if len(sys.argv) > 3 else None

    with open(image_path, "rb") as f:
        image_bytes = f.read()

    bibs = detect_and_extract_bibs(
        image_bytes,
        image_name=os.path.basename(image_path),
        ocr_conf_threshold=ocr_conf,
        debug_output_dir=debug_dir
    )
    
    print(f"\n=== Final Result ===")
    print(f"Detected bibs: {bibs}")
    if debug_dir:
        print(f"Debug images saved to: {debug_dir}")

