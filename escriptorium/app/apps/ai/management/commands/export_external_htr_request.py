"""Export one external HTR recognize request for a document part.

This encodes line images and writes a JSON file. It does not call an
engine, store an audit row, or write a transcription. Output is one line
and does not include the service address, stored options, a document
name, the image, or the output path.
"""
import sys

from PIL import Image
from django.core.management.base import BaseCommand

from ai.external_htr_export import (
    ConfigNotFound,
    PartNotFound,
    execute_external_htr_export,
)
from ai.models import ExternalHTREngineConfig
from core.models import DocumentPart


class Command(BaseCommand):
    help = (
        "Export an external HTR recognize request as JSON. "
        "This encodes line images. It does not call an engine, "
        "store an audit row, or write a transcription."
    )

    def add_arguments(self, parser):
        parser.add_argument("config_id", type=int)
        parser.add_argument("document_part_id", type=int)
        parser.add_argument("--model-id", required=True)
        parser.add_argument("--engine", required=True)
        parser.add_argument("--output", required=True)
        parser.add_argument(
            "--force",
            action="store_true",
            help="Replace an existing output file.",
        )

    def handle(self, *args, **options):
        try:
            code, line = execute_external_htr_export(
                options["config_id"],
                options["document_part_id"],
                model_id=options["model_id"],
                engine=options["engine"],
                output_path=options["output"],
                fetch_config=_load_config,
                fetch_part=_load_part,
                open_image=_open_part_image,
                force=options["force"],
            )
        except Exception:
            code, line = 1, "FAILED: external HTR request export internal"
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
