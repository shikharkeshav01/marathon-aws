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
    """
    Preprocess image for OCR using binary thresholding.

    Binary thresholding (Otsu's method) provides better contrast and
    higher OCR confidence compared to simple grayscale conversion.
    """
    # Convert to grayscale first
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)

    # Apply Otsu's binary thresholding for better OCR accuracy
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    return binary


def _normalize_bib_candidate(raw_text: str, min_len: int, max_len: int):
    """
    Normalize OCR text into a supported bib format.

    Supported formats:
    - digits only, e.g. 45218
    - one leading letter followed by digits, e.g. W2334
    """
    normalized = re.sub(r"[^A-Za-z0-9]", "", (raw_text or "").strip()).upper()
    if not normalized:
        return None

    pattern = rf"(?:[0-9]{{{min_len},{max_len}}}|[A-Z][0-9]{{{min_len},{max_len}}})"
    if not re.fullmatch(pattern, normalized):
        return None

    return normalized


def detect_and_extract_bibs(
    image_bytes,
    image_name: str = "input.jpg",
    detection_model: DetectionModel = DetectionModel.YOLOV10N,
    ocr_model: OCRModel = OCRModel.EASYOCR,
    conf_threshold: float = 0.6,
    ocr_conf_threshold: float = 0.8,
    min_len: int = 2,
    max_len: int = 6,
    debug_output_dir: str = None
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
        debug_output_dir: Optional directory to save debug crops

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

        for person_idx, ((x1, y1, x2, y2), det_conf) in enumerate(zip(xyxy, confs)):
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

            # Try multiple crop strategies to maximize bib capture
            # Strategy 1: Chest area (where most bibs are)
            # Strategy 2: Waist/belt area (for waist bibs)
            # We'll run OCR on both and combine results

            crop_regions = []

            # Region 1: Chest area (optimized for chest bibs)
            # Vertical: 10% to 60% of person height (upper torso)
            # Horizontal: center 80% (wider to avoid cutting off bib edges)
            chest_y1 = y1 + int(person_height * 0.10)
            chest_y2 = y1 + int(person_height * 0.60)
            margin_x_chest = int(person_width * 0.10)
            chest_x1 = x1 + margin_x_chest
            chest_x2 = x2 - margin_x_chest

            # Ensure valid bounds
            chest_x1 = max(0, chest_x1)
            chest_y1 = max(0, chest_y1)
            chest_x2 = min(img.shape[1], chest_x2)
            chest_y2 = min(img.shape[0], chest_y2)

            if chest_x2 > chest_x1 and chest_y2 > chest_y1:
                crop_regions.append(("chest", chest_x1, chest_y1, chest_x2, chest_y2))

            # Region 2: Waist/belt area (for waist bibs)
            # Vertical: 50% to 95% of person height (extended to capture lower bibs)
            # Horizontal: center 80%
            waist_y1 = y1 + int(person_height * 0.50)
            waist_y2 = y1 + int(person_height * 0.95)
            margin_x_waist = int(person_width * 0.10)
            waist_x1 = x1 + margin_x_waist
            waist_x2 = x2 - margin_x_waist

            # Ensure valid bounds
            waist_x1 = max(0, waist_x1)
            waist_y1 = max(0, waist_y1)
            waist_x2 = min(img.shape[1], waist_x2)
            waist_y2 = min(img.shape[0], waist_y2)

            if waist_x2 > waist_x1 and waist_y2 > waist_y1:
                crop_regions.append(("waist", waist_x1, waist_y1, waist_x2, waist_y2))

            if not crop_regions:
                continue

            print(f"  [BOX] person={person_idx} bbox=({x1},{y1},{x2},{y2}) conf={float(det_conf):.2f}")

            # Process each crop region
            all_ocr_results = []
            for region_name, rx1, ry1, rx2, ry2 in crop_regions:
                crop = img[ry1:ry2, rx1:rx2]
                if crop.size == 0:
                    continue

                print(f"    [CROP] {region_name}=({rx1},{ry1},{rx2},{ry2})")

                # Debug: save crop to verify what OCR sees
                if debug_output_dir:
                    import os
                    os.makedirs(debug_output_dir, exist_ok=True)
                    crop_path = os.path.join(debug_output_dir, f"person_{person_idx}_{region_name}_crop.jpg")
                    cv2.imwrite(crop_path, crop)
                    print(f"    [DEBUG] Saved {region_name} crop to {crop_path}")

                # Try multiple preprocessing techniques and combine results
                # This improves robustness across different image conditions
                preprocessing_methods = [
                    ("grayscale", lambda img: cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)),
                    ("binary", preprocess_for_ocr),  # Binary threshold (Otsu)
                ]

                method_results = {}
                for method_name, preprocess_fn in preprocessing_methods:
                    prep = preprocess_fn(crop)

                    # Debug: save preprocessed image
                    if debug_output_dir:
                        import os
                        os.makedirs(debug_output_dir, exist_ok=True)
                        prep_path = os.path.join(debug_output_dir, f"person_{person_idx}_{region_name}_{method_name}.jpg")
                        cv2.imwrite(prep_path, prep)

                    results = _run_ocr(ocr, ocr_model, prep, original_color_image=crop)
                    method_results[method_name] = results

                # Combine results from all preprocessing methods, keeping highest confidence for each text
                combined = {}
                for method_name, results in method_results.items():
                    for text, conf in results:
                        if text not in combined or conf > combined[text]:
                            combined[text] = conf

                ocr_results = [(text, conf) for text, conf in combined.items()]

                # Debug: show all OCR results before filtering
                if ocr_results:
                    print(f"      [OCR RAW] {[(text, f'{conf:.2f}') for text, conf in ocr_results]}")

                all_ocr_results.extend(ocr_results)

            # Process all OCR results from all crop regions
            for text, conf in all_ocr_results:
                raw_text = (text or "").strip()
                if conf < ocr_conf_threshold:
                    continue

                text_clean = _normalize_bib_candidate(raw_text, min_len, max_len)
                if not text_clean:
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
    ocr_conf_threshold=0.3,
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


def main():
    """Example usage with configurable models."""
    # Can be set via environment variables
    detection = os.environ.get("DETECTION_MODEL", "yolov10n")
    ocr = os.environ.get("OCR_MODEL", "easyocr")
    os.environ.setdefault("DISABLE_MODEL_SOURCE_CHECK", "True")

    detection_model = DetectionModel(detection)
    ocr_model = OCRModel(ocr)

    with open("/Users/sunny/Downloads/SUN_7290.jpg", "rb") as f:
        photo_bytes = f.read()
        bib_numbers = detect_and_extract_bibs(
            photo_bytes,
            image_name="SUN_7290.jpg",
            detection_model=detection_model,
            ocr_model=ocr_model
        )
        print(f"Detected bib numbers: {bib_numbers}")


if __name__ == "__main__":
    main()
