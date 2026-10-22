"""Preview or apply an import from one completed external HTR audit.

The default is a read-only preview. ``--apply`` is the only path that
writes, and it calls the import helper rather than trusting the preview.
Output is one line. It does not include recognized text, a layer name,
an endpoint, a document title, or exception text.
"""
import sys

from django.core.management.base import BaseCommand

from ai.external_htr_import_command import execute_external_htr_import
from ai.external_htr_writeback import import_external_htr_writeback
from ai.external_htr_writeback_plan import load_external_htr_writeback_plan

_INTERNAL = "FAILED: external HTR import import_internal"


class Command(BaseCommand):
    help = (
        "Preview an external HTR audit import. "
        "--apply creates one new transcription layer from that audit. "
        "This does not call an engine or change Transcribe."
    )

    def add_arguments(self, parser):
        parser.add_argument("job_id", nargs="?")
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Create the layer. The default only prints a preview.",
        )

    def handle(self, *args, **options):
        try:
            code, line = execute_external_htr_import(
                options.get("job_id"),
                apply=options["apply"],
                load_plan=load_external_htr_writeback_plan,
                import_writeback=import_external_htr_writeback,
                fetch_job=_fetch_job,
                count_lines=_count_lines,
            )
        except Exception:
            code, line = 1, _INTERNAL
        self.stdout.write(line)
        if code:
            sys.exit(code)


def _fetch_job(job_id):
    from ai.models import ExternalHTRJob

    return ExternalHTRJob.objects.filter(pk=job_id).only(
        "pk", "imported", "imported_transcription_id",
    ).first()


def _count_lines(transcription_id):
    from core.models import LineTranscription

    return LineTranscription.objects.filter(transcription_id=transcription_id).count()
