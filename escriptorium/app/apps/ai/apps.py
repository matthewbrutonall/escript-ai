from django.apps import AppConfig


class AiConfig(AppConfig):
    name = 'ai'
    verbose_name = 'AI transcription'

    def ready(self):
        from . import signals  # noqa: F401
