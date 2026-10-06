"""Plan one external HTR dry run for a stored config and document part.

This does not call an engine or write a transcription. Output is one line
and does not include the service address, stored options, a document
name, or an image. ``--encode-images`` opens the part image and crops
lines for this dry run. The default does not open an image.
"""
import sys

from PIL import Image
from django.core.management.base import BaseCommand

from ai.external_htr_plan_command import (
    ConfigNotFound,
    PartNotFound,
    execute_external_htr_dry_run,
    no_line_image,
)
from ai.external_htr_service import run_external_htr_dry_run
from ai.models import ExternalHTREngineConfig
from core.models import DocumentPart


class Command(BaseCommand):
    help = (
        "Plan an external HTR dry run and store the audit row. "
        "This does not call an engine or write a transcription. "
        "--encode-images is opt-in and still does not call an engine."
    )

    def add_arguments(self, parser):
        parser.add_argument("config_id", type=int)
        parser.add_argument("document_part_id", type=int)
        parser.add_argument("--model-id", required=True)
        parser.add_argument("--engine", required=True)
        parser.add_argument(
            "--encode-images",
            action="store_true",
            help=(
                "Encode line images for this dry run. "
                "This still does not call an engine or write a transcription."
            ),
        )

    def handle(self, *args, **options):
        try:
            code, line = execute_external_htr_dry_run(
                options["config_id"],
                options["document_part_id"],
                model_id=options["model_id"],
                engine=options["engine"],
                fetch_config=_load_config,
                fetch_part=_load_part,
                run=run_external_htr_dry_run,
                encode_line=no_line_image,
                encode_images=options["encode_images"],
                open_image=_open_part_image,
                dry_run=True,
            )
        except Exception:
            code, line = 1, "FAILED: external HTR dry run internal"
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
