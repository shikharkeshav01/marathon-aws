import os
import re
from enum import Enum
from typing import List, Tuple

import cv2
import numpy as np
import torch


class DetectionModel(Enum):
    YOLOV10N = "yolov10n"


class OCRModel(Enum):
    EASYOCR = "easyocr"
    PADDLEOCR = "paddleocr"


# Module-level model cache for Lambda container reuse
_detection_models = {}
_ocr_models = {}


def _get_detection_model(model_type: DetectionModel = DetectionModel.YOLOV10N):
    """Lazy-load detection model for container reuse."""
    global _detection_models
    if model_type not in _detection_models:
        from ultralytics import YOLO
        if model_type == DetectionModel.YOLOV10N:
            _detection_models[model_type] = YOLO("yolov10n.pt")
        else:
            raise ValueError(f"Unsupported detection model: {model_type}")
    return _detection_models[model_type]


def _get_ocr_model(model_type: OCRModel):
    """Lazy-load OCR model with GPU auto-detection."""
    global _ocr_models
    if model_type not in _ocr_models:
        use_gpu = torch.cuda.is_available()
        if model_type == OCRModel.EASYOCR:
            import easyocr
            _ocr_models[model_type] = easyocr.Reader(["en"], gpu=use_gpu)
        elif model_type == OCRModel.PADDLEOCR:
            from paddleocr import PaddleOCR
            _ocr_models[model_type] = PaddleOCR(
                use_textline_orientation=True,
                lang="en",
                device="gpu" if use_gpu else "cpu"
            )
        else:
            raise ValueError(f"Unsupported OCR model: {model_type}")
    return _ocr_models[model_type]


def _run_ocr(ocr_model, model_type: OCRModel, image, original_color_image=None) -> List[Tuple[str, float]]:
    """
    Run OCR and return list of (text, confidence) tuples.
    Normalizes output across different OCR backends.

    Args:
        ocr_model: The OCR model instance
        model_type: Type of OCR model
        image: Preprocessed grayscale image (for EasyOCR)
        original_color_image: Original BGR image (for PaddleOCR which needs color)
    """
    results = []

    if model_type == OCRModel.EASYOCR:
        ocr_results = ocr_model.readtext(
            image, detail=1, paragraph=False, slope_ths=0.1, height_ths=0.5
        )
        for bbox, text, conf in ocr_results:
            results.append((text, conf))

    elif model_type == OCRModel.PADDLEOCR:
        # PaddleOCR expects BGR color image, not grayscale
        img_for_paddle = original_color_image if original_color_image is not None else image
        ocr_output = ocr_model.predict(img_for_paddle)

        # Debug: print raw output structure
        print(f"    [PADDLE DEBUG] type={type(ocr_output)}, len={len(ocr_output) if ocr_output else 0}")
        if ocr_output:
            for i, r in enumerate(ocr_output[:2]):  # First 2 results
                print(f"    [PADDLE DEBUG] result[{i}] type={type(r)}, attrs={dir(r)[:10] if hasattr(r, '__dir__') else 'N/A'}")

        # Handle the predict() output format
        if ocr_output:
            for result in ocr_output:
                # New PaddleOCR predict() returns objects with 'rec_texts' and 'rec_scores'
                if hasattr(result, 'rec_texts') and hasattr(result, 'rec_scores'):
                    for text, conf in zip(result.rec_texts, result.rec_scores):
                        results.append((text, float(conf)))
                # Also try dictionary format
                elif isinstance(result, dict):
                    if 'rec_texts' in result and 'rec_scores' in result:
                        for text, conf in zip(result['rec_texts'], result['rec_scores']):
                            results.append((text, float(conf)))
                    elif 'text' in result:
                        text = result.get('text', '')
                        conf = result.get('confidence', result.get('score', 0.0))
                        results.append((text, float(conf)))
                # Legacy list format
                elif isinstance(result, (list, tuple)):
                    for line in result:
                        if isinstance(line, dict):
                            text = line.get('text', '')
                            conf = line.get('confidence', line.get('score', 0.0))
                            results.append((text, float(conf)))
                        elif isinstance(line, (list, tuple)) and len(line) >= 2:
                            text_part = line[1]
                            if isinstance(text_part, (list, tuple)) and len(text_part) >= 2:
                                text, conf = text_part[0], text_part[1]
                            elif isinstance(text_part, str):
                                text = text_part
                                conf = line[2] if len(line) > 2 else 1.0
                            else:
                                continue
                            results.append((text, float(conf)))

    return results


def preprocess_for_ocr(image_bgr):
    """Convert image to grayscale for OCR processing."""
    return cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)


def detect_and_extract_bibs(
    image_bytes,
    image_name: str = "input.jpg",
    detection_model: DetectionModel = DetectionModel.YOLOV10N,
    ocr_model: OCRModel = OCRModel.EASYOCR,
    conf_threshold: float = 0.5,
    ocr_conf_threshold: float = 0.6,
    min_len: int = 2,
    max_len: int = 6
) -> List[str]:
    """
    Detect people in image and extract bib numbers using configurable models.

    Args:
        image_bytes: Raw image bytes
        image_name: Name of image for logging
        detection_model: Detection model to use (YOLOV10N)
        ocr_model: OCR model to use (EASYOCR or PADDLEOCR)
        conf_threshold: Detection confidence threshold
        ocr_conf_threshold: OCR confidence threshold [0, 1]
        min_len: Minimum bib number length
        max_len: Maximum bib number length

    Returns:
        Sorted list of detected bib numbers
    """
    # Load models (cached for container reuse)
    detector = _get_detection_model(detection_model)
    ocr = _get_ocr_model(ocr_model)
    person_class_id = 0

    # Decode image
    np_buffer = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(np_buffer, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Failed to decode image bytes.")

    print(f"[IMG] {image_name} (detector={detection_model.value}, ocr={ocr_model.value})")

    # Detect persons only
    results = detector.predict(
        source=img, classes=[person_class_id], conf=conf_threshold, iou=0.5, verbose=False
    )
    bibs = set()

    if len(results) > 0 and results[0].boxes is not None:
        boxes = results[0].boxes
        xyxy = boxes.xyxy.cpu().numpy() if hasattr(boxes.xyxy, "cpu") else boxes.xyxy
        confs = boxes.conf.cpu().numpy() if hasattr(boxes.conf, "cpu") else boxes.conf
        print(f"[DETECT] persons={len(xyxy)} (conf>={conf_threshold})")

        for (x1, y1, x2, y2), det_conf in zip(xyxy, confs):
            x1, y1, x2, y2 = map(int, [x1, y1, x2, y2])
            x1 = max(0, x1)
            y1 = max(0, y1)
            x2 = min(img.shape[1], x2)
            y2 = min(img.shape[0], y2)
            if x2 <= x1 or y2 <= y1:
                continue

            # Extract torso region where bibs are typically worn
            # Bibs can be on chest (25-50%) or waist/belt (50-80%)
            person_height = y2 - y1
            person_width = x2 - x1

            # Vertical: start at 15% from top (below head), end at 95% (includes waist/belt bibs fully)
            torso_y1 = y1 + int(person_height * 0.15)
            torso_y2 = y1 + int(person_height * 0.95)

            # Horizontal: center 70% (exclude arms on sides)
            margin_x = int(person_width * 0.15)
            torso_x1 = x1 + margin_x
            torso_x2 = x2 - margin_x

            # Ensure valid bounds
            torso_x1 = max(0, torso_x1)
            torso_y1 = max(0, torso_y1)
            torso_x2 = min(img.shape[1], torso_x2)
            torso_y2 = min(img.shape[0], torso_y2)

            if torso_x2 <= torso_x1 or torso_y2 <= torso_y1:
                continue

            crop = img[torso_y1:torso_y2, torso_x1:torso_x2]
            if crop.size == 0:
                continue
            print(f"  [BOX] person=({x1},{y1},{x2},{y2}) torso=({torso_x1},{torso_y1},{torso_x2},{torso_y2}) conf={float(det_conf):.2f}")

            # # Debug: save crop to verify what OCR sees
            # debug_path = f"/tmp/debug_crop_{image_name}"
            # cv2.imwrite(debug_path, crop)
            # print(f"  [DEBUG] Saved crop to {debug_path}")

            # Preprocess and run OCR
            prep = preprocess_for_ocr(crop)
            ocr_results = _run_ocr(ocr, ocr_model, prep, original_color_image=crop)

            # Debug: show all OCR results before filtering
            if ocr_results:
                print(f"    [OCR RAW] {[(text, f'{conf:.2f}') for text, conf in ocr_results]}")

            for text, conf in ocr_results:
                raw_text = (text or "").strip()

                # Skip if the original text has too many non-digit characters
                # Real bib numbers are mostly/purely numeric
                digit_count = sum(c.isdigit() for c in raw_text)
                non_digit_count = len(raw_text) - digit_count
                if non_digit_count > digit_count:
                    # More letters than digits - likely not a bib (e.g., "21.1 KM HM")
                    continue

                text_clean = re.sub(r"[^0-9]", "", raw_text)
                if not text_clean:
                    continue
                if not (min_len <= len(text_clean) <= max_len):
                    continue
                if conf < ocr_conf_threshold:
                    continue
                bibs.add(text_clean)
                print(f"    [BIB] {text_clean} (OCR conf={conf:.2f}, raw='{raw_text}')")

    sorted_bibs = sorted(bibs)
    print(f"[SUMMARY] {image_name}: {sorted_bibs}")

    return sorted_bibs


# Backwards compatibility alias
def detect_and_tabulate_bibs_easyocr(
    image_bytes,
    image_name="input.jpg",
    conf_threshold=0.5,
    ocr_conf_threshold=0.6,
    min_len=2,
    max_len=6
) -> List[str]:
    """Legacy function name for backwards compatibility."""
    return detect_and_extract_bibs(
        image_bytes=image_bytes,
        image_name=image_name,
        detection_model=DetectionModel.YOLOV10N,
        ocr_model=OCRModel.EASYOCR,
        conf_threshold=conf_threshold,
        ocr_conf_threshold=ocr_conf_threshold,
        min_len=min_len,
        max_len=max_len
    )


# def main():
#     """Example usage with configurable models."""
#     # Can be set via environment variables
#     detection = os.environ.get("DETECTION_MODEL", "yolov10n")
#     ocr = os.environ.get("OCR_MODEL", "easyocr")
#     os.environ.setdefault("DISABLE_MODEL_SOURCE_CHECK", "True")
#
#     detection_model = DetectionModel(detection)
#     ocr_model = OCRModel(ocr)
#
#     with open("/Users/sunny/Downloads/SUN_7546.jpg", "rb") as f:
#         photo_bytes = f.read()
#         bib_numbers = detect_and_extract_bibs(
#             photo_bytes,
#             image_name="SUN_7546.jpg",
#             detection_model=detection_model,
#             ocr_model=ocr_model
#         )
#         print(f"Detected bib numbers: {bib_numbers}")
#
#
# if __name__ == "__main__":
#     main()
