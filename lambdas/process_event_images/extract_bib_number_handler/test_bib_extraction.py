import pathlib
import sys
import types
import unittest
from unittest.mock import patch


class _FakeImage:
    shape = (1000, 800, 3)
    size = 1

    def __getitem__(self, key):
        return _FakeImage()


class _FakeTensor:
    def __init__(self, value):
        self._value = value

    def cpu(self):
        return self

    def numpy(self):
        return self._value


def _install_dependency_stubs():
    cv2 = types.ModuleType("cv2")
    cv2.IMREAD_COLOR = 1
    cv2.COLOR_BGR2GRAY = 6
    cv2.THRESH_BINARY = 0
    cv2.THRESH_OTSU = 8
    cv2.imdecode = lambda buffer, flag: _FakeImage()
    cv2.cvtColor = lambda image, mode: image
    cv2.threshold = lambda img, thresh, maxval, type: (0, img)  # Returns (retval, thresholded_image)

    numpy = types.ModuleType("numpy")
    numpy.uint8 = "uint8"
    numpy.frombuffer = lambda image_bytes, dtype=None: image_bytes

    torch = types.ModuleType("torch")
    torch.cuda = types.SimpleNamespace(is_available=lambda: False)

    sys.modules.setdefault("cv2", cv2)
    sys.modules.setdefault("numpy", numpy)
    sys.modules.setdefault("torch", torch)


_install_dependency_stubs()
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import bib_extraction


class _FakeBoxes:
    def __init__(self, boxes=None):
        if boxes is None:
            boxes = [[10, 20, 300, 600]]
        self.xyxy = _FakeTensor(boxes)
        self.conf = _FakeTensor([0.95] * len(boxes))


class _FakeResult:
    def __init__(self, boxes=None):
        self.boxes = _FakeBoxes(boxes)


class _FakeDetector:
    def __init__(self, boxes=None):
        self._boxes = boxes

    def predict(self, **kwargs):
        return [_FakeResult(self._boxes)]


class DetectAndExtractBibsTests(unittest.TestCase):
    def _run_detection(self, ocr_results, ocr_conf_threshold=0.8, boxes=None):
        with patch.object(bib_extraction, "_get_detection_model", return_value=_FakeDetector(boxes)), patch.object(
            bib_extraction, "_get_ocr_model", return_value=object()
        ), patch.object(bib_extraction, "_run_ocr", return_value=ocr_results):
            return bib_extraction.detect_and_extract_bibs(
                b"fake-image", ocr_conf_threshold=ocr_conf_threshold
            )

    def test_preserves_numeric_and_prefixed_bibs(self):
        result = self._run_detection([
            ("456783", 0.95),
            ("w2334", 0.99),
            ("F-23344", 0.97),
        ])
        self.assertEqual(result, ["456783", "F23344", "W2334"])

    def test_rejects_invalid_bib_patterns(self):
        result = self._run_detection([
            ("AB2334", 0.99),
            ("2W334", 0.99),
            ("BIB123", 0.99),
            ("W23A4", 0.99),
            ("W 2334", 0.99),
        ])
        self.assertEqual(result, ["W2334"])

    def test_rejects_low_confidence_matches(self):
        result = self._run_detection([("W2334", 0.79)], ocr_conf_threshold=0.8)
        self.assertEqual(result, [])

    def test_detects_multiple_bibs_from_multiple_people(self):
        """Test that multiple bibs from different people are all detected."""
        # Simulate 3 people detected
        boxes = [
            [10, 20, 300, 600],   # Person 0
            [350, 50, 600, 650],  # Person 1
            [650, 100, 900, 700], # Person 2
        ]

        # Mock OCR to return different bibs for each person
        ocr_call_count = [0]
        def mock_ocr(*args, **kwargs):
            # Each person has 2 crop regions (chest + waist), so 6 total calls
            # Return different bibs for different people
            person_idx = ocr_call_count[0] // 2  # 2 crops per person
            ocr_call_count[0] += 1

            if person_idx == 0:
                return [("F1015", 0.99)]
            elif person_idx == 1:
                return [("W2334", 0.95)]
            elif person_idx == 2:
                return [("45218", 0.98)]
            return []

        with patch.object(bib_extraction, "_get_detection_model", return_value=_FakeDetector(boxes)), patch.object(
            bib_extraction, "_get_ocr_model", return_value=object()
        ), patch.object(bib_extraction, "_run_ocr", side_effect=mock_ocr):
            result = bib_extraction.detect_and_extract_bibs(b"fake-image")

        # Should detect all 3 bibs
        self.assertEqual(sorted(result), ["45218", "F1015", "W2334"])


if __name__ == "__main__":
    unittest.main()