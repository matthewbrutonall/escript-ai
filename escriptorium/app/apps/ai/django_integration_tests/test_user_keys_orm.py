"""Stored credentials fail closed. Hosted provider calls never run."""
import os
import json
from types import SimpleNamespace
from unittest.mock import patch

from django.db import OperationalError
from django.test import TestCase, override_settings

from ai.admin import AIUserKeyForm
from ai.dispatch import APIKeyUnavailable, assert_dispatch_allowed, resolve_api_key
from ai.models import AIBackendConfig, AIJob, AILayerGate, AIUsageLedger, AIUserKey, AIUserQuota
from ai.serializers import AITranscribeSerializer
from api.serializers import CollectionRecognizeSerializer
from ai.secrets import encrypt_key
from ai.tasks import ai_seg_review, ai_transcribe
from core.models import Line, OcrModel, Transcription, VirtualCollection, VirtualCollectionItem
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

    def test_estimate_and_transcribe_validation_refuse_over_budget_without_creating_jobs(self):
        document = self.factory.make_document(owner=self.user)
        part = self.factory.make_part(document=document)
        transcription = Transcription.objects.create(document=document, name='budget test')
        self.factory.make_content(part, amount=1, transcription=transcription)
        self.assertTrue(Line.objects.filter(document_part=part).exists())
        AIUserQuota.objects.create(user=self.user, monthly_usd=0.000000001)
        jobs_before = AIJob.objects.count()
        context = {
            'view': SimpleNamespace(request=SimpleNamespace(user=self.user)),
            'document': document,
        }
        serializer = AITranscribeSerializer(
            data={'backend': self.config.pk, 'parts': [part.pk]}, context=context)
        self.assertFalse(serializer.is_valid())
        self.assertIn('budget', str(serializer.errors).lower())
        self.assertEqual(AIJob.objects.count(), jobs_before)

        self.client.force_login(self.user)
        response = self.client.post(
            '/api/documents/%s/ai_estimate/' % document.pk,
            data={'backend': self.config.pk, 'parts': [part.pk]},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(response.json()['allowed'])
        self.assertEqual(response.json()['reason_code'], 'budget')
        self.assertEqual(AIJob.objects.count(), jobs_before)
        self.assertEqual(AIUsageLedger.objects.count(), 0)

    def test_collection_training_validation_blocks_raw_ai_layer_before_model_creation(self):
        document = self.factory.make_document(owner=self.user)
        part = self.factory.make_part(document=document)
        layer = Transcription.objects.create(document=document, name='unreviewed AI layer')
        AILayerGate.objects.create(transcription=layer, state=AILayerGate.STATE_RAW)
        collection = VirtualCollection.objects.create(name='review gate', owner=self.user)
        VirtualCollectionItem.objects.create(
            collection=collection, document_part=part, transcription_layer=layer)
        models_before = OcrModel.objects.count()
        serializer = CollectionRecognizeSerializer(
            data={'model_name': 'must not be created'},
            context={'request': SimpleNamespace(user=self.user), 'collection': collection},
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn('not been reviewed', str(serializer.errors))
        self.assertEqual(OcrModel.objects.count(), models_before)

    def test_partial_segmentation_review_failure_is_visible_and_keeps_serializable_result(self):
        document = self.factory.make_document(owner=self.user)
        first = self.factory.make_part(document=document)
        second = self.factory.make_part(document=document)
        job = AIJob.objects.create(
            backend=self.config, document=document, created_by=self.user,
            mode=AIJob.MODE_SEG_REVIEW,
        )
        partial_message = 'AI segmentation review finished with some pages unavailable. Check the page results before relying on the suggestions.'
        outcomes = [
            {'error': RuntimeError('first page provider failure'), 'suggestions': [], 'cost': 0, 'tokens_in': 0, 'tokens_out': 0},
            {'error': None, 'suggestions': [{'kind': 'missed', 'line': None, 'payload': {}}], 'cost': 0, 'tokens_in': 0, 'tokens_out': 0},
        ]
        notices = []
        with patch('ai.tasks.get_backend', return_value=object()), \
                patch('ai.pipeline.review_part_segmentation', side_effect=outcomes), \
                patch('ai.tasks.notify_user', side_effect=lambda _user, message, **kwargs: notices.append((message, kwargs))):
            result = ai_seg_review.apply(kwargs={
                'instance_pks': [first.pk, second.pk], 'ai_config_pk': self.config.pk,
                'user_pk': self.user.pk, 'job_pk': job.pk,
            }, throw=False)
        self.assertEqual(result.state, 'SUCCESS')
        json.dumps(result.result)
        job.refresh_from_db()
        self.assertEqual(job.status, job.STATUS_DONE)
        self.assertEqual(job.error_code, 'partial_failure')
        self.assertEqual(job.error_public, partial_message)
        self.assertEqual(notices[-1][0], partial_message)
        self.assertEqual(notices[-1][1]['level'], 'warning')

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
