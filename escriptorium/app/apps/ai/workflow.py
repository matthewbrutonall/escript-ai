"""Map AI celery task names onto the chrome's workflow keys (transcribe/segment)."""


def client_process(task_name: str) -> str:
    short = (task_name or "").rsplit(".", 1)[-1].replace("-", "_")
    if short == "ai_transcribe":
        return "transcribe"
    return short
