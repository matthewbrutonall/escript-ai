"""Kraken trainer epoch limits and checkpoint retention.

The tests in ``TrainingLimitTests`` and ``TrainingPathTests`` build a real
``KrakenTrainer`` and replace ``fit``. They do not train. ``PPOCRv6OneEpochSmokeTests``
is the only test that fits a model. It stays skipped unless ``KRAKEN_EPOCH_SMOKE=1``
and ``KRAKEN_PPOCR_SMOKE_MODEL`` point at a PP-OCRv6 safetensors file. Run that
process under an outer timeout so a regression cannot continue into a 100-epoch fit.
"""
import inspect
import os
import shutil
import time
import unittest
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.core.files.base import ContentFile
from django.test import SimpleTestCase, TestCase, TransactionTestCase

from core.models import (
    Line,
    LineTranscription,
    OcrModel,
    Transcription,
    VirtualCollection,
    VirtualCollectionItem,
)
from core.tasks import (
    FrontendFeedback,
    KrakenTrainer,
    TrainingLimitError,
    construct_kraken_trainer,
    make_checkpoint_callback,
    recognition_training_classes,
    record_retained_checkpoint,
    segtrain,
    segtrain_from_collection,
    train,
    train_,
    train_from_collection,
    trainer_epoch_limits,
)
from core.tests.factory import CoreFactory
from kraken.configs.ppocr import PPOCRv6RecognitionTrainingConfig
from kraken.configs.vgsl import BLLASegmentationTrainingConfig, VGSLRecognitionTrainingConfig
from lightning.pytorch.callbacks import EarlyStopping, ModelCheckpoint


def _limits_for(config_cls, **kwargs):
    kwargs.setdefault('batch_size', 1)
    kwargs.setdefault('resize', 'union')
    return trainer_epoch_limits(config_cls(**kwargs))


class TrainingLimitTests(SimpleTestCase):
    """Config semantics and checkpoint callback fields. No database and no fit."""

    def test_fixed_and_early_match_ketos_limits(self):
        ppocr = _limits_for(PPOCRv6RecognitionTrainingConfig)
        self.assertEqual(ppocr, {'max_epochs': 100, 'min_epochs': 0})
        self.assertEqual(
            _limits_for(PPOCRv6RecognitionTrainingConfig, epochs=1),
            {'max_epochs': 1, 'min_epochs': 0},
        )
        self.assertEqual(
            _limits_for(PPOCRv6RecognitionTrainingConfig, epochs=4, min_epochs=2),
            {'max_epochs': 4, 'min_epochs': 2},
        )
        vgsl = _limits_for(VGSLRecognitionTrainingConfig)
        self.assertEqual(vgsl, {'max_epochs': -1, 'min_epochs': 0})
        self.assertEqual(
            _limits_for(VGSLRecognitionTrainingConfig, quit='early', min_epochs=3, lag=4),
            {'max_epochs': -1, 'min_epochs': 3},
        )
        # An explicit epoch count does not cap an early-stopping run.
        self.assertEqual(
            _limits_for(VGSLRecognitionTrainingConfig, quit='early', epochs=6, min_epochs=1, lag=2),
            {'max_epochs': -1, 'min_epochs': 1},
        )
        blla = _limits_for(BLLASegmentationTrainingConfig)
        self.assertEqual(blla, {'max_epochs': 50, 'min_epochs': 0})
        self.assertEqual(
            _limits_for(BLLASegmentationTrainingConfig, quit='early', min_epochs=5, lag=1),
            {'max_epochs': -1, 'min_epochs': 5},
        )

    def test_invalid_limits_raise(self):
        invalid = [
            dict(config=PPOCRv6RecognitionTrainingConfig, epochs=0),
            dict(config=PPOCRv6RecognitionTrainingConfig, epochs=-1),
            dict(config=PPOCRv6RecognitionTrainingConfig, epochs=True),
            dict(config=PPOCRv6RecognitionTrainingConfig, epochs=3, min_epochs=4),
            dict(config=PPOCRv6RecognitionTrainingConfig, min_epochs=True),
            dict(config=VGSLRecognitionTrainingConfig, quit='early', epochs=0),
            dict(config=VGSLRecognitionTrainingConfig, quit='early', lag=-1),
            dict(config=VGSLRecognitionTrainingConfig, quit='early', lag=True),
            dict(config=BLLASegmentationTrainingConfig, quit='never'),
            dict(config=BLLASegmentationTrainingConfig, epochs=2, min_epochs=-1),
        ]
        for item in invalid:
            config = item['config']
            options = {key: value for key, value in item.items() if key != 'config'}
            with self.subTest(config=config.__name__, **options):
                with self.assertRaises(TrainingLimitError):
                    _limits_for(config, **options)

    def test_checkpoint_keeps_one_best_and_no_last(self):
        callback = make_checkpoint_callback('/tmp/escript-epoch-checkpoint-callback')
        self.assertIsInstance(callback, ModelCheckpoint)
        self.assertEqual(callback.save_top_k, 1)
        self.assertFalse(callback.save_last)
        self.assertEqual(callback.monitor, 'val_metric')
        self.assertEqual(callback.mode, 'max')
        self.assertFalse(callback._enable_version_counter)

    def test_only_the_helper_constructs_kraken_trainer(self):
        import core.tasks as tasks

        source = inspect.getsource(tasks)
        self.assertEqual(source.count('KrakenTrainer('), 1)
        for name in ('train_', 'segtrain', 'segtrain_from_collection'):
            body = inspect.getsource(getattr(tasks, name))
            self.assertIn('construct_kraken_trainer(', body)
            self.assertNotIn('KrakenTrainer(', body)
        signature = inspect.signature(KrakenTrainer.__init__)
        self.assertEqual(signature.parameters['max_epochs'].default, 100)
        self.assertEqual(signature.parameters['min_epochs'].default, 5)

    def test_construct_passes_limits_into_the_real_trainer(self):
        # Real KrakenTrainer objects, no fit. Proves Lightning stored the override.
        import tempfile
        fixed_dir = tempfile.mkdtemp(prefix='escript-limit-fixed-')
        early_dir = tempfile.mkdtemp(prefix='escript-limit-early-')
        self.addCleanup(shutil.rmtree, fixed_dir, True)
        self.addCleanup(shutil.rmtree, early_dir, True)
        fixed = PPOCRv6RecognitionTrainingConfig(batch_size=1, resize='union', epochs=1)
        early = VGSLRecognitionTrainingConfig(batch_size=1, resize='union', min_epochs=2, lag=3)
        fixed_trainer = construct_kraken_trainer(
            fixed, accelerator='cpu', device='auto', precision='32',
            callbacks=[make_checkpoint_callback(fixed_dir)])
        early_trainer = construct_kraken_trainer(
            early, accelerator='cpu', device='auto', precision='32',
            callbacks=[make_checkpoint_callback(early_dir)])
        self.assertEqual(fixed_trainer.max_epochs, 1)
        self.assertEqual(fixed_trainer.min_epochs, 0)
        self.assertEqual(early_trainer.max_epochs, -1)
        self.assertEqual(early_trainer.min_epochs, 2)
        self.assertIn('cpu', type(fixed_trainer.accelerator).__name__.lower())
        self.assertFalse(any(isinstance(cb, EarlyStopping) for cb in fixed_trainer.callbacks))
        with patch('core.tasks.KrakenTrainer', side_effect=AssertionError('constructed')):
            with self.assertRaises(TrainingLimitError):
                construct_kraken_trainer(
                    PPOCRv6RecognitionTrainingConfig(batch_size=1, resize='union', epochs=0),
                    accelerator='cpu', device='auto', precision='32', callbacks=[])

    def test_cancel_training_does_not_resume_from_a_checkpoint(self):
        source = inspect.getsource(OcrModel.cancel_training)
        self.assertNotIn('checkpoint', source.lower())
        self.assertNotIn('.fit', source)
        self.assertIn('training = False', source)
        self.assertIsNone(OcrModel.version_history_max_length)


def _fake_fit(state):
    def fit(self, *args, **kwargs):
        state['trainer'] = self
        callback = self.checkpoint_callback
        self.assert_callback = callback
        destination = os.path.join(str(callback.dirpath), 'checkpoint_epoch=0.ckpt')
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        with open(destination, 'wb') as handle:
            handle.write(b'best-checkpoint')
        callback.best_model_path = destination
        callback.best_model_score = 0.42
    return fit


def _fake_convert(state):
    def convert(paths, dest):
        state['order'].append('convert')
        state['convert_exists'] = os.path.isfile(paths[0])
        state['best_path'] = os.path.abspath(paths[0])
        # Leave the checkpoint and the current model file untouched.
    return convert


def _wrap_record(state):
    def wrapped(es_model, best_path):
        state['order'].append('record')
        state['record_exists'] = os.path.isfile(best_path)
        return record_retained_checkpoint(es_model, best_path)
    return wrapped


class TrainingPathTests(TestCase):
    """Drive each application trainer call site. ``fit`` is replaced; arguments are real."""

    def setUp(self):
        self.factory = CoreFactory()

    def _model(self, job, name):
        user = self.factory.make_user()
        document = self.factory.make_document(owner=user)
        part = self.factory.make_part(document=document)
        layer = Transcription.objects.create(document=document, name='layer')
        line = Line.objects.create(
            document_part=part,
            baseline=[[1, 10], [40, 10]],
            mask=[[0, 0], [40, 0], [40, 20], [0, 20]],
        )
        LineTranscription.objects.create(line=line, transcription=layer, content='ab')
        model = OcrModel.objects.create(
            name=name, owner=user, job=job, file_size=12,
        )
        model.file.save(f'{name}.safetensors', ContentFile(b'fake-weights'), save=True)
        self.addCleanup(shutil.rmtree, os.path.dirname(model.file.path), True)
        return user, document, part, layer, model

    def _plant_history(self, model):
        directory = os.path.dirname(model.file.path)
        historical = os.path.join(directory, 'historical.ckpt')
        decoy = os.path.join(directory, 'decoy-unreferenced.ckpt')
        with open(historical, 'wb') as handle:
            handle.write(b'historical')
        with open(decoy, 'wb') as handle:
            handle.write(b'decoy')
        model.new_version(file=os.path.relpath(historical, settings.MEDIA_ROOT))
        model.save()
        return historical, decoy

    def _assert_trainer(self, trainer, max_epochs, min_epochs):
        self.assertIsNotNone(trainer)
        self.assertEqual(trainer.max_epochs, max_epochs)
        self.assertEqual(trainer.min_epochs, min_epochs)
        self.assertIn('cpu', type(trainer.accelerator).__name__.lower())
        checkpoints = [cb for cb in trainer.callbacks if isinstance(cb, ModelCheckpoint)]
        self.assertEqual(len(checkpoints), 1)
        self.assertEqual(checkpoints[0].save_top_k, 1)
        self.assertFalse(checkpoints[0].save_last)
        self.assertEqual(checkpoints[0].monitor, 'val_metric')
        self.assertFalse(any(isinstance(cb, EarlyStopping) for cb in trainer.callbacks))
        self.assertTrue(any(isinstance(cb, FrontendFeedback) for cb in trainer.callbacks))

    def _assert_retained(self, model, state, historical, decoy):
        self.assertEqual(state['order'], ['record', 'convert'])
        self.assertTrue(state['record_exists'])
        self.assertTrue(state['convert_exists'])
        best = state['best_path']
        self.assertTrue(os.path.isfile(best))
        with open(best, 'rb') as handle:
            self.assertEqual(handle.read(), b'best-checkpoint')
        with open(historical, 'rb') as handle:
            self.assertEqual(handle.read(), b'historical')
        with open(decoy, 'rb') as handle:
            self.assertEqual(handle.read(), b'decoy')
        with open(model.file.path, 'rb') as handle:
            self.assertEqual(handle.read(), b'fake-weights')
        model.refresh_from_db()
        files = [(version.get('data') or {}).get('file') for version in model.versions]
        for relative in files:
            self.assertIsNotNone(relative)
            self.assertFalse(relative.startswith('..') or os.path.isabs(relative))
            self.assertTrue(os.path.isfile(os.path.join(settings.MEDIA_ROOT, relative)), relative)
        best_relative = os.path.relpath(best, settings.MEDIA_ROOT)
        historical_relative = os.path.relpath(historical, settings.MEDIA_ROOT)
        decoy_relative = os.path.relpath(decoy, settings.MEDIA_ROOT)
        self.assertIn(best_relative, files)
        self.assertIn(historical_relative, files)
        self.assertNotIn(decoy_relative, files)
        self.assertEqual(files.count(best_relative), 1)

    def _recognition_patches(self, state, architecture):
        class StubRecognitionModel:
            @classmethod
            def load_from_weights(cls, path, config):
                return cls(config)

            def __init__(self, config=None):
                self.config = config

        def classes(found):
            config_cls, *_ignored = recognition_training_classes(found)
            return (config_cls, MagicMock(), MagicMock(), StubRecognitionModel)

        return [
            patch('core.tasks.detect_model_architecture', return_value=architecture),
            patch('core.tasks.recognition_training_classes', side_effect=classes),
            patch('core.tasks.build_binary_dataset'),
            patch('core.tasks.make_recognition_segmentation', return_value=[]),
            patch('core.tasks.convert_models', side_effect=_fake_convert(state)),
            patch('core.tasks.record_retained_checkpoint', side_effect=_wrap_record(state)),
            patch.object(KrakenTrainer, 'fit', _fake_fit(state)),
        ]

    def _segmentation_patches(self, state):
        return [
            patch('core.tasks.BLLASegmentationModel', MagicMock()),
            patch('core.tasks.BLLASegmentationDataModule', MagicMock()),
            patch('core.tasks.make_segmentation_training_data', return_value=[]),
            patch('core.tasks.convert_models', side_effect=_fake_convert(state)),
            patch('core.tasks.record_retained_checkpoint', side_effect=_wrap_record(state)),
            patch.object(KrakenTrainer, 'fit', _fake_fit(state)),
        ]

    def _run_patches(self, patches, call):
        from contextlib import ExitStack
        with ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            call()

    def test_recognition_ppocr_default_fixed_100(self):
        _user, document, _part, layer, model = self._model(OcrModel.MODEL_JOB_RECOGNIZE, 'ppocr-default')
        historical, decoy = self._plant_history(model)
        state = {'order': []}
        queryset = LineTranscription.objects.filter(transcription=layer)
        self._run_patches(
            self._recognition_patches(state, 'PPOCRv6Model'),
            lambda: train_(queryset, document=document, transcription=layer, model=model),
        )
        self._assert_trainer(state['trainer'], 100, 0)
        self._assert_retained(model, state, historical, decoy)

    def test_recognition_ppocr_explicit_one_epoch(self):
        _user, document, _part, layer, model = self._model(OcrModel.MODEL_JOB_RECOGNIZE, 'ppocr-one')
        historical, decoy = self._plant_history(model)
        state = {'order': []}
        queryset = LineTranscription.objects.filter(transcription=layer)
        self._run_patches(
            self._recognition_patches(state, 'PPOCRv6Model'),
            lambda: train_(queryset, document=document, transcription=layer, model=model, epochs=1),
        )
        self._assert_trainer(state['trainer'], 1, 0)
        self._assert_retained(model, state, historical, decoy)

    def test_recognition_vgsl_early_and_fixed_override(self):
        _user, document, _part, layer, model = self._model(OcrModel.MODEL_JOB_RECOGNIZE, 'vgsl-early')
        historical, decoy = self._plant_history(model)
        state = {'order': []}
        queryset = LineTranscription.objects.filter(transcription=layer)
        self._run_patches(
            self._recognition_patches(state, None),
            lambda: train_(queryset, document=document, transcription=layer, model=model),
        )
        self._assert_trainer(state['trainer'], -1, 0)
        self._assert_retained(model, state, historical, decoy)

        state = {'order': []}
        self._run_patches(
            self._recognition_patches(state, None),
            lambda: train_(
                queryset, document=document, transcription=layer, model=model,
                epochs=6, quit_mode='fixed', min_epochs=1,
            ),
        )
        self._assert_trainer(state['trainer'], 6, 1)

    def test_recognition_early_stopping_override(self):
        _user, document, _part, layer, model = self._model(OcrModel.MODEL_JOB_RECOGNIZE, 'ppocr-early')
        state = {'order': []}
        queryset = LineTranscription.objects.filter(transcription=layer)
        self._run_patches(
            self._recognition_patches(state, 'PPOCRv6Model'),
            lambda: train_(
                queryset, document=document, transcription=layer, model=model,
                quit_mode='early', min_epochs=2,
            ),
        )
        self._assert_trainer(state['trainer'], -1, 2)
        self.assertEqual(state['order'][0], 'record')

    def test_document_segmentation_fixed_and_early(self):
        _user, document, part, _layer, model = self._model(OcrModel.MODEL_JOB_SEGMENT, 'seg-doc')
        historical, decoy = self._plant_history(model)
        state = {'order': []}
        self._run_patches(
            self._segmentation_patches(state),
            lambda: segtrain(model_pk=model.pk, part_pks=[part.pk], document_pk=document.pk),
        )
        self._assert_trainer(state['trainer'], 50, 0)
        self._assert_retained(model, state, historical, decoy)

        state = {'order': []}
        self._run_patches(
            self._segmentation_patches(state),
            lambda: segtrain(
                model_pk=model.pk, part_pks=[part.pk], document_pk=document.pk,
                epochs=4, min_epochs=1,
            ),
        )
        self._assert_trainer(state['trainer'], 4, 1)

        state = {'order': []}
        self._run_patches(
            self._segmentation_patches(state),
            lambda: segtrain(
                model_pk=model.pk, part_pks=[part.pk], document_pk=document.pk,
                quit_mode='early', min_epochs=3,
            ),
        )
        self._assert_trainer(state['trainer'], -1, 3)

    def test_collection_segmentation_fixed_and_early(self):
        user, _document, part, layer, model = self._model(OcrModel.MODEL_JOB_SEGMENT, 'seg-coll')
        historical, decoy = self._plant_history(model)
        collection = VirtualCollection.objects.create(name='limits', owner=user)
        VirtualCollectionItem.objects.create(
            collection=collection, document_part=part, transcription_layer=layer,
        )
        state = {'order': []}
        self._run_patches(
            self._segmentation_patches(state),
            lambda: segtrain_from_collection(collection_pk=collection.pk, model_pk=model.pk),
        )
        self._assert_trainer(state['trainer'], 50, 0)
        self._assert_retained(model, state, historical, decoy)

        state = {'order': []}
        self._run_patches(
            self._segmentation_patches(state),
            lambda: segtrain_from_collection(
                collection_pk=collection.pk, model_pk=model.pk,
                quit_mode='early', min_epochs=4,
            ),
        )
        self._assert_trainer(state['trainer'], -1, 4)

    def test_invalid_limits_do_not_construct_a_trainer(self):
        _user, document, part, layer, model = self._model(OcrModel.MODEL_JOB_RECOGNIZE, 'invalid')
        queryset = LineTranscription.objects.filter(transcription=layer)
        boom = patch('core.tasks.KrakenTrainer', side_effect=AssertionError('constructed'))
        class StubRecognitionModel:
            @classmethod
            def load_from_weights(cls, path, config):
                return cls(config)

            def __init__(self, config=None):
                self.config = config

        with boom, patch('core.tasks.build_binary_dataset'), \
                patch('core.tasks.make_recognition_segmentation', return_value=[]), \
                patch('core.tasks.recognition_training_classes', side_effect=lambda architecture: (
                    PPOCRv6RecognitionTrainingConfig, MagicMock(), MagicMock(), StubRecognitionModel)):
            with self.assertRaises(TrainingLimitError):
                train_(queryset, document=document, transcription=layer, model=model, epochs=0)
        seg_model_user, seg_document, seg_part, seg_layer, seg_model = self._model(
            OcrModel.MODEL_JOB_SEGMENT, 'invalid-seg')
        collection = VirtualCollection.objects.create(name='invalid', owner=seg_model_user)
        VirtualCollectionItem.objects.create(
            collection=collection, document_part=seg_part, transcription_layer=seg_layer,
        )
        heavy = [
            patch('core.tasks.BLLASegmentationModel', MagicMock()),
            patch('core.tasks.BLLASegmentationDataModule', MagicMock()),
            patch('core.tasks.make_segmentation_training_data', return_value=[]),
            patch('core.tasks.KrakenTrainer', side_effect=AssertionError('constructed')),
        ]
        from contextlib import ExitStack
        with ExitStack() as stack:
            for item in heavy:
                stack.enter_context(item)
            with self.assertRaises(TrainingLimitError):
                segtrain(model_pk=seg_model.pk, part_pks=[seg_part.pk], document_pk=seg_document.pk, epochs=0)
            with self.assertRaises(TrainingLimitError):
                segtrain_from_collection(
                    collection_pk=collection.pk, model_pk=seg_model.pk, min_epochs=8, epochs=2,
                )
        seg_model.refresh_from_db()
        self.assertEqual(seg_model.versions or [], [])

    def test_record_retained_checkpoint_refuses_missing_and_outside_files(self):
        _user, _document, _part, _layer, model = self._model(OcrModel.MODEL_JOB_RECOGNIZE, 'record')
        historical, decoy = self._plant_history(model)
        before = list(model.versions)
        missing = os.path.join(os.path.dirname(model.file.path), 'missing.ckpt')
        self.assertFalse(record_retained_checkpoint(model, missing))
        outside = '/tmp/escript-epoch-outside-checkpoint.ckpt'
        with open(outside, 'wb') as handle:
            handle.write(b'outside')
        self.addCleanup(os.remove, outside)
        self.assertFalse(record_retained_checkpoint(model, outside))
        self.assertTrue(os.path.isfile(outside))
        model.refresh_from_db()
        self.assertEqual(model.versions, before)
        self.assertTrue(os.path.isfile(historical))
        self.assertTrue(os.path.isfile(decoy))

    def test_epoch_callback_updates_progress_without_a_version(self):
        _user, document, _part, _layer, model = self._model(OcrModel.MODEL_JOB_RECOGNIZE, 'callback')
        feedback = FrontendFeedback(model, os.path.dirname(model.file.path), document.pk)

        class _Trainer:
            current_epoch = 2
            logged_metrics = {'val_mean_iu': 0.2}

        feedback.on_train_epoch_end(_Trainer(), None)
        model.refresh_from_db()
        self.assertEqual(model.versions or [], [])
        self.assertEqual(model.training_epoch, 2)
        self.assertAlmostEqual(model.training_accuracy, 0.2)

    def test_production_callers_omit_epoch_overrides(self):
        for function in (
            train,
            train_from_collection,
            OcrModel.train,
            OcrModel.segtrain,
            OcrModel.segtrain_from_collection,
            OcrModel.train_from_collection,
        ):
            source = inspect.getsource(function)
            self.assertNotIn('epochs=', source)
            self.assertNotIn('quit_mode=', source)
            self.assertNotIn('min_epochs=', source)


@unittest.skipUnless(
    os.environ.get('KRAKEN_EPOCH_SMOKE') == '1',
    'Set KRAKEN_EPOCH_SMOKE=1 and KRAKEN_PPOCR_SMOKE_MODEL to run the one-epoch PP-OCRv6 smoke.',
)
class PPOCRv6OneEpochSmokeTests(TransactionTestCase):
    """Actual training through ``train_``. Scores from this fixture are process evidence."""

    def test_train_stops_after_one_epoch_and_keeps_the_best_checkpoint(self):
        from core.tasks import detect_model_architecture
        from kraken.tasks import RecognitionTaskModel

        weights = os.environ.get('KRAKEN_PPOCR_SMOKE_MODEL', '')
        if not os.path.isfile(weights):
            self.skipTest('KRAKEN_PPOCR_SMOKE_MODEL is not a readable PP-OCRv6 file')
        self.assertEqual(detect_model_architecture(weights), 'PPOCRv6Model')
        with open(weights, 'rb') as handle:
            parent_hash_before = _sha256(handle)

        factory = CoreFactory()
        user = factory.make_user()
        document = factory.make_document(owner=user, name='ppocr-epoch-smoke')
        layer = Transcription.objects.create(document=document, name='synthetic')
        from PIL import Image, ImageDraw
        import io
        from django.core.files import File

        for index in range(10):
            image = Image.new('RGB', (320, 64), 'white')
            ImageDraw.Draw(image).text((8, 16), f'line{index} ab', fill='black')
            buffer = io.BytesIO()
            image.save(buffer, format='PNG')
            from core.models import DocumentPart
            part = DocumentPart(
                document=document,
                original_filename=f'syn-{index}.png',
                image_file_size=buffer.tell(),
            )
            buffer.seek(0)
            part.image.save(f'syn-{index}.png', ContentFile(buffer.read()), save=True)
            line = Line.objects.create(
                document_part=part,
                baseline=[[4, 48], [300, 48]],
                mask=[[4, 8], [300, 8], [300, 60], [4, 60]],
            )
            LineTranscription.objects.create(
                line=line, transcription=layer, content=f'line{index} ab',
            )

        child = OcrModel.objects.create(
            name='ppocr-one-epoch-smoke',
            file_size=0,
            job=OcrModel.MODEL_JOB_RECOGNIZE,
            owner=user,
        )
        with open(weights, 'rb') as handle:
            child.file.save('ppocr-one-epoch-smoke.safetensors', File(handle), save=True)
        child.file_size = child.file.size
        child.save(update_fields=['file_size'])
        model_dir = os.path.dirname(child.file.path)

        previous = {
            'batch': getattr(settings, 'KRAKEN_TRAINING_BATCH_SIZE', None),
            'device': getattr(settings, 'KRAKEN_TRAINING_DEVICE', None),
            'threads': getattr(settings, 'KRAKEN_TRAINING_LOAD_THREADS', None),
            'precision': getattr(settings, 'KRAKEN_TRAINING_PRECISION', None),
        }
        # Harness-only. The product default batch size and precision stay as they are.
        settings.KRAKEN_TRAINING_BATCH_SIZE = 1
        settings.KRAKEN_TRAINING_DEVICE = 'cpu'
        settings.KRAKEN_TRAINING_LOAD_THREADS = 0
        settings.KRAKEN_TRAINING_PRECISION = '32'
        started = time.time()
        try:
            with self.assertLogs('core.tasks', level='INFO') as logs:
                train_(
                    LineTranscription.objects.filter(transcription=layer),
                    document=document,
                    transcription=layer,
                    model=child,
                    user=user,
                    epochs=1,
                )
        finally:
            for key, value in (
                ('KRAKEN_TRAINING_BATCH_SIZE', previous['batch']),
                ('KRAKEN_TRAINING_DEVICE', previous['device']),
                ('KRAKEN_TRAINING_LOAD_THREADS', previous['threads']),
                ('KRAKEN_TRAINING_PRECISION', previous['precision']),
            ):
                if value is None and hasattr(settings, key):
                    delattr(settings, key)
                elif value is not None:
                    setattr(settings, key, value)
        elapsed = time.time() - started
        # train() persists this instance after train_ returns. Mirror that save here.
        child.save()
        child.refresh_from_db()

        epoch_lines = [line for line in logs.output if 'Epoch ' in line and ' finished' in line]
        limit_lines = [line for line in logs.output if 'Kraken trainer limits' in line]
        checkpoints = sorted(
            name for name in os.listdir(model_dir) if name.endswith('.ckpt')
        )
        version_files = [(version.get('data') or {}).get('file') for version in child.versions or []]
        reloaded = RecognitionTaskModel.load_model(child.file.path)
        architecture = detect_model_architecture(child.file.path)
        evidence = {
            'label': 'synthetic process evidence, not accuracy',
            'seconds': round(elapsed, 1),
            'limit_lines': limit_lines,
            'epoch_lines': epoch_lines,
            'checkpoints': checkpoints,
            'version_files': version_files,
            'training_epoch': child.training_epoch,
            'training_accuracy': child.training_accuracy,
            'architecture': architecture,
            'reloaded_type': type(reloaded).__name__,
            'reloaded_net_type': type(reloaded.net).__name__,
            'output_bytes': child.file.size,
        }
        evidence_dir = '/tmp/escript-kraken-val/evidence'
        os.makedirs(evidence_dir, exist_ok=True)
        with open(os.path.join(evidence_dir, 'epoch-limit-smoke.json'), 'w', encoding='utf-8') as handle:
            import json
            json.dump(evidence, handle, indent=2)
        try:
            self.assertTrue(any('max_epochs=1' in line and 'min_epochs=0' in line for line in limit_lines), limit_lines)
            self.assertEqual(epoch_lines, ['INFO:core.tasks:Epoch 0 finished.'])
            self.assertEqual(checkpoints, ['checkpoint_epoch=0.ckpt'])
            self.assertFalse(any(name.startswith('last') for name in os.listdir(model_dir)))
            self.assertEqual(len(version_files), 1)
            version_path = os.path.join(settings.MEDIA_ROOT, version_files[0])
            self.assertTrue(os.path.isfile(version_path))
            self.assertEqual(os.path.basename(version_path), 'checkpoint_epoch=0.ckpt')
            self.assertTrue(os.path.isfile(os.path.join(model_dir, 'checkpoint_epoch=0.ckpt')))
            self.assertEqual(architecture, 'PPOCRv6Model')
            # load_model returns the recognition task wrapper. The network inside it is the model.
            self.assertEqual(type(reloaded).__name__, 'RecognitionTaskModel')
            self.assertEqual(type(reloaded.net).__name__, 'PPOCRv6Model')
            self.assertEqual(child.training_epoch, 0)
            self.assertIsInstance(child.training_accuracy, float)
        finally:
            with open(weights, 'rb') as handle:
                parent_hash_after = _sha256(handle)
            self.assertEqual(parent_hash_before, parent_hash_after)
            if os.environ.get('KRAKEN_EPOCH_SMOKE_KEEP') != '1':
                shutil.rmtree(model_dir, ignore_errors=True)


def _sha256(handle):
    import hashlib
    digest = hashlib.sha256()
    for chunk in iter(lambda: handle.read(1024 * 1024), b''):
        digest.update(chunk)
    return digest.hexdigest()
