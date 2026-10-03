"""Check one stored external HTR engine against the contract.

This does not transcribe a document. Output is one line and does not
include the service address, stored options, the image, or a response body.
"""
import sys

from django.core.management.base import BaseCommand

from ai.htr_engine_contract_check import (
    EngineConfigNotFound,
    execute_contract_check,
    run_contract_check,
)
from ai.models import ExternalHTREngineConfig


class Command(BaseCommand):
    help = (
        "Check one external HTR engine against the contract. "
        "This does not transcribe a document."
    )

    def add_arguments(self, parser):
        parser.add_argument("config_id", type=int)

    def handle(self, *args, **options):
        code, line = execute_contract_check(
            options["config_id"],
            _load_config,
            run_contract_check,
        )
        self.stdout.write(line)
        if code:
            sys.exit(code)


def _load_config(config_id):
    try:
        return ExternalHTREngineConfig.objects.get(pk=config_id)
    except ExternalHTREngineConfig.DoesNotExist:
        raise EngineConfigNotFound from None
