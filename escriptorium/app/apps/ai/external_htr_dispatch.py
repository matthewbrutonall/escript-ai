"""Choose the page worker or the line worker for one queued run.

The choice is the engine row's metadata. A URL alone does not select the
page worker. This module does not call an engine at import time.
"""
from __future__ import annotations

PAGE_MARKER = {"task": "recognize_page", "engine": "htrflow"}


def is_htrflow_page_config(config) -> bool:
    """True only for the exact page-recognition marker."""
    return getattr(config, "metadata", None) == PAGE_MARKER


def dispatch_external_htr_run(run_id, task_id=""):
    """Run the queued recognition on the worker that matches its engine row."""
    from ai.models import ExternalHTRRun

    run = ExternalHTRRun.objects.filter(pk=run_id).select_related("config").first()
    if run is not None and is_htrflow_page_config(run.config):
        from ai.htrflow_run import execute_htrflow_run

        return execute_htrflow_run(run_id, task_id=task_id)
    from ai.external_htr_run import execute_external_htr_run

    return execute_external_htr_run(run_id, task_id=task_id)
