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
                budget_ok=False)
        self.assertIn("budget", str(ctx.exception))

    def test_remote_allowed_with_key_and_no_policy(self):
        cfg = SimpleNamespace(provider="gemini", is_local=False,
                              model_id="gemini-2.5-flash", key_ref="K")
        assert_dispatch_allowed(
            SimpleNamespace(pk=7), cfg,
            get_policy=lambda doc: None,
            get_api_key=lambda c: "secret")


if __name__ == "__main__":
    unittest.main()
