import importlib
import os
import pathlib
import sys
import types
import unittest
from enum import Enum
from unittest.mock import patch


def _install_dependency_stubs():
    os.environ.setdefault("RAW_BUCKET", "test-bucket")
    os.environ.setdefault("GDRIVE_SA_SSM_PARAM", "google-service-account")
    os.environ.setdefault("EVENT_PARTICIPANTS_TABLE", "EventParticipants")

    fake_boto3 = types.ModuleType("boto3")

    class _FakeDdbResource:
        def Table(self, name):
            return object()

    class _FakeSsmClient:
        def get_parameter(self, Name, WithDecryption):
            return {"Parameter": {"Value": '{"type": "service_account"}'}}

    fake_boto3.resource = lambda name: _FakeDdbResource()
    fake_boto3.client = lambda name: _FakeSsmClient() if name == "ssm" else object()

    googleapiclient = types.ModuleType("googleapiclient")
    discovery = types.ModuleType("googleapiclient.discovery")
    discovery.build = lambda *args, **kwargs: object()

    errors = types.ModuleType("googleapiclient.errors")
    errors.HttpError = Exception

    google = types.ModuleType("google")
    google_oauth2 = types.ModuleType("google.oauth2")
    service_account = types.ModuleType("google.oauth2.service_account")

    class _FakeCredentials:
        @staticmethod
        def from_service_account_info(info, scopes=None):
            return object()

    service_account.Credentials = _FakeCredentials

    fake_bib_extraction = types.ModuleType("bib_extraction")

    class DetectionModel(Enum):
        YOLOV10N = "yolov10n"

    class OCRModel(Enum):
        EASYOCR = "easyocr"

    fake_bib_extraction.DetectionModel = DetectionModel
    fake_bib_extraction.OCRModel = OCRModel
    fake_bib_extraction.detect_and_extract_bibs = lambda *args, **kwargs: []

    sys.modules.setdefault("boto3", fake_boto3)
    sys.modules.setdefault("googleapiclient", googleapiclient)
    sys.modules.setdefault("googleapiclient.discovery", discovery)
    sys.modules.setdefault("googleapiclient.errors", errors)
    sys.modules.setdefault("google", google)
    sys.modules.setdefault("google.oauth2", google_oauth2)
    sys.modules.setdefault("google.oauth2.service_account", service_account)
    sys.modules.setdefault("bib_extraction", fake_bib_extraction)


_install_dependency_stubs()
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
lambda_function = importlib.import_module("lambda_function")


class _FakeParticipantsTable:
    def __init__(self, known_bibs):
        self.known_bibs = set(known_bibs)

    def get_item(self, Key):
        bib_id = Key["BibId"]
        if bib_id in self.known_bibs:
            return {"Item": {"BibId": bib_id}}
        return {}


class ExtractBibNumbersValidationTests(unittest.TestCase):
    def _run_validation(self, detected_bibs, known_bibs):
        fake_table = _FakeParticipantsTable(known_bibs)
        fake_ddb = types.SimpleNamespace(Table=lambda name: fake_table)

        with patch.object(lambda_function, "ddb", fake_ddb), patch.object(
            lambda_function, "detect_and_extract_bibs", return_value=detected_bibs
        ):
            return lambda_function.extract_bib_numbers(b"photo", 1, "image.jpg")

    def test_does_not_drop_letter_prefix_during_lookup(self):
        result = self._run_validation(["W2334"], {"2334"})
        self.assertEqual(result, [])

    def test_prefixed_bib_can_match_by_trimming_last_digit(self):
        result = self._run_validation(["W23344"], {"W2334"})
        self.assertEqual(result, ["W2334"])

    def test_prefixed_bib_can_match_by_trimming_first_digit_after_prefix(self):
        result = self._run_validation(["W2334"], {"W334"})
        self.assertEqual(result, ["W334"])


if __name__ == "__main__":
    unittest.main()