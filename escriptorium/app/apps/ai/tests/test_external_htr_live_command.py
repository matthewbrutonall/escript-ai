"""Operator live check from a request file. No Django, no network, no transcription write."""
import ast
import unittest
from pathlib import Path
from types import SimpleNamespace

from ai.external_htr_apply_plan import ExternalHtrApplyPlan, PlannedLine
from ai.external_htr_live import ExternalHtrLiveResult
from ai.external_htr_policy import CODE_OFFSITE, CODE_POLICY, review_loaded
from ai.external_htr_live_command import (
    CODE_CONFIG,
    CODE_DISABLED,
    CODE_INTERNAL,
    CODE_INVALID,
    CODE_REQUEST,
    ConfigNotFound,
    RequestInvalid,
    RequestUnavailable,
    execute_external_htr_live_check,
)

AI_DIR = Path(__file__).resolve().parents[1]
COMMAND = AI_DIR / "management" / "commands" / "check_external_htr_live.py"
PLAN_COMMAND = AI_DIR / "management" / "commands" / "plan_external_htr.py"
IMAGE = "QUJDRA=="
ENDPOINT = "http://127.0.0.1:9/secret-endpoint"
TOKEN = "secret-token"
TITLE = "Secret Title"
SECRET_PATH = "/tmp/secret-request.json"
RAW = "raw-response-body"
PARSER = "parser exploded at lines[0]"
RUNTIME = (
    "tasks.py",
    "views.py",
    "serializers.py",
    "pipeline.py",
    "backends.py",
    "dispatch.py",
    "models.py",
    "admin.py",
)
UNWIRED = (
    "management/commands/plan_external_htr.py",
    "external_htr_plan_command.py",
    "external_htr_service.py",
    "tasks.py",
)


class _Config:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.pk = 4
        self.name = TITLE
        self.endpoint_url = ENDPOINT
        self.metadata = {"api_key": TOKEN}
        self.title = TITLE
        self.path = SECRET_PATH


def _request():
    return {
        "api_version": "1",
        "job_id": "job-1",
        "document_id": 7,
        "part_id": 8,
        "engine": "pylaia",
        "model_id": "example-model",
        "preprocessing": {},
        "lines": [{
            "line_id": "10",
            "image": IMAGE,
            "text": RAW,
            "baseline": [[0, 2], [10, 2]],
            "mask": [[0, 0], [10, 0], [10, 4], [0, 4]],
        }],
        "title": TITLE,
        "path": SECRET_PATH,
    }


def _plan(count=2):
    return ExternalHtrApplyPlan(
        would_write=True,
        code=None,
        message="response would be written",
        layer_source="external-htr:pylaia:example-model",
        engine="pylaia",
        model_id="example-model",
        model_version="1",
        api_version="1",
        results=tuple(
            PlannedLine(str(index), RAW + TITLE, None, 0, ())
            for index in range(count)
        ),
        result_count=count,
        warning_count=0,
        empty_text_count=0,
    )


def _secrets():
    return (IMAGE, ENDPOINT, TOKEN, TITLE, SECRET_PATH, RAW, PARSER)


def _allow(config, request):
    return None


def _foreign_request():
    payload = _request()
    payload["lines"] = list(payload["lines"]) + [{
        "line_id": "11",
        "image": IMAGE,
    }]
    return payload


def _owners(flag, *, document_pk=7, part_pk=8, line_pks=(10,), missing=False, policy_error=False, request=None):
    payload = _request() if request is None else request

    def review(config, body):
        def load_part(part_id):
            if missing:
                raise LookupError(SECRET_PATH)
            return SimpleNamespace(pk=part_pk, document_id=document_pk)

        def load_lines(part):
            return line_pks

        def load_policy(part):
            if policy_error:
                raise RuntimeError(ENDPOINT + SECRET_PATH)
            if flag is None:
                return None
            return SimpleNamespace(never_send_offsite=flag)

        return review_loaded(
            config,
            body,
            load_part=load_part,
            load_line_pks=load_lines,
            load_policy=load_policy,
        )

    review.request = payload
    return review


class LiveCheckTests(unittest.TestCase):
    def test_success_prints_one_ok_line(self):
        request = _request()
        seen = []

        def read_request(path):
            self.assertEqual(path, SECRET_PATH)
            return request

        def run(config, payload):
            seen.append((config, payload))
            return ExternalHtrLiveResult(True, None, PARSER + RAW, _plan())

        config = _Config()
        code, line = execute_external_htr_live_check(
            4,
            SECRET_PATH,
            fetch_config=lambda config_id: config,
            read_request=read_request,
            review_request=_allow,
            run=run,
        )
        self.assertEqual(code, 0)
        self.assertEqual(line, "OK: external HTR live check pylaia:example-model results=2")
        self.assertEqual(len(seen), 1)
        self.assertIs(seen[0][0], config)
        self.assertIs(seen[0][1], request)
        self._assert_clean(line)

    def test_missing_config_fails_before_the_request(self):
        def fetch_config(config_id):
            raise ConfigNotFound(SECRET_PATH + PARSER)

        code, line = execute_external_htr_live_check(
            4,
            SECRET_PATH,
            fetch_config=fetch_config,
            read_request=self._unused_reader,
            run=self._unused_run,
        )
        self.assertEqual((code, line), (1, "FAILED: external HTR live check " + CODE_CONFIG))
        self._assert_clean(line)

    def test_disabled_config_fails_before_the_live_helper(self):
        for enabled in (False, 1, None):
            code, line = execute_external_htr_live_check(
                4,
                SECRET_PATH,
                fetch_config=lambda config_id: _Config(enabled=enabled),
                read_request=self._unused_reader,
                run=self._unused_run,
            )
            self.assertEqual((code, line), (1, "FAILED: external HTR live check " + CODE_DISABLED))
            self._assert_clean(line)

    def test_missing_and_unreadable_request_are_a_fixed_failure(self):
        for message in (SECRET_PATH, SECRET_PATH + " permission denied " + PARSER):
            def read_request(path, message=message):
                raise RequestUnavailable(message)

            code, line = execute_external_htr_live_check(
                4,
                SECRET_PATH,
                fetch_config=lambda config_id: _Config(),
                read_request=read_request,
                run=self._unused_run,
            )
            self.assertEqual((code, line), (1, "FAILED: external HTR live check " + CODE_REQUEST))
            self._assert_clean(line)

    def test_bad_json_is_a_fixed_failure(self):
        def read_request(path):
            raise RequestInvalid(SECRET_PATH + PARSER + IMAGE + RAW)

        code, line = execute_external_htr_live_check(
            4,
            SECRET_PATH,
            fetch_config=lambda config_id: _Config(),
            read_request=read_request,
            run=self._unused_run,
        )
        self.assertEqual((code, line), (1, "FAILED: external HTR live check " + CODE_INVALID))
        self._assert_clean(line)

        code, line = execute_external_htr_live_check(
            4,
            SECRET_PATH,
            fetch_config=lambda config_id: _Config(),
            read_request=lambda path: [IMAGE, RAW, SECRET_PATH],
            run=self._unused_run,
        )
        self.assertEqual((code, line), (1, "FAILED: external HTR live check " + CODE_INVALID))
        self._assert_clean(line)

    def test_live_helper_failure_is_a_fixed_line(self):
        def run(config, payload):
            return ExternalHtrLiveResult(
                False,
                "http_error",
                ENDPOINT + TOKEN + RAW + SECRET_PATH + PARSER,
                None,
            )

        code, line = execute_external_htr_live_check(
            4,
            SECRET_PATH,
            fetch_config=lambda config_id: _Config(),
            read_request=lambda path: _request(),
            review_request=_allow,
            run=run,
        )
        self.assertEqual((code, line), (1, "FAILED: external HTR live check http_error"))
        self._assert_clean(line)

    def test_unsafe_labels_and_codes_are_not_printed(self):
        plan = _plan(count=1)
        leaked = ExternalHtrApplyPlan(
            would_write=True,
            code=None,
            message=RAW,
            layer_source=ENDPOINT,
            engine=ENDPOINT,
            model_id=SECRET_PATH,
            model_version="1",
            api_version="1",
            results=plan.results,
            result_count=1,
            warning_count=0,
            empty_text_count=0,
        )

        def unsafe(config, payload):
            return ExternalHtrLiveResult(True, None, "ready", leaked)

        code, line = execute_external_htr_live_check(
            4,
            SECRET_PATH,
            fetch_config=lambda config_id: _Config(),
            read_request=lambda path: _request(),
            review_request=_allow,
            run=unsafe,
        )
        self.assertEqual((code, line), (1, "FAILED: external HTR live check " + CODE_INVALID))
        self._assert_clean(line)

        def bad_code(config, payload):
            return ExternalHtrLiveResult(False, ENDPOINT + PARSER, RAW, None)

        code, line = execute_external_htr_live_check(
            4,
            SECRET_PATH,
            fetch_config=lambda config_id: _Config(),
            read_request=lambda path: _request(),
            review_request=_allow,
            run=bad_code,
        )
        self.assertEqual((code, line), (1, "FAILED: external HTR live check " + CODE_INTERNAL))
        self._assert_clean(line)

    def test_restricted_remote_is_blocked_before_the_live_helper(self):
        config = _Config()
        config.endpoint_url = "http://192.168.1.9:9/secret-lan"
        code, line = execute_external_htr_live_check(
            4,
            SECRET_PATH,
            fetch_config=lambda config_id: config,
            read_request=lambda path: _request(),
            review_request=_owners(True),
            run=self._unused_run,
        )
        self.assertEqual((code, line), (1, "FAILED: external HTR live check " + CODE_OFFSITE))
        self._assert_clean(line)
        self.assertNotIn("192.168.1.9", line)

    def test_restricted_loopback_and_localhost_reach_the_live_helper(self):
        for endpoint in ("http://127.0.0.1:8766", "http://localhost:8766", "http://[::1]:8766"):
            seen = []
            config = _Config()
            config.endpoint_url = endpoint

            def run(config, payload, seen=seen):
                seen.append(payload)
                return ExternalHtrLiveResult(True, None, "ready", _plan())

            code, line = execute_external_htr_live_check(
                4,
                SECRET_PATH,
                fetch_config=lambda config_id, config=config: config,
                read_request=lambda path: _request(),
                review_request=_owners(True),
                run=run,
            )
            self.assertEqual(code, 0, endpoint)
            self.assertEqual(len(seen), 1, endpoint)
            self.assertNotIn(endpoint, line)

    def test_unrestricted_remote_still_calls_the_live_helper(self):
        for flag in (False, None):
            seen = []
            config = _Config()
            config.endpoint_url = "http://10.1.2.3:9/secret-lan"

            def run(config, payload, seen=seen):
                seen.append(config)
                return ExternalHtrLiveResult(True, None, "ready", _plan())

            code, line = execute_external_htr_live_check(
                4,
                SECRET_PATH,
                fetch_config=lambda config_id, config=config: config,
                read_request=lambda path: _request(),
                review_request=_owners(flag),
                run=run,
            )
            self.assertEqual(code, 0, flag)
            self.assertEqual(len(seen), 1, flag)
            self._assert_clean(line)

    def test_non_bool_policy_stops_before_the_live_helper(self):
        code, line = execute_external_htr_live_check(
            4,
            SECRET_PATH,
            fetch_config=lambda config_id: _Config(),
            read_request=lambda path: _request(),
            review_request=_owners(1),
            run=self._unused_run,
        )
        self.assertEqual((code, line), (1, "FAILED: external HTR live check " + CODE_POLICY))
        self._assert_clean(line)

    def test_bad_ownership_and_policy_lookup_stop_before_the_live_helper(self):
        cases = (
            (_owners(True, document_pk=99), CODE_INVALID),
            (_owners(True, line_pks=(11,)), CODE_INVALID),
            (_owners(True, missing=True), CODE_INVALID),
            (_owners(True, policy_error=True), CODE_POLICY),
            (_owners(True, request=_foreign_request()), CODE_INVALID),
        )
        for review, expected in cases:
            code, line = execute_external_htr_live_check(
                4,
                SECRET_PATH,
                fetch_config=lambda config_id: _Config(),
                read_request=lambda path, review=review: review.request,
                review_request=review,
                run=self._unused_run,
            )
            self.assertEqual((code, line), (1, "FAILED: external HTR live check " + expected))
            self._assert_clean(line)

    def test_loader_exception_text_is_not_printed(self):
        def fetch_config(config_id):
            raise RuntimeError(ENDPOINT + TOKEN + SECRET_PATH + PARSER)

        code, line = execute_external_htr_live_check(
            4,
            SECRET_PATH,
            fetch_config=fetch_config,
            read_request=self._unused_reader,
            run=self._unused_run,
        )
        self.assertEqual((code, line), (1, "FAILED: external HTR live check " + CODE_INTERNAL))
        self._assert_clean(line)

    def _unused_reader(self, path):
        raise AssertionError("request was read")

    def _unused_run(self, config, payload):
        raise AssertionError("live helper was called")

    def _assert_clean(self, line):
        self.assertNotIn("\n", line)
        self.assertEqual(line.count("\n"), 0)
        for secret in _secrets():
            self.assertNotIn(secret, line, secret)


class IsolationTests(unittest.TestCase):
    def test_helper_does_not_open_documents_or_write(self):
        text = (AI_DIR / "external_htr_live_command.py").read_text()
        tree = ast.parse(text)
        top = []
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module:
                top.append(node.module)
        self.assertEqual(top, ["__future__", "ai.external_htr_live"])
        for banned in (
            "external_htr_audit",
            "record_external_htr_apply",
            "record_external_htr_plan",
            "LineTranscription",
            "ai_transcribe",
            "crop_line",
            "endpoint_url",
            "DocumentPart",
            "external_htr_image",
            "encode_line_image",
            "urllib",
            "requests",
            "django",
            "json",
            "open(",
        ):
            self.assertNotIn(banned, text, banned)

    def test_command_calls_the_live_helper_only(self):
        text = COMMAND.read_text()
        self.assertIn("run_external_htr_live", text)
        self.assertIn("review_request=review_live_request", text)
        self.assertIn("stdout.write", text)
        self.assertIn("sys.exit", text)
        self.assertIn("raise ConfigNotFound from None", text)
        self.assertIn("raise RequestUnavailable from None", text)
        self.assertIn("raise RequestInvalid from None", text)
        self.assertEqual(text.count("stdout.write"), 1)
        for banned in (
            "htr_engine_client",
            "recognize_lines",
            "LineTranscription",
            "ai_transcribe",
            "external_htr_audit",
            "record_external_htr_apply",
            "record_external_htr_plan",
            "crop_line",
            "DocumentPart",
            "external_htr_image",
            "encode_line_image",
            "Image.open",
            "--live",
            "--encode-images",
            "endpoint_url",
        ):
            self.assertNotIn(banned, text, banned)
        tree = ast.parse(text)
        arguments = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                self.assertNotIn(node.attr, {"endpoint_url", "metadata", "filename", "title", "image"})
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "add_argument":
                continue
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    arguments.append(arg.value)
        self.assertEqual(arguments, ["config_id", "request_json_path"])

    def test_plan_command_stays_dry_run_and_stage1_does_not_import_this(self):
        plan = PLAN_COMMAND.read_text()
        self.assertIn("encode_line=no_line_image", plan)
        self.assertIn("dry_run=True", plan)
        self.assertNotIn("--live", plan)
        self.assertNotIn("check_external_htr_live", plan)
        self.assertNotIn("external_htr_live", plan)
        for name in UNWIRED:
            text = (AI_DIR / name).read_text()
            self.assertNotIn("check_external_htr_live", text, name)
            self.assertNotIn("external_htr_live_command", text, name)
            self.assertNotIn("execute_external_htr_live_check", text, name)
        for name in RUNTIME:
            text = (AI_DIR / name).read_text()
            self.assertNotIn("check_external_htr_live", text, name)
            self.assertNotIn("external_htr_live_command", text, name)
            self.assertNotIn("execute_external_htr_live_check", text, name)
            self.assertNotIn("run_external_htr_live", text, name)


if __name__ == "__main__":
    unittest.main()
