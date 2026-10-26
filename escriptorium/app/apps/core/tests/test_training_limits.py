"""Kraken trainer epoch limits and checkpoint retention.

``TrainingLimitTests`` and most of ``TrainingPathTests`` build a real
``KrakenTrainer`` and replace ``fit``. They do not train.
``test_two_runs_prune_only_inside_their_own_directories`` fits a toy
Lightning module so ``save_top_k`` pruning actually runs.
``PPOCRv6OneEpochSmokeTests`` is the only test that fits Kraken. It stays
skipped unless ``KRAKEN_EPOCH_SMOKE=1`` and ``KRAKEN_PPOCR_SMOKE_MODEL``
point at a PP-OCRv6 safetensors file. Run that process under an outer timeout
so a regression cannot continue into a 100-epoch fit.
"""
import inspect
import json
import os
import shutil
import tempfile
import subprocess
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.core.files.base import ContentFile
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings

from core.models import (
    Line,
    LineTranscription,
    OcrModel,
    Transcription,
    VirtualCollection,
    VirtualCollectionItem,
)
from core.tasks import (
    CheckpointMissing,
    FrontendFeedback,
    KrakenTrainer,
    TrainingLimitError,
    allocate_training_run_dir,
    construct_kraken_trainer,
    make_checkpoint_callback,
    model_file_path,
    recognition_training_classes,
    record_retained_checkpoint,
    recognition_batch_size,
    restore_base_model_file,
    segtrain,
    segtrain_from_collection,
    train,
    train_,
    train_from_collection,
    trainer_epoch_limits,
    switch_model_file,
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

    @override_settings(KRAKEN_TRAINING_BATCH_SIZE=3,
                       KRAKEN_TRAINING_BATCH_SIZE_PPOCR=7,
                       KRAKEN_TRAINING_BATCH_SIZE_VGSL=2)
    def test_architecture_batch_size_precedence(self):
        self.assertEqual(recognition_batch_size('PPOCRv6Model'), 7)
        self.assertEqual(recognition_batch_size('TorchVGSLModel'), 2)
        self.assertEqual(recognition_batch_size(None), 2)
        with override_settings(KRAKEN_TRAINING_BATCH_SIZE_PPOCR=None):
            self.assertEqual(recognition_batch_size('PPOCRv6Model'), 3)
        with override_settings(KRAKEN_TRAINING_BATCH_SIZE_PPOCR=None,
                               KRAKEN_TRAINING_BATCH_SIZE=None):
            self.assertIsNone(recognition_batch_size('PPOCRv6Model'))

    @override_settings(KRAKEN_TRAINING_BATCH_SIZE_PPOCR=None)
    def test_invalid_batch_size_is_refused(self):
        for value in (0, -1, True, '4', 2.5):
            with self.subTest(value=value), override_settings(KRAKEN_TRAINING_BATCH_SIZE=value):
                with self.assertRaises(TrainingLimitError):
                    recognition_batch_size('PPOCRv6Model')

    def test_environment_settings_are_loaded_in_a_fresh_process(self):
        names = ['KRAKEN_TRAINING_BATCH_SIZE', 'KRAKEN_TRAINING_BATCH_SIZE_PPOCR',
                 'KRAKEN_TRAINING_BATCH_SIZE_VGSL', 'KRAKEN_TRAINING_PRECISION']
        environment = os.environ.copy()
        environment.update(dict(zip(names, ['3', '7', '2', 'bf16-mixed'])))
        code = (
            'import json; from escriptorium import settings; '
            f'print(json.dumps([getattr(settings, name) for name in {names!r}]))')
        result = subprocess.run(
            [sys.executable, '-c', code], env=environment,
            capture_output=True, text=True, check=True, timeout=30)
        self.assertEqual(json.loads(result.stdout), [3, 7, 2, 'bf16-mixed'])

    @override_settings(KRAKEN_EARLY_STOP_MAX_EPOCHS=100)
    def test_fixed_and_early_limits(self):
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
        # VGSL leaves epochs at -1, so the application ceiling is the cap.
        vgsl = _limits_for(VGSLRecognitionTrainingConfig)
        self.assertEqual(vgsl, {'max_epochs': 100, 'min_epochs': 0})
        self.assertEqual(
            _limits_for(VGSLRecognitionTrainingConfig, quit='early', min_epochs=3, lag=4),
            {'max_epochs': 100, 'min_epochs': 3},
        )
        # A positive epoch count is the early-stopping ceiling.
        self.assertEqual(
            _limits_for(VGSLRecognitionTrainingConfig, quit='early', epochs=6, min_epochs=1, lag=2),
            {'max_epochs': 6, 'min_epochs': 1},
        )
        blla = _limits_for(BLLASegmentationTrainingConfig)
        self.assertEqual(blla, {'max_epochs': 50, 'min_epochs': 0})
        # BLLA's own epochs value is positive, so it caps an early-stopping run.
        self.assertEqual(
            _limits_for(BLLASegmentationTrainingConfig, quit='early', min_epochs=5, lag=1),
            {'max_epochs': 50, 'min_epochs': 5},
        )

    @override_settings(KRAKEN_EARLY_STOP_MAX_EPOCHS=40)
    def test_unconfigured_early_stopping_uses_the_application_ceiling(self):
        self.assertEqual(
            _limits_for(VGSLRecognitionTrainingConfig),
            {'max_epochs': 40, 'min_epochs': 0},
        )
        self.assertEqual(
            _limits_for(VGSLRecognitionTrainingConfig, min_epochs=40, lag=1),
            {'max_epochs': 40, 'min_epochs': 40},
        )
        with self.assertRaises(TrainingLimitError):
            _limits_for(VGSLRecognitionTrainingConfig, min_epochs=41, lag=1)
        # Positive configured epochs are the ceiling. The setting is not read.
        self.assertEqual(
            _limits_for(PPOCRv6RecognitionTrainingConfig, quit='early', lag=1),
            {'max_epochs': 100, 'min_epochs': 0},
        )
        self.assertEqual(
            _limits_for(BLLASegmentationTrainingConfig, quit='early', lag=1),
            {'max_epochs': 50, 'min_epochs': 0},
        )

    @override_settings(KRAKEN_EARLY_STOP_MAX_EPOCHS=0)
    def test_invalid_early_ceiling_is_rejected_only_as_the_fallback(self):
        with self.assertRaises(TrainingLimitError):
            _limits_for(VGSLRecognitionTrainingConfig)
        self.assertEqual(
            _limits_for(VGSLRecognitionTrainingConfig, quit='early', epochs=6, min_epochs=1, lag=2),
            {'max_epochs': 6, 'min_epochs': 1},
        )

    @override_settings(KRAKEN_EARLY_STOP_MAX_EPOCHS='nope')
    def test_non_integer_early_ceiling_is_rejected(self):
        with self.assertRaises(TrainingLimitError):
            _limits_for(VGSLRecognitionTrainingConfig)

    @override_settings(KRAKEN_EARLY_STOP_MAX_EPOCHS=True)
    def test_boolean_early_ceiling_is_rejected(self):
        with self.assertRaises(TrainingLimitError):
            _limits_for(VGSLRecognitionTrainingConfig)

    @override_settings(KRAKEN_EARLY_STOP_MAX_EPOCHS=100)
    def test_invalid_limits_raise(self):
        invalid = [
            dict(config=PPOCRv6RecognitionTrainingConfig, epochs=0),
            dict(config=PPOCRv6RecognitionTrainingConfig, epochs=-1),
            dict(config=PPOCRv6RecognitionTrainingConfig, epochs=True),
            dict(config=PPOCRv6RecognitionTrainingConfig, epochs=3, min_epochs=4),
            dict(config=PPOCRv6RecognitionTrainingConfig, min_epochs=True),
            dict(config=VGSLRecognitionTrainingConfig, quit='early', epochs=0),
            dict(config=VGSLRecognitionTrainingConfig, quit='early', epochs=4, min_epochs=5, lag=1),
            dict(config=VGSLRecognitionTrainingConfig, quit='early', min_epochs=101, lag=1),
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

    @override_settings(KRAKEN_EARLY_STOP_MAX_EPOCHS=100)
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
        self.assertEqual(early_trainer.max_epochs, 100)
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
        state['fitted_model'] = args[0]
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

    def _assert_trainer(self, trainer, max_epochs, min_epochs, model):
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
        run_dir = os.path.abspath(str(checkpoints[0].dirpath))
        model_dir = os.path.abspath(os.path.dirname(model.file.path))
        self.assertNotEqual(run_dir, model_dir)
        relative = os.path.relpath(run_dir, model_dir)
        parts = relative.split(os.sep)
        self.assertEqual(parts[0], 'runs')
        self.assertEqual(len(parts[1]), 32)
        int(parts[1], 16)

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
        self.assertIn(f'{os.sep}runs{os.sep}', best)
        run_id = os.path.basename(os.path.dirname(best))
        self.assertEqual(len(run_id), 32)
        int(run_id, 16)
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
        self._assert_trainer(state['trainer'], 100, 0, model)
        self._assert_retained(model, state, historical, decoy)

    def test_legacy_model_names_switch_after_conversion_on_all_trainer_sites(self):
        for site in ('recognition', 'document-segmentation', 'collection-segmentation'):
            with self.subTest(site=site):
                job = (OcrModel.MODEL_JOB_RECOGNIZE if site == 'recognition'
                       else OcrModel.MODEL_JOB_SEGMENT)
                user, document, part, layer, model = self._model(job, site)
                model.file.save(f'{site}.mlmodel', ContentFile(b'legacy-base'), save=True)
                legacy_path = model.file.path
                model.new_version(file=model.file.name)
                model.save()
                state = {'order': []}

                def convert(paths, dest):
                    self.assertTrue(str(dest).endswith('.safetensors'))
                    with open(dest, 'wb') as handle:
                        handle.write(b'converted-weights')

                if site == 'recognition':
                    patches = self._recognition_patches(state, 'PPOCRv6Model')
                    def call():
                        train_(LineTranscription.objects.filter(transcription=layer),
                               document=document, transcription=layer, model=model, epochs=1)
                elif site == 'document-segmentation':
                    patches = self._segmentation_patches(state)
                    def call():
                        segtrain(model_pk=model.pk, part_pks=[part.pk], document_pk=document.pk,
                                 epochs=1)
                else:
                    collection = VirtualCollection.objects.create(name='conversion', owner=user)
                    VirtualCollectionItem.objects.create(
                        collection=collection, document_part=part, transcription_layer=layer)
                    patches = self._segmentation_patches(state)
                    def call():
                        segtrain_from_collection(collection_pk=collection.pk, model_pk=model.pk,
                                                 epochs=1)
                patches.append(patch('core.tasks.convert_models', side_effect=convert))
                self._run_patches(patches, call)
                model.refresh_from_db()
                self.assertTrue(model.file.name.endswith('.safetensors'))
                with open(model.file.path, 'rb') as handle:
                    self.assertEqual(handle.read(), b'converted-weights')
                with open(legacy_path, 'rb') as handle:
                    self.assertEqual(handle.read(), b'legacy-base')
                history = [v['data']['file'] for v in model.versions]
                self.assertIn(os.path.relpath(legacy_path, settings.MEDIA_ROOT), history)

    def test_restore_base_uses_its_extension_and_keeps_current_artifact(self):
        _user, _document, _part, _layer, model = self._model(
            OcrModel.MODEL_JOB_SEGMENT, 'restore-base')
        current_path = model.file.path
        source = os.path.join(os.path.dirname(current_path), 'base.mlmodel')
        with open(source, 'wb') as handle:
            handle.write(b'original-base')
        restore_base_model_file(model, source)
        model.refresh_from_db()
        self.assertTrue(model.file.name.endswith('.mlmodel'))
        with open(model.file.path, 'rb') as handle:
            self.assertEqual(handle.read(), b'original-base')
        with open(current_path, 'rb') as handle:
            self.assertEqual(handle.read(), b'fake-weights')
        with patch('core.tasks.shutil.copy', side_effect=AssertionError('copied onto itself')):
            restore_base_model_file(model, model.file.path)
        original_name = model.file.name
        with self.assertRaises(FileNotFoundError):
            restore_base_model_file(model, '')
        model.refresh_from_db()
        self.assertEqual(model.file.name, original_name)

    def test_switching_extension_does_not_save_stale_training_state(self):
        _user, _document, _part, _layer, model = self._model(
            OcrModel.MODEL_JOB_RECOGNIZE, 'file-field-only')
        OcrModel.objects.filter(pk=model.pk).update(training=True)
        switch_model_file(model, '.mlmodel')
        model.refresh_from_db()
        self.assertTrue(model.training)

    def test_real_legacy_conversion_reload_uses_persisted_safetensors_path(self):
        from kraken.lib.vgsl import TorchVGSLModel
        from kraken.models import convert_models, load_models

        _user, _document, _part, _layer, model = self._model(
            OcrModel.MODEL_JOB_RECOGNIZE, 'real-format')
        model.file.save('real-format.mlmodel', ContentFile(b''), save=True)
        source = model.file.path
        TorchVGSLModel(vgsl='[1,48,0,1 Lbx2 O1c3]').save_model(source)
        with open(source, 'rb') as handle:
            original = handle.read()
        destination = model_file_path(model, '.safetensors')
        output = convert_models([source], destination)
        self.assertEqual(str(output), destination)
        switch_model_file(model, '.safetensors')
        model.refresh_from_db()
        self.assertEqual(model.file.path, destination)
        loaded = load_models(model.file.path)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].__class__.__name__, 'TorchVGSLModel')
        with open(source, 'rb') as handle:
            self.assertEqual(handle.read(), original)

    @override_settings(KRAKEN_TRAINING_BATCH_SIZE=None,
                       KRAKEN_TRAINING_BATCH_SIZE_PPOCR=None,
                       KRAKEN_TRAINING_BATCH_SIZE_VGSL=None)
    def test_recognition_uses_architecture_batch_defaults(self):
        for architecture, expected in [('PPOCRv6Model', 64), (None, 1)]:
            with self.subTest(architecture=architecture):
                _user, document, _part, layer, model = self._model(
                    OcrModel.MODEL_JOB_RECOGNIZE, f'batch-{expected}')
                state = {'order': []}
                self._run_patches(
                    self._recognition_patches(state, architecture),
                    lambda: train_(LineTranscription.objects.filter(transcription=layer),
                                   document=document, transcription=layer, model=model, epochs=1),
                )
                self.assertEqual(state['fitted_model'].config.batch_size, expected)

    @override_settings(KRAKEN_TRAINING_BATCH_SIZE=3, KRAKEN_TRAINING_BATCH_SIZE_PPOCR=7)
    def test_recognition_passes_batch_override_to_kraken(self):
        _user, document, _part, layer, model = self._model(
            OcrModel.MODEL_JOB_RECOGNIZE, 'batch-override')
        state = {'order': []}
        self._run_patches(
            self._recognition_patches(state, 'PPOCRv6Model'),
            lambda: train_(LineTranscription.objects.filter(transcription=layer),
                           document=document, transcription=layer, model=model, epochs=1),
        )
        self.assertEqual(state['fitted_model'].config.batch_size, 7)

    def test_recognition_ppocr_explicit_one_epoch(self):
        _user, document, _part, layer, model = self._model(OcrModel.MODEL_JOB_RECOGNIZE, 'ppocr-one')
        historical, decoy = self._plant_history(model)
        state = {'order': []}
        queryset = LineTranscription.objects.filter(transcription=layer)
        self._run_patches(
            self._recognition_patches(state, 'PPOCRv6Model'),
            lambda: train_(queryset, document=document, transcription=layer, model=model, epochs=1),
        )
        self._assert_trainer(state['trainer'], 1, 0, model)
        self._assert_retained(model, state, historical, decoy)

    @override_settings(KRAKEN_EARLY_STOP_MAX_EPOCHS=100)
    def test_recognition_vgsl_early_and_fixed_override(self):
        _user, document, _part, layer, model = self._model(OcrModel.MODEL_JOB_RECOGNIZE, 'vgsl-early')
        historical, decoy = self._plant_history(model)
        state = {'order': []}
        queryset = LineTranscription.objects.filter(transcription=layer)
        self._run_patches(
            self._recognition_patches(state, None),
            lambda: train_(queryset, document=document, transcription=layer, model=model),
        )
        self._assert_trainer(state['trainer'], 100, 0, model)
        self._assert_retained(model, state, historical, decoy)
        first_best = state['best_path']
        first_run = os.path.dirname(first_best)

        state = {'order': []}
        self._run_patches(
            self._recognition_patches(state, None),
            lambda: train_(
                queryset, document=document, transcription=layer, model=model,
                epochs=6, quit_mode='fixed', min_epochs=1,
            ),
        )
        self._assert_trainer(state['trainer'], 6, 1, model)
        self._assert_retained(model, state, historical, decoy)
        self.assertNotEqual(os.path.dirname(state['best_path']), first_run)
        self.assertTrue(os.path.isfile(first_best))
        with open(first_best, 'rb') as handle:
            self.assertEqual(handle.read(), b'best-checkpoint')
        model.refresh_from_db()
        recorded = [(version.get('data') or {}).get('file') for version in model.versions]
        self.assertIn(os.path.relpath(first_best, settings.MEDIA_ROOT), recorded)

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
        self._assert_trainer(state['trainer'], 100, 2, model)
        self.assertEqual(state['order'][0], 'record')

    def test_document_segmentation_fixed_and_early(self):
        _user, document, part, _layer, model = self._model(OcrModel.MODEL_JOB_SEGMENT, 'seg-doc')
        historical, decoy = self._plant_history(model)
        state = {'order': []}
        self._run_patches(
            self._segmentation_patches(state),
            lambda: segtrain(model_pk=model.pk, part_pks=[part.pk], document_pk=document.pk),
        )
        self._assert_trainer(state['trainer'], 50, 0, model)
        self._assert_retained(model, state, historical, decoy)

        state = {'order': []}
        self._run_patches(
            self._segmentation_patches(state),
            lambda: segtrain(
                model_pk=model.pk, part_pks=[part.pk], document_pk=document.pk,
                epochs=4, min_epochs=1,
            ),
        )
        self._assert_trainer(state['trainer'], 4, 1, model)

        state = {'order': []}
        self._run_patches(
            self._segmentation_patches(state),
            lambda: segtrain(
                model_pk=model.pk, part_pks=[part.pk], document_pk=document.pk,
                quit_mode='early', min_epochs=3,
            ),
        )
        self._assert_trainer(state['trainer'], 50, 3, model)

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
        self._assert_trainer(state['trainer'], 50, 0, model)
        self._assert_retained(model, state, historical, decoy)

        state = {'order': []}
        self._run_patches(
            self._segmentation_patches(state),
            lambda: segtrain_from_collection(
                collection_pk=collection.pk, model_pk=model.pk,
                quit_mode='early', min_epochs=4,
            ),
        )
        self._assert_trainer(state['trainer'], 50, 4, model)

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

    def test_public_recognition_tasks_forward_limits_and_reject_invalid_schedules(self):
        for function in (train, train_from_collection):
            params = inspect.signature(function).parameters
            for name in ('epochs', 'quit_mode', 'min_epochs'):
                self.assertIn(name, params)
                self.assertIsNone(params[name].default)

        user, document, part, layer, model = self._model(
            OcrModel.MODEL_JOB_RECOGNIZE, 'public-forward')
        collection = VirtualCollection.objects.create(name='public-forward', owner=user)
        VirtualCollectionItem.objects.create(
            collection=collection, document_part=part, transcription_layer=layer,
        )
        state = {'order': []}
        self._run_patches(
            self._recognition_patches(state, 'PPOCRv6Model'),
            lambda: train(
                transcription_pk=layer.pk, model_pk=model.pk, part_pks=[part.pk],
                epochs=3, quit_mode='fixed', min_epochs=1,
            ),
        )
        self._assert_trainer(state['trainer'], 3, 1, model)

        state = {'order': []}
        self._run_patches(
            self._recognition_patches(state, 'PPOCRv6Model'),
            lambda: train_from_collection(
                collection_pk=collection.pk, model_pk=model.pk,
                epochs=4, quit_mode='fixed', min_epochs=2,
            ),
        )
        self._assert_trainer(state['trainer'], 4, 2, model)

        class StubRecognitionModel:
            @classmethod
            def load_from_weights(cls, path, config):
                return cls(config)

            def __init__(self, config=None):
                self.config = config

        heavy = [
            patch('core.tasks.KrakenTrainer', side_effect=AssertionError('constructed')),
            patch('core.tasks.build_binary_dataset'),
            patch('core.tasks.make_recognition_segmentation', return_value=[]),
            patch('core.tasks.recognition_training_classes', side_effect=lambda architecture: (
                PPOCRv6RecognitionTrainingConfig, MagicMock(), MagicMock(), StubRecognitionModel)),
        ]
        from contextlib import ExitStack
        with ExitStack() as stack:
            for item in heavy:
                stack.enter_context(item)
            with self.assertRaises(TrainingLimitError):
                train(
                    transcription_pk=layer.pk, model_pk=model.pk, part_pks=[part.pk],
                    epochs=0,
                )
            with self.assertRaises(TrainingLimitError):
                train_from_collection(
                    collection_pk=collection.pk, model_pk=model.pk,
                    epochs=2, min_epochs=8,
                )
        self.assertTrue(OcrModel.objects.filter(pk=model.pk).exists())
        model.refresh_from_db()
        with open(model.file.path, 'rb') as handle:
            self.assertEqual(handle.read(), b'fake-weights')

    def _missing_checkpoint_failure(self, task, *, empty=False, history=False):
        from contextlib import ExitStack

        def fit(self, *args, **kwargs):
            callback = self.checkpoint_callback
            destination = os.path.join(str(callback.dirpath), 'checkpoint_epoch=0.ckpt')
            callback.best_model_path = destination
            callback.best_model_score = 0.1

        segmenter = task in (segtrain, segtrain_from_collection)
        collection_task = task in (train_from_collection, segtrain_from_collection)
        job = OcrModel.MODEL_JOB_SEGMENT if segmenter else OcrModel.MODEL_JOB_RECOGNIZE
        user, document, part, layer, model = self._model(job, 'missing-checkpoint')
        historical = None
        if history:
            historical, _decoy = self._plant_history(model)
        original_path = model.file.path
        if empty:
            model.file = ''
            model.save()
        before = list(model.versions or [])
        model_pk = model.pk
        kwargs = {'model_pk': model_pk, 'user_pk': user.pk, 'epochs': 1}
        if collection_task:
            collection = VirtualCollection.objects.create(name='missing', owner=user)
            VirtualCollectionItem.objects.create(
                collection=collection, document_part=part, transcription_layer=layer,
            )
            room, room_pk = 'collection', collection.pk
            kwargs['collection_pk'] = collection.pk
        else:
            room, room_pk = 'document', document.pk
            kwargs['part_pks'] = [part.pk]
            kwargs['document_pk' if segmenter else 'transcription_pk'] = (
                document.pk if segmenter else layer.pk)
        state = {'order': []}
        patches = (self._segmentation_patches(state) if segmenter else
                   self._recognition_patches(state, 'PPOCRv6Model'))
        patches[-1] = patch.object(KrakenTrainer, 'fit', fit)
        with ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            events = stack.enter_context(patch('core.tasks.send_event'))
            notify = stack.enter_context(patch.object(type(user), 'notify'))
            qualify = stack.enter_context(patch('core.tasks.qualify_model.delay'))
            result = task.apply(kwargs=kwargs, throw=False)
        self.assertTrue(result.failed(), result.result)
        self.assertIsInstance(result.result, CheckpointMissing)
        self.assertEqual(result.result.created_without_artifact, empty and not history)
        events.assert_any_call(room, room_pk, 'training:error', {'id': model_pk})
        events.assert_any_call(room, room_pk, 'training:done', {'id': model_pk})
        notify.assert_called_once()
        self.assertEqual(notify.call_args.kwargs['id'], 'training-error')
        self.assertEqual(notify.call_args.kwargs['level'], 'danger')
        qualify.assert_not_called()
        self.assertEqual(state['order'], [])
        if empty and not history:
            self.assertFalse(OcrModel.objects.filter(pk=model_pk).exists())
            return
        self.assertTrue(OcrModel.objects.filter(pk=model_pk).exists())
        model.refresh_from_db()
        self.assertFalse(model.training)
        self.assertEqual(model.versions or [], before)
        with open(original_path, 'rb') as handle:
            self.assertEqual(handle.read(), b'fake-weights')
        if historical:
            with open(historical, 'rb') as handle:
                self.assertEqual(handle.read(), b'historical')

    def test_missing_checkpoint_fails_and_preserves_existing_models(self):
        for task in (train, train_from_collection, segtrain, segtrain_from_collection):
            with self.subTest(task=task.name):
                self._missing_checkpoint_failure(task, history=True)

    def test_missing_checkpoint_discards_only_new_empty_models(self):
        for task in (train, train_from_collection, segtrain, segtrain_from_collection):
            with self.subTest(task=task.name):
                self._missing_checkpoint_failure(task, empty=True)

    def test_missing_checkpoint_preserves_file_without_history(self):
        self._missing_checkpoint_failure(train)

    def test_missing_checkpoint_preserves_history_without_current_file(self):
        self._missing_checkpoint_failure(train, empty=True, history=True)

    def test_two_runs_prune_only_inside_their_own_directories(self):
        """Lightning's real ``save_top_k`` pruning must not delete an earlier run."""
        import torch
        from lightning.pytorch import LightningModule, Trainer
        from lightning.pytorch.callbacks import Callback
        from torch.utils.data import DataLoader, TensorDataset

        class GrowingMetric(LightningModule):
            def __init__(self):
                super().__init__()
                self.layer = torch.nn.Linear(2, 1)

            def training_step(self, batch, batch_idx):
                features, target = batch
                return torch.nn.functional.mse_loss(self.layer(features), target)

            def validation_step(self, batch, batch_idx):
                self.log(
                    'val_metric', float(self.current_epoch + 1),
                    on_step=False, on_epoch=True, prog_bar=False, logger=True,
                )

            def configure_optimizers(self):
                return torch.optim.SGD(self.parameters(), lr=0.01)

        class SeenCheckpoints(Callback):
            def __init__(self, directory):
                self.directory = directory
                self.seen = []

            def on_train_epoch_end(self, trainer, pl_module):
                names = sorted(
                    name for name in os.listdir(self.directory) if name.endswith('.ckpt')
                )
                self.seen.append(names)

        def loader():
            features = torch.zeros(2, 2)
            target = torch.zeros(2, 1)
            return DataLoader(TensorDataset(features, target), batch_size=2)

        def fit_run(run_dir, epochs):
            seen = SeenCheckpoints(run_dir)
            trainer = Trainer(
                accelerator='cpu',
                devices=1,
                max_epochs=epochs,
                min_epochs=epochs,
                enable_progress_bar=False,
                enable_model_summary=False,
                logger=False,
                num_sanity_val_steps=0,
                limit_train_batches=1,
                limit_val_batches=1,
                check_val_every_n_epoch=1,
                callbacks=[make_checkpoint_callback(run_dir), seen],
            )
            trainer.fit(GrowingMetric(), loader(), loader())
            kept = sorted(
                name for name in os.listdir(run_dir) if name.endswith('.ckpt')
            )
            return kept, seen.seen

        def load_checkpoint(path):
            try:
                return torch.load(path, map_location='cpu', weights_only=False)
            except TypeError:
                return torch.load(path, map_location='cpu')

        _user, _document, _part, _layer, model = self._model(
            OcrModel.MODEL_JOB_RECOGNIZE, 'two-run-prune')
        model_dir = os.path.dirname(model.file.path)
        legacy = os.path.join(model_dir, 'checkpoint_epoch=0.ckpt')
        with open(legacy, 'wb') as handle:
            handle.write(b'legacy-root')

        first_dir = allocate_training_run_dir(model_dir)
        first_kept, first_seen = fit_run(first_dir, 4)
        self.assertEqual(len(first_kept), 1, first_seen)
        self.assertGreaterEqual(len(first_seen), 2)
        earlier = {name for names in first_seen for name in names}
        self.assertGreater(len(earlier), 1, first_seen)
        self.assertNotIn(first_seen[0][0], first_kept)
        first_path = os.path.join(first_dir, first_kept[0])
        self.assertTrue(record_retained_checkpoint(model, first_path))

        same_name = os.path.join(model_dir, first_kept[0])
        if same_name != legacy:
            with open(same_name, 'wb') as handle:
                handle.write(b'same-name-outside-run')
            self.addCleanup(os.remove, same_name)

        second_dir = allocate_training_run_dir(model_dir)
        self.assertNotEqual(first_dir, second_dir)
        second_kept, second_seen = fit_run(second_dir, 3)
        self.assertEqual(len(second_kept), 1, second_seen)
        second_path = os.path.join(second_dir, second_kept[0])
        self.assertTrue(os.path.isfile(first_path))
        self.assertTrue(record_retained_checkpoint(model, second_path))

        for path in (first_path, second_path):
            loaded = load_checkpoint(path)
            self.assertIn('state_dict', loaded)
            self.assertTrue(loaded['state_dict'])

        with open(legacy, 'rb') as handle:
            self.assertEqual(handle.read(), b'legacy-root')
        if same_name != legacy:
            with open(same_name, 'rb') as handle:
                self.assertEqual(handle.read(), b'same-name-outside-run')

        model.refresh_from_db()
        recorded = [(version.get('data') or {}).get('file') for version in model.versions]
        first_relative = os.path.relpath(first_path, settings.MEDIA_ROOT)
        second_relative = os.path.relpath(second_path, settings.MEDIA_ROOT)
        self.assertIn(first_relative, recorded)
        self.assertIn(second_relative, recorded)
        self.assertNotEqual(first_relative, second_relative)
        for relative in (first_relative, second_relative):
            self.assertIn(f'{os.sep}runs{os.sep}', relative)
            stored = os.path.join(settings.MEDIA_ROOT, relative)
            self.assertTrue(os.path.isfile(stored))
            self.assertIn('state_dict', load_checkpoint(stored))
        with open(model.file.path, 'rb') as handle:
            self.assertEqual(handle.read(), b'fake-weights')


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

        evidence_dir = tempfile.mkdtemp(prefix='escript-epoch-smoke-')
        started = time.time()
        try:
            # Harness-only. The product default batch size and precision stay as they are.
            with override_settings(
                KRAKEN_TRAINING_BATCH_SIZE=1,
                KRAKEN_TRAINING_DEVICE='cpu',
                KRAKEN_TRAINING_LOAD_THREADS=0,
                KRAKEN_TRAINING_PRECISION='32',
            ):
                with self.assertLogs('core.tasks', level='INFO') as logs:
                    train_(
                        LineTranscription.objects.filter(transcription=layer),
                        document=document,
                        transcription=layer,
                        model=child,
                        user=user,
                        epochs=1,
                    )
            elapsed = time.time() - started
            # train() persists this instance after train_ returns. Mirror that save here.
            child.save()
            child.refresh_from_db()

            epoch_lines = [line for line in logs.output if 'Epoch ' in line and ' finished' in line]
            limit_lines = [line for line in logs.output if 'Kraken trainer limits' in line]
            checkpoints = _checkpoint_files(model_dir)
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
            import json
            with open(os.path.join(evidence_dir, 'epoch-limit-smoke.json'), 'w', encoding='utf-8') as handle:
                json.dump(evidence, handle, indent=2)
            self.assertTrue(any('max_epochs=1' in line and 'min_epochs=0' in line for line in limit_lines), limit_lines)
            self.assertEqual(epoch_lines, ['INFO:core.tasks:Epoch 0 finished.'])
            self.assertEqual(len(checkpoints), 1)
            self.assertEqual(os.path.basename(checkpoints[0]), 'checkpoint_epoch=0.ckpt')
            self.assertIn(f'{os.sep}runs{os.sep}', checkpoints[0])
            self.assertFalse(any(
                name.startswith('last')
                for _dirpath, _dirnames, names in os.walk(model_dir)
                for name in names
            ))
            self.assertEqual(len(version_files), 1)
            version_path = os.path.join(settings.MEDIA_ROOT, version_files[0])
            self.assertTrue(os.path.isfile(version_path))
            self.assertEqual(os.path.basename(version_path), 'checkpoint_epoch=0.ckpt')
            self.assertIn(f'{os.sep}runs{os.sep}', version_path)
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
                shutil.rmtree(evidence_dir, ignore_errors=True)


def _checkpoint_files(root):
    found = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if name.endswith('.ckpt'):
                found.append(os.path.join(dirpath, name))
    return sorted(found)


def _sha256(handle):
    import hashlib
    digest = hashlib.sha256()
    for chunk in iter(lambda: handle.read(1024 * 1024), b''):
        digest.update(chunk)
    return digest.hexdigest()
