#!/usr/bin/env python3
"""
Test script for running reel generation locally without AWS dependencies.

Usage:
    python test_local.py

Prerequisites:
    - Background video file
    - Image files
    - Python packages: moviepy, pillow, numpy
"""

import json
import os
from handler import generate_reel_local, process_reel_config


def main():
    # Example configuration - adjust paths to your local files
    video_path = "/Users/sunny/Downloads/background_video.mp4"  # Path to your background video
    image_paths = [
        "/Users/sunny/Downloads/image1.jpeg",  # Path to first image
        "/Users/sunny/Downloads/image2.jpeg",
        "/Users/sunny/Downloads/image3.jpeg"
    ]
    output_path = "/Users/sunny/Downloads/output_reel.mp4"

    # Example reel configuration with template variables
    reel_config = {
        "overlays": [
            {
                "type": "text",
                "text": "Bib No: ${bibId}\nRunner: ${runner}\nCompletion Time: ${completionTime}\nCategory: ${category}",
                "start_time": 9.0,
                "duration": 2.0,
                "position": "center",
                "text_style": {
                    "font_size": 60,
                    "color": "#FFFFFF",
                    "stroke_color": "#000000",
                    "stroke_width": 3,
                    "padding": 100,
                    "align": "top-center",
                    "char_animation": True,
                    "char_fade_duration": 0.01,
                    "char_delay": 0.03,
                    "bg_gradient": {
                        "start": "#000000FF",
                        "end": "#000000FF"
                    }
                }
            },
            {
                "type": "image_stack",
                "start_time": 12.0,
                "duration": 7.0,
                "position": "center",
                "bg_color": "#FFFFFF",
                "width": 0.7,
                "height": 0.7,
                "fit_mode": "cover",
                "rotation_range": 15,
                "opacity": 1.0,
            }
        ]
    }

    # Template variables to substitute
    template_vars = {
        "bibId": "123456",
        "runner": "Jatin Sharmadasdasdasdasd"[:10],
        "category": None,
        "completionTime": "02:30:15"
    }

    # Validate files exist
    if not os.path.exists(video_path):
        print(f"Error: Background video not found: {video_path}")
        print("\nPlease provide a background video file.")
        return

    for img_path in image_paths:
        if not os.path.exists(img_path):
            print(f"Error: Image not found: {img_path}")
            print("\nPlease provide all required image files.")
            return

    # Generate the reel
    print("Starting reel generation...")
    print(f"Video: {video_path}")
    print(f"Images: {image_paths}")
    print(f"Output: {output_path}")
    print(f"Template vars: {template_vars}")
    print()

    try:
        # Process reel config to evaluate template variables before passing
        processed_config = process_reel_config(reel_config, template_vars)
        print(f"Processed config: {json.dumps(processed_config, indent=2)}")
        print()
        
        result_path = generate_reel_local(
            video_path=video_path,
            image_paths=image_paths,
            reel_config_json=json.dumps(processed_config),
            output_path=output_path
        )
        print(f"\n✓ Success! Reel generated at: {result_path}")
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
