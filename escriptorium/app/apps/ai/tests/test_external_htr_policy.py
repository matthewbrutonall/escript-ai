"""Off-site policy for external HTR. No Django, no network, no DNS.

The decision tests call ``review_loaded`` with fake loaders. They do not
boot the ORM. ``review_live_request`` and ``review_document_endpoint`` are
executed in ``ai.django_integration_tests`` when Django and a database
are available.
"""
import ast
import unittest
from pathlib import Path
from types import SimpleNamespace

from ai.external_htr_policy import (
    CODE_INVALID,
    CODE_OFFSITE,
    CODE_POLICY,
    literal_local,
    owners_match,
    policy_verdict,
    request_owners,
    review_loaded,
)
from ai.htr_engine_client import EngineClientError, join_engine_url

AI_DIR = Path(__file__).resolve().parents[1]
POLICY = AI_DIR / "external_htr_policy.py"
CLIENT = AI_DIR / "htr_engine_client.py"
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
QUIET = (
    "management/commands/plan_external_htr.py",
    "external_htr_plan_command.py",
    "external_htr_service.py",
    "external_htr_export.py",
    "management/commands/export_external_htr_request.py",
)


class LocalTests(unittest.TestCase):
    def test_literal_loopback_and_localhost_are_local(self):
        for url in (
            "http://127.0.0.1:8766",
            "http://127.0.0.2/",
            "https://127.255.255.255/base",
            "http://[::1]:8766/",
            "http://localhost:8766",
            "http://LOCALHOST/v1",
        ):
            self.assertTrue(literal_local(url), url)

    def test_other_destinations_are_off_site_without_dns(self):
        for url in (
            "http://10.0.0.8:8766",
            "http://192.168.1.9/",
            "http://172.17.0.2:8766",
            "http://pylaia-smoke:8766",
            "http://0.0.0.0:8766",
            "http://[::ffff:127.0.0.1]/",
            "http://[::ffff:7f00:1]/",
            "http://[0:0:0:0:0:ffff:127.0.0.1]/",
            "http://localhost.example.com/",
            "http://127.0.0.1.nip.io/",
            "http://user:pass@127.0.0.1/",
            "http://127.0.0.1/?token=secret",
            "ftp://127.0.0.1/",
            "http://127.0.0.1/#frag",
        ):
            self.assertFalse(literal_local(url), url)

    def test_classifier_does_not_resolve_names(self):
        text = POLICY.read_text()
        for banned in ("getaddrinfo", "gethostbyname", "socket", "urlopen"):
            self.assertNotIn(banned, text, banned)

    def test_rejected_urls_match_the_client_rule(self):
        for url in (
            "http://user:pass@127.0.0.1/",
            "http://127.0.0.1/?q=1",
            "http://127.0.0.1/#x",
            "ftp://127.0.0.1/",
            "",
        ):
            with self.assertRaises(EngineClientError):
                join_engine_url(url, "/v1/recognize")
            self.assertFalse(literal_local(url), url)
        join_engine_url("http://127.0.0.1:8766", "/v1/recognize")
        join_engine_url("http://10.0.0.8:8766", "/v1/recognize")


class VerdictTests(unittest.TestCase):
    def test_missing_or_false_policy_allows_a_remote_endpoint(self):
        remote = "http://10.0.0.8:9/secret"
        self.assertIsNone(policy_verdict(None, remote))
        self.assertIsNone(policy_verdict(SimpleNamespace(never_send_offsite=False), remote))

    def test_true_policy_allows_only_a_literal_local_endpoint(self):
        policy = SimpleNamespace(never_send_offsite=True)
        self.assertIsNone(policy_verdict(policy, "http://127.0.0.1:8766"))
        self.assertIsNone(policy_verdict(policy, "http://localhost:8766"))
        self.assertEqual(policy_verdict(policy, "http://10.0.0.8:9/secret"), CODE_OFFSITE)
        self.assertEqual(policy_verdict(policy, "http://pylaia-smoke:8766"), CODE_OFFSITE)
        self.assertEqual(policy_verdict(policy, "not a url"), CODE_OFFSITE)

    def test_non_bool_flag_fails_closed(self):
        for flag in (1, "yes", None):
            policy = SimpleNamespace(never_send_offsite=flag)
            self.assertEqual(
                policy_verdict(policy, "http://127.0.0.1:8766"),
                CODE_POLICY,
                flag,
            )


class OwnershipTests(unittest.TestCase):
    def test_request_ids_are_normalized_without_reading_images(self):
        payload = {
            "document_id": 7,
            "part_id": "8",
            "lines": [{"line_id": 10, "image": "QUJDRA=="}],
            "title": "Secret Title",
        }
        self.assertEqual(request_owners(payload), ("7", "8", ("10",)))

    def test_bad_identity_is_rejected(self):
        good = {"document_id": 7, "part_id": 8, "lines": [{"line_id": "10"}]}
        self.assertIsNone(request_owners({**good, "document_id": True}))
        self.assertIsNone(request_owners({**good, "part_id": -1}))
        self.assertIsNone(request_owners({**good, "lines": []}))
        self.assertIsNone(request_owners({**good, "lines": [{"line_id": "10"}, {"line_id": "10"}]}))
        self.assertIsNone(request_owners({"document_id": 7, "part_id": 8}))

    def test_ownership_requires_every_line(self):
        self.assertTrue(owners_match("7", "8", ("10",), document_pk=7, part_pk=8, line_pks=[10]))
        self.assertFalse(owners_match("7", "8", ("10",), document_pk=99, part_pk=8, line_pks=[10]))
        self.assertFalse(owners_match("7", "8", ("10",), document_pk=7, part_pk=4, line_pks=[10]))
        self.assertFalse(owners_match("7", "8", ("10", "11"), document_pk=7, part_pk=8, line_pks=[10]))


class ReviewLoadedTests(unittest.TestCase):
    """Fake database records. This is not an ORM test."""

    def test_restricted_remote_and_local_and_unrestricted(self):
        request = {"document_id": 7, "part_id": 8, "lines": [{"line_id": "10", "image": "QUJDRA=="}]}
        remote = SimpleNamespace(endpoint_url="http://10.0.0.8:9/secret-lan")
        local = SimpleNamespace(endpoint_url="http://127.0.0.1:8766")
        self.assertEqual(self._review(remote, True, request), CODE_OFFSITE)
        self.assertIsNone(self._review(local, True, request))
        self.assertIsNone(self._review(remote, False, request))
        self.assertIsNone(self._review(remote, None, request))

    def test_bad_records_and_policy_errors_fail_closed(self):
        request = {"document_id": 7, "part_id": 8, "lines": [{"line_id": "10"}]}
        config = SimpleNamespace(endpoint_url="http://127.0.0.1:8766")
        self.assertEqual(self._review(config, True, request, document_pk=99), CODE_INVALID)
        self.assertEqual(self._review(config, True, request, line_pks=[11]), CODE_INVALID)
        self.assertEqual(self._review(config, True, request, missing=True), CODE_INVALID)
        self.assertEqual(self._review(config, True, request, policy_error=True), CODE_POLICY)
        self.assertEqual(review_loaded(
            config,
            {"document_id": True, "part_id": 8, "lines": [{"line_id": "10"}]},
            load_part=self._boom,
            load_line_pks=self._boom,
            load_policy=self._boom,
        ), CODE_INVALID)

    def _review(self, config, flag, request, *, document_pk=7, line_pks=None, missing=False, policy_error=False):
        def load_part(part_id):
            if missing:
                raise LookupError("secret path")
            return SimpleNamespace(pk=8, document_id=document_pk)

        def load_lines(part):
            return [10] if line_pks is None else line_pks

        def load_policy(part):
            if policy_error:
                raise RuntimeError("secret endpoint")
            if flag is None:
                return None
            return SimpleNamespace(never_send_offsite=flag)

        return review_loaded(
            config,
            request,
            load_part=load_part,
            load_line_pks=load_lines,
            load_policy=load_policy,
        )

    def _boom(self, *args, **kwargs):
        raise AssertionError("loader called")


class AdapterSourceTests(unittest.TestCase):
    def test_django_adapters_are_not_executed_without_django(self):
        orm = AI_DIR / "django_integration_tests" / "test_external_htr_policy_orm.py"
        try:
            import django  # noqa: F401
        except ModuleNotFoundError:
            self.skipTest(
                "Django is not installed. ORM lookups run in "
                "ai.django_integration_tests.test_external_htr_policy_orm."
            )
        self.assertTrue(orm.is_file())
        text = orm.read_text()
        self.assertIn("review_live_request", text)
        self.assertIn("review_document_endpoint", text)
        self.assertIn("execute_external_htr_audit", text)

    def test_adapters_query_trusted_rows_and_commands_use_them(self):
        text = POLICY.read_text()
        self.assertIn("DocumentPart.objects.only", text)
        self.assertIn("Line.objects.filter", text)
        self.assertIn("AIDocumentPolicy.objects.filter", text)
        self.assertIn("return review_loaded(", text)
        audit = (AI_DIR / "management" / "commands" / "run_external_htr_audit.py").read_text()
        live = (AI_DIR / "management" / "commands" / "check_external_htr_live.py").read_text()
        self.assertIn("fetch_policy=review_document_endpoint", audit)
        self.assertIn("review_request=review_live_request", live)
        client = CLIENT.read_text()
        self.assertIn("ProxyHandler({})", client)
        self.assertIn("def redirect_request", client)
        for name in QUIET:
            other = (AI_DIR / name).read_text()
            self.assertNotIn("external_htr_policy", other, name)
            self.assertNotIn("recognize_lines", other, name)
        for name in RUNTIME:
            other = (AI_DIR / name).read_text()
            self.assertNotIn("external_htr_policy", other, name)

    def test_policy_module_imports_no_client(self):
        tree = ast.parse(POLICY.read_text())
        modules = []
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
            elif isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
        self.assertEqual(modules, ["__future__", "ipaddress", "urllib.parse"])


if __name__ == "__main__":
    unittest.main()
