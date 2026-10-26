"""Stored credentials fail closed. Hosted provider calls never run."""
import os
from types import SimpleNamespace
from unittest.mock import patch

from django.db import OperationalError
from django.test import TestCase, override_settings

from ai.admin import AIUserKeyForm
from ai.dispatch import APIKeyUnavailable, assert_dispatch_allowed, resolve_api_key
from ai.models import AIBackendConfig, AIJob, AIUserKey
from ai.secrets import encrypt_key
from ai.tasks import ai_transcribe
from core.models import Transcription
from core.tests.factory import CoreFactory


@override_settings(SECRET_KEY='private-test-encryption-secret', AI_DEFAULT_API_KEY='shared-test-key')
class UserKeyTests(TestCase):
    def setUp(self):
        self.factory = CoreFactory()
        self.user = self.factory.make_user()
        self.config = AIBackendConfig.objects.create(
            name='key-test', provider='openai', model_id='test-model',
            key_ref='ESCRIPT_TEST_ONLY_KEY', prompt_template='test')
        self.environment = patch.dict(os.environ, {'ESCRIPT_TEST_ONLY_KEY': 'environment-test-key'})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.http = patch('requests.post', side_effect=AssertionError('unexpected provider call'))
        self.http.start()
        self.addCleanup(self.http.stop)

    def store(self, plaintext='user-test-key'):
        return AIUserKey.objects.create(
            user=self.user, provider='openai',
            ciphertext=encrypt_key(plaintext, 'private-test-encryption-secret'))

    def test_user_key_precedes_shared_keys(self):
        self.store()
        self.assertEqual(resolve_api_key(self.config, self.user), 'user-test-key')

    def test_absent_user_key_keeps_environment_then_default_precedence(self):
        self.assertEqual(resolve_api_key(self.config, self.user), 'environment-test-key')
        with patch.dict(os.environ, {'ESCRIPT_TEST_ONLY_KEY': ''}):
            self.assertEqual(resolve_api_key(self.config, self.user), 'shared-test-key')

    def test_rotated_secret_never_falls_back_and_hides_original_error(self):
        self.store()
        with override_settings(SECRET_KEY='rotated-private-test-secret'):
            with self.assertRaises(APIKeyUnavailable) as caught:
                resolve_api_key(self.config, self.user)
        self.assertTrue(caught.exception.__suppress_context__)
        self.assertNotIn('user-test-key', str(caught.exception))

    def test_corrupt_empty_and_whitespace_keys_never_fall_back(self):
        for ciphertext in (b'corrupt-secret-token', b'', encrypt_key('   ', 'private-test-encryption-secret')):
            with self.subTest(ciphertext=bool(ciphertext)):
                AIUserKey.objects.update_or_create(
                    user=self.user, provider='openai', defaults={'ciphertext': ciphertext})
                with self.assertRaises(APIKeyUnavailable):
                    resolve_api_key(self.config, self.user)

    def test_lookup_failure_is_not_treated_as_absent_key(self):
        with patch.object(AIUserKey.objects, 'filter', side_effect=OperationalError('sensitive-db-error')):
            with self.assertRaises(APIKeyUnavailable) as caught:
                resolve_api_key(self.config, self.user)
        self.assertNotIn('sensitive-db-error', str(caught.exception))

    def test_local_and_anonymous_resolution_do_not_load_user_keys(self):
        local = SimpleNamespace(provider='local', is_local=True)
        with patch.object(AIUserKey.objects, 'filter', side_effect=AssertionError('queried keys')):
            self.assertIsNone(resolve_api_key(local, self.user))
            self.assertEqual(resolve_api_key(self.config), 'environment-test-key')

    def test_dispatch_stops_before_budget_or_backend_use(self):
        self.store()
        with override_settings(SECRET_KEY='rotated-private-test-secret'):
            with patch('ai.dispatch.check_budget', side_effect=AssertionError('checked budget')):
                with self.assertRaises(APIKeyUnavailable):
                    assert_dispatch_allowed(SimpleNamespace(pk=1), self.config, self.user,
                                            get_policy=lambda document: None)

    def test_task_records_fixed_failure_before_backend_is_constructed(self):
        document = self.factory.make_document(owner=self.user)
        layer = Transcription.objects.create(document=document, name='key-test')
        job = AIJob.objects.create(backend=self.config, document=document, created_by=self.user,
                                   transcription=layer)
        self.store()
        with override_settings(SECRET_KEY='rotated-private-test-secret'):
            with patch('ai.tasks.get_backend', side_effect=AssertionError('built backend')):
                result = ai_transcribe.apply(kwargs={
                    'instance_pks': [], 'ai_config_pk': self.config.pk,
                    'transcription_pk': layer.pk, 'user_pk': self.user.pk, 'job_pk': job.pk,
                }, throw=False)
        self.assertEqual(result.state, 'FAILURE')
        self.assertIsInstance(result.result, APIKeyUnavailable)
        job.refresh_from_db()
        self.assertEqual(job.status, job.STATUS_ERROR)
        self.assertEqual(job.error, str(result.result))
        self.assertNotIn('user-test-key', job.error)

    @override_settings(SECRET_KEY='a-beautiful-snowflake')
    def test_admin_refuses_new_keys_with_default_secret(self):
        form = AIUserKeyForm(data={
            'user': self.user.pk, 'provider': 'openai', 'plaintext': 'test-key'})
        self.assertFalse(form.is_valid())
        self.assertIn('SECRET_KEY', form.errors['plaintext'][0])
        self.assertFalse(AIUserKey.objects.exists())

    def test_admin_secure_storage_and_blank_edit_preserve_ciphertext(self):
        form = AIUserKeyForm(data={
            'user': self.user.pk, 'provider': 'openai', 'plaintext': 'user-test-key'})
        self.assertTrue(form.is_valid(), form.errors)
        row = form.save()
        original = bytes(row.ciphertext)
        self.assertNotEqual(original, b'user-test-key')
        self.assertEqual(resolve_api_key(self.config, self.user), 'user-test-key')
        form = AIUserKeyForm(instance=row, data={
            'user': self.user.pk, 'provider': 'openai', 'plaintext': ''})
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        row.refresh_from_db()
        self.assertEqual(bytes(row.ciphertext), original)
