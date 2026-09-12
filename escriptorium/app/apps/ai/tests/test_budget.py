"""Budget + key precedence. No Django."""
import unittest

from ai.budget import budget_allows, remaining
from ai.secrets import decrypt_key, encrypt_key, pick_api_key


class BudgetTests(unittest.TestCase):
    def test_unlimited_when_cap_missing(self):
        self.assertTrue(budget_allows(999, None, 10))
        self.assertTrue(budget_allows(999, -1, 10))
        self.assertIsNone(remaining(10, None))

    def test_blocks_when_spent_plus_est_exceeds_cap(self):
        self.assertTrue(budget_allows(8, 10, 1))
        self.assertFalse(budget_allows(8, 10, 3))
        self.assertEqual(remaining(8, 10), 2)

    def test_zero_cap_blocks(self):
        self.assertFalse(budget_allows(0, 0, 0.01))


class KeyPrecedenceTests(unittest.TestCase):
    def test_local_needs_no_key(self):
        self.assertIsNone(pick_api_key(
            is_local=True, user_key="u", env_key="e", default_key="d"))

    def test_user_beats_env_beats_default(self):
        self.assertEqual(pick_api_key(
            is_local=False, user_key="u", env_key="e", default_key="d"), "u")
        self.assertEqual(pick_api_key(
            is_local=False, user_key=None, env_key="e", default_key="d"), "e")
        self.assertEqual(pick_api_key(
            is_local=False, user_key=None, env_key=None, default_key="d"), "d")
        self.assertIsNone(pick_api_key(
            is_local=False, user_key=None, env_key=None, default_key=None))


class EncryptTests(unittest.TestCase):
    def test_roundtrip(self):
        token = encrypt_key("sk-test", "secret-for-tests")
        self.assertNotIn(b"sk-test", token)
        self.assertEqual(decrypt_key(token, "secret-for-tests"), "sk-test")

    def test_wrong_secret_fails(self):
        token = encrypt_key("sk-test", "secret-a")
        with self.assertRaises(Exception):
            decrypt_key(token, "secret-b")
