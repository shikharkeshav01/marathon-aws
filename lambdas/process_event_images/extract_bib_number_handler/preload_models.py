"""
Preload models during Docker build to avoid cold start delays.

Environment variables to control which models to preload:
- PRELOAD_DETECTION_MODELS: Comma-separated list (default: "yolov10n")
- PRELOAD_OCR_MODELS: Comma-separated list (default: "easyocr,paddleocr")

Set to empty string to skip preloading that category.
"""
import os
import time

# Which models to preload (can be configured via env vars during build)
DETECTION_MODELS = os.environ.get("PRELOAD_DETECTION_MODELS", "yolov10n").split(",")
OCR_MODELS = os.environ.get("PRELOAD_OCR_MODELS", "easyocr,paddleocr").split(",")

# Filter empty strings
DETECTION_MODELS = [m.strip() for m in DETECTION_MODELS if m.strip()]
OCR_MODELS = [m.strip() for m in OCR_MODELS if m.strip()]

print("[PRELOAD] Starting model preload...")
print(f"[PRELOAD] Detection models to load: {DETECTION_MODELS}")
print(f"[PRELOAD] OCR models to load: {OCR_MODELS}")

# Preload YOLO detection models
if DETECTION_MODELS:
    from ultralytics import YOLO

    for model_name in DETECTION_MODELS:
        print(f"[PRELOAD] Loading YOLO model ({model_name}.pt)...")
        try:
            model = YOLO(f"{model_name}.pt")
            print(f"[PRELOAD] {model_name} loaded successfully")
        except Exception as e:
            print(f"[PRELOAD] Error loading {model_name}: {e}")
            raise

# Preload OCR models with retry logic
for ocr_name in OCR_MODELS:
    if ocr_name == "easyocr":
        print("[PRELOAD] Loading EasyOCR reader (English)...")
        max_retries = 3
        retry_delay = 5

        for attempt in range(max_retries):
            try:
                import easyocr
                reader = easyocr.Reader(["en"], gpu=False)  # Use CPU during build
                print("[PRELOAD] EasyOCR reader loaded successfully")
                break
            except Exception as e:
                if attempt < max_retries - 1:
                    print(f"[PRELOAD] Error loading EasyOCR reader (attempt {attempt + 1}/{max_retries}): {e}")
                    print(f"[PRELOAD] Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                else:
                    print(f"[PRELOAD] Error loading EasyOCR reader after {max_retries} attempts: {e}")
                    raise

    elif ocr_name == "paddleocr":
        print("[PRELOAD] Loading PaddleOCR (English)...")
        max_retries = 3
        retry_delay = 5

        for attempt in range(max_retries):
            try:
                from paddleocr import PaddleOCR
                ocr = PaddleOCR(use_textline_orientation=True, lang="en", device="cpu")
                print("[PRELOAD] PaddleOCR loaded successfully")
                break
            except Exception as e:
                if attempt < max_retries - 1:
                    print(f"[PRELOAD] Error loading PaddleOCR (attempt {attempt + 1}/{max_retries}): {e}")
                    print(f"[PRELOAD] Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                else:
                    print(f"[PRELOAD] Error loading PaddleOCR after {max_retries} attempts: {e}")
                    raise

print("[PRELOAD] All models preloaded successfully!")
