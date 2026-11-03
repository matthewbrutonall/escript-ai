"""Celery entry for the external HTR action.

The task body loads the executor when it runs. This module does not call
an engine and does not write a transcription layer.
"""
from escriptorium.celery import app


@app.task(bind=True, name="ai.external_htr_recognize")
def external_htr_recognize(self, run_id):
    """Recognize one queued run. A bad id does not raise."""
    from ai.external_htr_dispatch import dispatch_external_htr_run

    raw = getattr(getattr(self, "request", None), "id", None)
    task_id = raw[:64] if isinstance(raw, str) else ""
    try:
        dispatch_external_htr_run(run_id, task_id=task_id)
    except Exception:
        return None
    return None
