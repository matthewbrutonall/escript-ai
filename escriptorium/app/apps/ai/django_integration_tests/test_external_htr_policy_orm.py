"""ORM checks for the external HTR off-site policy.

A missing row and a stored boolean false stay allowed. Boolean true
restricts the endpoint to literal localhost or loopback. Django's
BooleanField cannot store a non-boolean; those values stay in
VerdictTests. Engine IO is mocked. No production rows are read.
"""
import json
import os
import tempfile
import uuid
from io import BytesIO
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image

from ai.external_htr_audit_command import execute_external_htr_audit
from ai.external_htr_live_command import execute_external_htr_live_check
from ai.external_htr_policy import (
    CODE_INVALID,
    CODE_OFFSITE,
    review_document_endpoint,
    review_live_request,
)
from ai.management.commands.check_external_htr_live import (
    _load_config as load_live_config,
    _read_request,
)
from ai.management.commands.run_external_htr_audit import _load_config, _load_part
from ai.models import AIDocumentPolicy, ExternalHTREngineConfig, ExternalHTRJob
from core.models import Document, DocumentPart, Line, LineTranscription, Project
from users.models import User

MEDIA = "/tmp/htr-policy-orm-media"

REMOTE = (
    "http://10.0.0.8:9/",
    "http://172.17.0.2:9/",
    "http://192.168.1.9:9/",
    "http://0.0.0.0:9/",
    "http://pylaia-smoke:8766/",
    "http://[::ffff:127.0.0.1]:9/",
)
LOOPBACK = (
    "http://127.0.0.1:8766/",
    "http://127.0.0.2:8766/",
    "http://localhost:8766/",
    "http://[::1]:8766/",
)


def _png():
    buffer = BytesIO()
    Image.new("RGB", (60, 60), (20, 20, 20)).save(buffer, "PNG")
    payload = buffer.getvalue()
    return SimpleUploadedFile("page.png", payload, "image/png"), len(payload)


def _recognize(document, part, line):
    return {
        "api_version": "1",
        "job_id": "orm-check",
        "document_id": str(document.pk),
        "part_id": str(part.pk),
        "engine": "reference",
        "model_id": "model-a",
        "preprocessing": {},
        "lines": [{"line_id": str(line.pk), "image": "QQ=="}],
    }


class _Response:
    status = 200

    def __init__(self, body):
        self.body = body

    def read(self, limit=-1):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _Opener:
    """Stand-in for the engine socket. The body matches the posted request."""

    def __init__(self):
        self.calls = []

    def open(self, request, timeout=None):
        self.calls.append(request.get_full_url())
        posted = json.loads(request.data.decode("utf-8"))
        line_id = posted["lines"][0]["line_id"]
        payload = {
            "api_version": "1",
            "job_id": posted["job_id"],
            "engine": posted["engine"],
            "model_id": posted["model_id"],
            "model_version": "1",
            "results": [{
                "line_id": line_id,
                "text": "",
                "confidence": None,
                "timing_ms": 1,
                "warnings": [],
            }],
            "provenance": {
                "engine": posted["engine"],
                "model_id": posted["model_id"],
                "model_version": "1",
                "api_version": "1",
            },
            "timing_ms": 1,
            "resources": {"device": "cpu"},
        }
        return _Response(json.dumps(payload).encode("utf-8"))


@override_settings(MEDIA_ROOT=MEDIA)
class ExternalHtrPolicyOrmTests(TestCase):
    def setUp(self):
        os.makedirs(MEDIA, exist_ok=True)
        self.owner = User.objects.create_user(
            username="orm-%s" % uuid.uuid4().hex[:12],
            email="orm-%s@example.test" % uuid.uuid4().hex[:12],
            password="unused-test-password",
        )
        self.project = Project.objects.create(
            name="orm",
            slug=uuid.uuid4().hex[:12],
            owner=self.owner,
        )
        self.document, self.part, self.line = self._page(self.project, "page")
        self.other_document, self.other_part, self.other_line = self._page(
            self.project, "other",
        )

    def _page(self, project, name):
        document = Document.objects.create(
            name=name, owner=self.owner, project=project,
        )
        upload, size = _png()
        part = DocumentPart.objects.create(
            document=document,
            image=upload,
            image_file_size=size,
            original_filename="page.png",
        )
        line = Line.objects.create(
            document_part=part,
            baseline=[[10, 30], [50, 30]],
            mask=[[10, 20], [50, 20], [50, 40], [10, 40]],
        )
        return document, part, line

    def _config(self, endpoint):
        return ExternalHTREngineConfig.objects.create(
            name="engine-%s" % uuid.uuid4().hex[:12],
            endpoint_url=endpoint,
            enabled=True,
            tier=ExternalHTREngineConfig.TIER_RESEARCH,
            timeout_seconds=5,
            metadata={},
        )

    def _owners(self, document, part, line, line_id=None):
        return {
            "document_id": document.pk,
            "part_id": part.pk,
            "lines": [{"line_id": line.pk if line_id is None else line_id}],
        }

    def _live(self, config, payload, opener):
        handle = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8",
        )
        try:
            json.dump(payload, handle)
            handle.close()
            with patch(
                "ai.htr_engine_client.urllib.request.build_opener",
                return_value=opener,
            ), patch(
                "ai.htr_engine_client.urllib.request.urlopen",
                side_effect=AssertionError("urlopen"),
            ):
                return execute_external_htr_live_check(
                    config.pk,
                    handle.name,
                    fetch_config=load_live_config,
                    read_request=_read_request,
                    review_request=review_live_request,
                )
        finally:
            os.unlink(handle.name)

    def test_ownership_lookups_use_stored_primary_keys(self):
        loaded = DocumentPart.objects.only("id", "document_id").get(pk=self.part.pk)
        self.assertEqual(loaded.document_id, self.document.pk)
        line_pks = list(
            Line.objects.filter(document_part_id=self.part.pk).values_list("pk", flat=True)
        )
        self.assertEqual(line_pks, [self.line.pk])
        self.assertNotEqual(self.line.external_id, str(self.line.pk))
        config = self._config(LOOPBACK[0])
        self.assertIsNone(review_live_request(
            config, self._owners(self.document, self.part, self.line),
        ))
        self.assertIsNone(review_document_endpoint(self.document, config))

    def test_missing_policy_row_allows_a_remote_endpoint(self):
        self.assertIsNone(
            AIDocumentPolicy.objects.filter(document_id=self.document.pk).first()
        )
        config = self._config(REMOTE[0])
        self.assertIsNone(review_live_request(
            config, self._owners(self.document, self.part, self.line),
        ))
        self.assertIsNone(review_document_endpoint(self.document, config))
        opener = _Opener()
        code, line = self._live(config, _recognize(self.document, self.part, self.line), opener)
        self.assertEqual(opener.calls, [REMOTE[0] + "v1/recognize"])
        self.assertEqual((code, line), (0, "OK: external HTR live check reference:model-a results=1"))
        self.assertEqual(ExternalHTRJob.objects.count(), 0)
        self.assertEqual(LineTranscription.objects.count(), 0)

    def test_boolean_false_allows_a_remote_endpoint(self):
        policy = AIDocumentPolicy.objects.create(
            document=self.document, never_send_offsite=False,
        )
        policy.refresh_from_db()
        self.assertIs(policy.never_send_offsite, False)
        config = self._config(REMOTE[0])
        self.assertIsNone(review_live_request(
            config, self._owners(self.document, self.part, self.line),
        ))
        self.assertIsNone(review_document_endpoint(self.document, config))
        opener = _Opener()
        code, line = self._live(config, _recognize(self.document, self.part, self.line), opener)
        self.assertEqual(opener.calls, [REMOTE[0] + "v1/recognize"])
        self.assertEqual((code, line), (0, "OK: external HTR live check reference:model-a results=1"))
        self.assertEqual(ExternalHTRJob.objects.count(), 0)
        self.assertEqual(LineTranscription.objects.count(), 0)

    def test_boolean_true_blocks_remote_and_private_endpoints(self):
        policy = AIDocumentPolicy.objects.create(
            document=self.document, never_send_offsite=True,
        )
        policy.refresh_from_db()
        self.assertIs(policy.never_send_offsite, True)
        for endpoint in REMOTE:
            with self.subTest(endpoint=endpoint):
                config = self._config(endpoint)
                self.assertEqual(
                    review_live_request(
                        config, self._owners(self.document, self.part, self.line),
                    ),
                    CODE_OFFSITE,
                )
                self.assertEqual(
                    review_document_endpoint(self.document, config),
                    CODE_OFFSITE,
                )
                opener = _Opener()
                code, line = self._live(
                    config,
                    _recognize(self.document, self.part, self.line),
                    opener,
                )
                self.assertEqual(opener.calls, [])
                self.assertEqual(code, 1)
                self.assertIn(CODE_OFFSITE, line)
                self.assertNotIn(endpoint, line)

    def test_boolean_true_permits_loopback(self):
        AIDocumentPolicy.objects.create(
            document=self.document, never_send_offsite=True,
        )
        for endpoint in LOOPBACK:
            with self.subTest(endpoint=endpoint):
                config = self._config(endpoint)
                self.assertIsNone(review_live_request(
                    config, self._owners(self.document, self.part, self.line),
                ))
                self.assertIsNone(review_document_endpoint(self.document, config))
                opener = _Opener()
                code, line = self._live(
                    config,
                    _recognize(self.document, self.part, self.line),
                    opener,
                )
                self.assertEqual(opener.calls, [endpoint + "v1/recognize"])
                self.assertEqual(
                    (code, line),
                    (0, "OK: external HTR live check reference:model-a results=1"),
                )
                self.assertEqual(LineTranscription.objects.count(), 0)

    def test_mismatched_ids_fail_before_the_network_call(self):
        AIDocumentPolicy.objects.create(
            document=self.document, never_send_offsite=False,
        )
        config = self._config(REMOTE[0])
        missing_part = (
            DocumentPart.objects.order_by("-pk").values_list("pk", flat=True).first()
            + 1000
        )
        cases = {
            "document": self._owners(self.other_document, self.part, self.line),
            "part": {
                "document_id": self.other_document.pk,
                "part_id": self.other_part.pk,
                "lines": [{"line_id": self.line.pk}],
            },
            "missing-part": {
                "document_id": self.document.pk,
                "part_id": missing_part,
                "lines": [{"line_id": self.line.pk}],
            },
            "foreign-line": self._owners(
                self.document, self.part, self.line, line_id=self.other_line.pk,
            ),
            "external-id": self._owners(
                self.document, self.part, self.line, line_id=self.line.external_id,
            ),
        }
        for name, payload in cases.items():
            with self.subTest(name=name):
                self.assertEqual(review_live_request(config, payload), CODE_INVALID)
                body = _recognize(self.document, self.part, self.line)
                body["document_id"] = str(payload["document_id"])
                body["part_id"] = str(payload["part_id"])
                body["lines"][0]["line_id"] = str(payload["lines"][0]["line_id"])
                opener = _Opener()
                code, line = self._live(config, body, opener)
                self.assertEqual(opener.calls, [])
                self.assertEqual((code, line), (1, "FAILED: external HTR live check invalid_request"))
                self.assertEqual(ExternalHTRJob.objects.count(), 0)
                self.assertEqual(LineTranscription.objects.count(), 0)

    def test_audit_rejection_stops_early_or_persists_the_failure(self):
        policy = AIDocumentPolicy.objects.create(
            document=self.document, never_send_offsite=True,
        )
        remote = self._config(REMOTE[0])
        transcriptions = LineTranscription.objects.count()
        opened = []

        def open_image(part):
            opened.append(part.pk)
            return Image.new("RGB", (60, 60))

        opener = _Opener()
        with patch(
            "ai.htr_engine_client.urllib.request.build_opener",
            return_value=opener,
        ), patch(
            "ai.htr_engine_client.urllib.request.urlopen",
            side_effect=AssertionError("urlopen"),
        ):
            code, line = execute_external_htr_audit(
                remote.pk,
                self.part.pk,
                model_id="model-a",
                engine="reference",
                fetch_config=_load_config,
                fetch_part=_load_part,
                open_image=open_image,
                fetch_policy=review_document_endpoint,
            )
        self.assertEqual(opened, [])
        self.assertEqual(opener.calls, [])
        self.assertEqual((code, line), (1, "FAILED: external HTR audit offsite_forbidden"))
        self.assertEqual(ExternalHTRJob.objects.count(), 0)

        policy.never_send_offsite = False
        policy.save()
        policy.refresh_from_db()
        self.assertIs(policy.never_send_offsite, False)

        def flip_then_open(part):
            opened.append(part.pk)
            row = AIDocumentPolicy.objects.get(pk=policy.pk)
            self.assertIs(row.never_send_offsite, False)
            row.never_send_offsite = True
            row.save()
            return Image.new("RGB", (60, 60))

        opener = _Opener()
        with patch(
            "ai.htr_engine_client.urllib.request.build_opener",
            return_value=opener,
        ), patch(
            "ai.htr_engine_client.urllib.request.urlopen",
            side_effect=AssertionError("urlopen"),
        ):
            code, line = execute_external_htr_audit(
                remote.pk,
                self.part.pk,
                model_id="model-a",
                engine="reference",
                fetch_config=_load_config,
                fetch_part=_load_part,
                open_image=flip_then_open,
                fetch_policy=review_document_endpoint,
            )
        self.assertEqual(opened, [self.part.pk])
        self.assertEqual(opener.calls, [])
        self.assertEqual((code, line), (1, "FAILED: external HTR audit offsite_forbidden"))
        job = ExternalHTRJob.objects.get()
        self.assertEqual(job.status, ExternalHTRJob.STATUS_FAILED)
        self.assertEqual(job.code, CODE_OFFSITE)
        self.assertEqual(job.message, "document forbids an off-site engine")
        self.assertNotIn(remote.endpoint_url, job.message)
        self.assertEqual(LineTranscription.objects.count(), transcriptions)
        self.assertIs(
            AIDocumentPolicy.objects.get(pk=policy.pk).never_send_offsite,
            True,
        )
