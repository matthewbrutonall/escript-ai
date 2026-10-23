"""Stop billing for AI jobs whose worker is gone (docs/budget-reservations.md).

Closes open holds of cancelled, finished, or stale jobs. Their in-flight
calls become `unknown` (if sent) or `released` (if never sent). Nothing that
may have been billed is released. Reservations also run this check, so the
command is only needed when no new AI work is arriving.
"""
from django.core.management.base import BaseCommand

from ai.billing_store import reconcile_now


class Command(BaseCommand):
    help = "Close AI budget holds left by cancelled or crashed jobs."

    def handle(self, *args, **options):
        closed = reconcile_now()
        self.stdout.write(f"OK: closed {closed} stale AI budget hold(s).")
