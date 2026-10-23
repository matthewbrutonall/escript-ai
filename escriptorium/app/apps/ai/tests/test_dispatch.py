"""Dispatch guards — no Django, no Celery, no network."""
import unittest
from types import SimpleNamespace

from ai.dispatch import (
    CrossDocumentError,
    RemoteAIForbidden,
    assert_dispatch_allowed,
    assert_parts_belong,
    load_parts_for_transcription,
)


class Missing(LookupError):
    pass


def _part(pk, doc_id):
    return SimpleNamespace(pk=pk, document_id=doc_id)


class LoadPartsTests(unittest.TestCase):
    def setUp(self):
        self.parts = {1: _part(1, 10), 2: _part(2, 10), 9: _part(9, 99)}
        self.trans = SimpleNamespace(pk=5, document_id=10)

    def get_part(self, pk):
        if pk not in self.parts:
            raise Missing(pk)
        return self.parts[pk]

    def test_keeps_matching_parts(self):
        got = load_parts_for_transcription(
            [1, 2], self.trans, get_part=self.get_part, missing=Missing)
        self.assertEqual([p.pk for p in got], [1, 2])

    def test_skips_missing_parts(self):
        got = load_parts_for_transcription(
            [1, 404], self.trans, get_part=self.get_part, missing=Missing)
        self.assertEqual([p.pk for p in got], [1])

    def test_assert_parts_belong_rejects_foreign_part(self):
        with self.assertRaises(CrossDocumentError):
            assert_parts_belong(
                SimpleNamespace(pk=10),
                [_part(1, 10), _part(9, 99)])

    def test_assert_parts_belong_accepts_own_parts(self):
        assert_parts_belong(
            SimpleNamespace(pk=10), [_part(1, 10), _part(2, 10)])

    def test_cross_document_raises_before_any_work(self):
        with self.assertRaises(CrossDocumentError) as ctx:
            load_parts_for_transcription(
                [1, 9], self.trans, get_part=self.get_part, missing=Missing)
        self.assertIn("99", str(ctx.exception))
        self.assertIn("10", str(ctx.exception))


class EgressTests(unittest.TestCase):
    def test_local_backend_skips_offsite_and_key_checks(self):
        # No endpoint is configured, so the local default is loopback and a
        # restricted document is allowed. The hosted key check is skipped.
        cfg = SimpleNamespace(provider="local", is_local=True,
                              model_id="llava", key_ref=None)
        assert_dispatch_allowed(
            SimpleNamespace(pk=1), cfg,
            get_policy=lambda doc: SimpleNamespace(never_send_offsite=True),
            get_api_key=lambda c: None)

    def test_remote_blocked_when_policy_forbids(self):
        cfg = SimpleNamespace(provider="gemini", is_local=False,
                              model_id="gemini-2.5-flash", key_ref="K")
        with self.assertRaises(RemoteAIForbidden):
            assert_dispatch_allowed(
                SimpleNamespace(pk=7), cfg,
                get_policy=lambda doc: SimpleNamespace(never_send_offsite=True),
                get_api_key=lambda c: "secret")

    def test_remote_blocked_when_key_missing(self):
        cfg = SimpleNamespace(provider="gemini", is_local=False,
                              model_id="gemini-2.5-flash", key_ref="K")
        with self.assertRaises(RuntimeError) as ctx:
            assert_dispatch_allowed(
                SimpleNamespace(pk=7), cfg,
                get_policy=lambda doc: None,
                get_api_key=lambda c: None)
        self.assertIn("No API key", str(ctx.exception))

    def test_budget_failure_raises(self):
        cfg = SimpleNamespace(provider="local", is_local=True,
                              model_id="llava", key_ref=None)
        with self.assertRaises(RuntimeError) as ctx:
            assert_dispatch_allowed(
                SimpleNamespace(pk=1), cfg,
                user=SimpleNamespace(pk=3),
                get_policy=lambda doc: None,
                budget_ok=False)
        self.assertIn("budget", str(ctx.exception))

    def test_remote_allowed_with_key_and_no_policy(self):
        cfg = SimpleNamespace(provider="gemini", is_local=False,
                              model_id="gemini-2.5-flash", key_ref="K")
        assert_dispatch_allowed(
            SimpleNamespace(pk=7), cfg,
            get_policy=lambda doc: None,
            get_api_key=lambda c: "secret")

    def test_remote_uses_per_user_key(self):
        cfg = SimpleNamespace(provider="gemini", is_local=False,
                              model_id="gemini-2.5-flash", key_ref="K")
        seen = {}

        def key_fn(config, user=None):
            seen['user'] = user
            return "user-secret" if user is not None else None

        user = SimpleNamespace(pk=3)
        assert_dispatch_allowed(
            SimpleNamespace(pk=7), cfg, user=user,
            get_policy=lambda doc: None,
            get_api_key=key_fn,
            budget_ok=True)
        self.assertIs(seen['user'], user)

    def test_remote_blocked_when_user_key_also_missing(self):
        cfg = SimpleNamespace(provider="gemini", is_local=False,
                              model_id="gemini-2.5-flash", key_ref="K")

        def key_fn(config, user=None):
            return None

        with self.assertRaises(RuntimeError) as ctx:
            assert_dispatch_allowed(
                SimpleNamespace(pk=7), cfg,
                user=SimpleNamespace(pk=3),
                get_policy=lambda doc: None,
                get_api_key=key_fn,
                budget_ok=True)
        self.assertIn("No API key", str(ctx.exception))


class LocalEgressTests(unittest.TestCase):
    """provider='local' does not make an arbitrary URL local."""

    forbid = staticmethod(lambda doc: SimpleNamespace(never_send_offsite=True))

    def local(self, endpoint):
        return SimpleNamespace(provider="local", is_local=True,
                               model_id="llava", key_ref=None, endpoint=endpoint)

    def test_restricted_document_refuses_non_loopback_local_endpoint(self):
        from unittest.mock import patch
        for endpoint in ("http://10.0.0.5:11434/v1", "https://api.example.com/v1",
                         "http://gpu-box:8000/v1", "http://0.0.0.0:11434/v1"):
            with patch("requests.post", side_effect=AssertionError("post")), \
                    patch("requests.get", side_effect=AssertionError("get")):
                with self.assertRaises(RemoteAIForbidden, msg=endpoint) as ctx:
                    assert_dispatch_allowed(
                        SimpleNamespace(pk=1), self.local(endpoint),
                        get_policy=self.forbid)
            self.assertNotIn(endpoint, str(ctx.exception), endpoint)

    def test_restricted_document_allows_loopback_local_endpoint(self):
        from unittest.mock import patch
        for endpoint in ("http://127.0.0.1:11434/v1", "http://localhost:8000/v1",
                         "http://[::1]:11434/v1", None):
            with patch("requests.post", side_effect=AssertionError("post")), \
                    patch("requests.get", side_effect=AssertionError("get")):
                assert_dispatch_allowed(
                    SimpleNamespace(pk=1), self.local(endpoint),
                    get_policy=self.forbid)

    def test_unrestricted_document_allows_any_local_endpoint(self):
        from unittest.mock import patch
        endpoint = "http://10.0.0.5:11434/v1"
        with patch("requests.post", side_effect=AssertionError("post")), \
                patch("requests.get", side_effect=AssertionError("get")):
            assert_dispatch_allowed(
                SimpleNamespace(pk=1), self.local(endpoint),
                get_policy=lambda doc: None)
            assert_dispatch_allowed(
                SimpleNamespace(pk=1), self.local(endpoint),
                get_policy=lambda doc: SimpleNamespace(never_send_offsite=False))

    def test_unreadable_flag_fails_closed(self):
        with self.assertRaises(RemoteAIForbidden):
            assert_dispatch_allowed(
                SimpleNamespace(pk=1), self.local("http://127.0.0.1/v1"),
                get_policy=lambda doc: SimpleNamespace(never_send_offsite="yes"))

    def test_mapped_ipv6_and_malformed_endpoints_fail_closed(self):
        from unittest.mock import patch
        bad = (
            "http://[::ffff:127.0.0.1]/",
            "http://[::ffff:7f00:1]/",
            "http://[0:0:0:0:0:ffff:127.0.0.1]/",
            "http://user:pass@127.0.0.1/",
            "http://127.0.0.1/?token=secret",
            "http://127.0.0.1/#frag",
            "ftp://127.0.0.1/",
            "not a url",
            7,
        )
        for endpoint in bad:
            with patch("requests.post", side_effect=AssertionError("post")), \
                    patch("requests.get", side_effect=AssertionError("get")):
                with self.assertRaises(RemoteAIForbidden, msg=repr(endpoint)):
                    assert_dispatch_allowed(
                        SimpleNamespace(pk=1), self.local(endpoint),
                        get_policy=self.forbid)

    def test_classifier_matches_external_htr_policy(self):
        # Runtime code may not import external_htr_policy, so egress.py
        # restates its rule. Both must agree on every case.
        from ai.egress import endpoint_is_loopback
        from ai.external_htr_policy import literal_local
        cases = (
            "http://127.0.0.1:8766", "http://127.0.0.2/",
            "https://127.255.255.255/base", "http://[::1]:8766/",
            "http://localhost:8766", "http://LOCALHOST/v1",
            "http://10.0.0.8:8766", "http://192.168.1.9/",
            "http://172.17.0.2:8766", "http://pylaia-smoke:8766",
            "http://0.0.0.0:8766", "http://[::ffff:127.0.0.1]/",
            "http://[::ffff:7f00:1]/", "http://[0:0:0:0:0:ffff:127.0.0.1]/",
            "http://localhost.example.com/", "http://127.0.0.1.nip.io/",
            "http://user:pass@127.0.0.1/", "http://127.0.0.1/?token=secret",
            "ftp://127.0.0.1/", "http://127.0.0.1/#frag", "", None, 7,
            " http://127.0.0.1/ ",
        )
        for url in cases:
            self.assertEqual(endpoint_is_loopback(url), literal_local(url), url)


if __name__ == "__main__":
    unittest.main()
