"""Check one stored external HTR engine with a supplied recognize request.

This does not read a document, store an audit row, or write a transcription.
Output is one line and does not include the service address, stored options,
the request path, or a response body.
"""
import json
import sys

from django.core.management.base import BaseCommand

from ai.external_htr_live import run_external_htr_live
from ai.external_htr_live_command import (
    ConfigNotFound,
    RequestInvalid,
    RequestUnavailable,
    execute_external_htr_live_check,
)
from ai.models import ExternalHTREngineConfig


class Command(BaseCommand):
    help = (
        "Check one external HTR engine with a supplied recognize request. "
        "This does not read a document, store an audit row, or write a transcription."
    )

    def add_arguments(self, parser):
        parser.add_argument("config_id", type=int)
        parser.add_argument("request_json_path")

    def handle(self, *args, **options):
        try:
            code, line = execute_external_htr_live_check(
                options["config_id"],
                options["request_json_path"],
                fetch_config=_load_config,
                read_request=_read_request,
                run=run_external_htr_live,
            )
        except Exception:
            code, line = 1, "FAILED: external HTR live check internal"
        self.stdout.write(line)
        if code:
            sys.exit(code)


def _load_config(config_id):
    try:
        return ExternalHTREngineConfig.objects.get(pk=config_id)
    except ExternalHTREngineConfig.DoesNotExist:
        raise ConfigNotFound from None


def _read_request(path):
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except OSError:
        raise RequestUnavailable from None
    except (json.JSONDecodeError, UnicodeError):
        raise RequestInvalid from None
    if not isinstance(payload, dict):
        raise RequestInvalid
    return payload
