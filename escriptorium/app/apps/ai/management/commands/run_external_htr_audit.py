"""Audit one document part with a configured external HTR engine.

This encodes line images in memory, calls that engine, and stores audit
rows. It does not write a transcription. Output is one line and does not
include the service address, stored options, a document name, or an image.
"""
import sys

from PIL import Image
from django.core.management.base import BaseCommand

from ai.external_htr_audit_command import (
    ConfigNotFound,
    PartNotFound,
    execute_external_htr_audit,
)
from ai.models import ExternalHTREngineConfig
from core.models import DocumentPart


class Command(BaseCommand):
    help = (
        "Audit one document part with an external HTR engine. "
        "This records audit rows only. It does not write transcription output."
    )

    def add_arguments(self, parser):
        parser.add_argument("config_id", type=int)
        parser.add_argument("document_part_id", type=int)
        parser.add_argument("--engine", required=True)
        parser.add_argument("--model-id", required=True)

    def handle(self, *args, **options):
        try:
            code, line = execute_external_htr_audit(
                options["config_id"],
                options["document_part_id"],
                model_id=options["model_id"],
                engine=options["engine"],
                fetch_config=_load_config,
                fetch_part=_load_part,
                open_image=_open_part_image,
            )
        except Exception:
            code, line = 1, "FAILED: external HTR audit internal"
        self.stdout.write(line)
        if code:
            sys.exit(code)


def _load_config(config_id):
    try:
        return ExternalHTREngineConfig.objects.get(pk=config_id)
    except ExternalHTREngineConfig.DoesNotExist:
        raise ConfigNotFound from None


def _load_part(part_id):
    try:
        part = DocumentPart.objects.get(pk=part_id)
    except DocumentPart.DoesNotExist:
        raise PartNotFound from None
    return part, list(part.lines.order_by("order", "pk"))


def _open_part_image(part):
    # The caller closes the image. It has to stay open until the crops are read.
    return Image.open(part.image.path)
