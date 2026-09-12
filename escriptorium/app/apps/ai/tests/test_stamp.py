"""ORM stamp helper — no Django. Fake LineTranscription records provenance."""
import unittest
from types import SimpleNamespace


class FakeLT:
    def __init__(self):
        self.content = ""
        self.version_source = "eScriptorium"
        self.version_author = ""
        self.saved = 0

    def save(self):
        self.saved += 1


class FakeLTModel:
    def __init__(self, existing=None):
        self._obj = existing or FakeLT()
        self.created = existing is None
        self.objects = self

    def get_or_create(self, **kwargs):
        return self._obj, self.created


class StampTests(unittest.TestCase):
    def test_new_row_stamps_version_source(self):
        from ai.pipeline import stamp_line_transcription
        model = FakeLTModel()
        lt = stamp_line_transcription(
            line=SimpleNamespace(pk=1),
            transcription=SimpleNamespace(pk=2),
            text="Monday morning",
            version_source="gemini:gemini-2.5-flash",
            author="aitest",
            lt_model=model,
            no_change=Exception,
        )
        self.assertEqual(lt.content, "Monday morning")
        self.assertEqual(lt.version_source, "gemini:gemini-2.5-flash")
        self.assertEqual(lt.version_author, "aitest")
        self.assertEqual(lt.saved, 1)

    def test_existing_row_still_overwrites_source(self):
        from ai.pipeline import stamp_line_transcription
        existing = FakeLT()
        existing.version_source = "eScriptorium"
        existing.new_version = lambda **kw: None
        model = FakeLTModel(existing=existing)
        lt = stamp_line_transcription(
            line=SimpleNamespace(pk=1),
            transcription=SimpleNamespace(pk=2),
            text="updated",
            version_source="gemini:gemini-2.5-flash",
            author="aitest",
            lt_model=model,
            no_change=Exception,
        )
        self.assertEqual(lt.version_source, "gemini:gemini-2.5-flash")
        self.assertEqual(lt.content, "updated")


class PromptTests(unittest.TestCase):
    def test_default_prompt_forbids_metadata(self):
        from ai.pipeline import DEFAULT_PROMPT, build_prompt
        cfg = SimpleNamespace(prompt_template="")
        text = build_prompt(cfg, ["red", "blue"])
        self.assertIn("filenames", DEFAULT_PROMPT)
        self.assertIn("red", text)
        self.assertIn("blue", text)


if __name__ == "__main__":
    unittest.main()
