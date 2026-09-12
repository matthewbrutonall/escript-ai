"""notify_user must stringify lazy-like objects before the channel layer."""
import unittest
from types import SimpleNamespace

from ai.notify import notify_user


class Lazy:
    def __str__(self):
        return "AI transcription done!"


class NotifyUserTests(unittest.TestCase):
    def test_stringifies_message(self):
        seen = {}

        def notify(msg, **kwargs):
            seen["msg"] = msg
            seen.update(kwargs)

        notify_user(
            SimpleNamespace(notify=notify),
            Lazy(),
            id="ai-transcription-success",
            level="success",
        )
        self.assertIsInstance(seen["msg"], str)
        self.assertEqual(seen["msg"], "AI transcription done!")
        self.assertEqual(seen["id"], "ai-transcription-success")

    def test_skips_missing_user(self):
        notify_user(None, "nope")
