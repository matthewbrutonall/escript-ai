import os
import os.path
import tempfile

import celery.app.trace
from celery import signals

from reporting.tasks import create_task_reporting

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
REDIS_DB = 1

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _short_test_media_root():
    """Keep absolute FileField values inside the 100-character column.

    Several tests assign an absolute model path to ``OcrModel.file``.
    A media root under this repository exceeds that limit.
    """
    configured = os.environ.get('ESCRIPT_TEST_MEDIA')
    if configured:
        os.makedirs(configured, exist_ok=True)
        return configured
    return tempfile.mkdtemp(prefix='es-m-')


MEDIA_ROOT = _short_test_media_root()
STATICFILES_STORAGE = 'django.contrib.staticfiles.storage.StaticFilesStorage'

WEBPACK_LOADER = {
    'DEFAULT': {
        'LOADER_CLASS': 'webpack_loader.loaders.FakeWebpackLoader',
    }
}


class DisableMigrations(object):

    def __contains__(self, item):
        return True

    def __getitem__(self, item):
        return None


MIGRATION_MODULES = DisableMigrations()

# disable cache
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.dummy.DummyCache',
    }
}

KRAKEN_TRAINING_LOAD_THREADS = 0

# Disables easy-thumbnail spamming
THUMBNAIL_OPTIMIZE_COMMAND = {}

# Trigger before_task_publish signal even using celery eager mode
old_build_tracer = celery.app.trace.build_tracer

# show all errors during exports
EXPORT_STRICT = True


def build_tracer_patched(name, task, *args, **kwargs):
    before_task_publish_receivers = signals.before_task_publish.receivers
    old_trace_task = old_build_tracer(name, task, *args, **kwargs)

    def trace_task_patched(uuid, args, kwargs, request=None):
        if before_task_publish_receivers:
            create_task_reporting(sender=name, body=(args, kwargs), headers={"id": uuid})
        return old_trace_task(uuid, args, kwargs, request)

    return trace_task_patched


celery.app.trace.build_tracer = build_tracer_patched
