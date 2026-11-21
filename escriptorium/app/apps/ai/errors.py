"""Fixed, user-safe descriptions of AI job failures."""
import re

MESSAGES = {
    "offsite_forbidden": "This document is set never to leave this server, and the chosen AI backend is hosted elsewhere. Choose a local backend or change the document's policy.",
    "policy_unreadable": "The document's off-site policy could not be checked, so nothing was sent.",
    "no_api_key": "No API key is set up for this AI backend. Ask an administrator, or add your own key.",
    "key_unreadable": "Your saved API key could not be read. Ask an administrator to reset it.",
    "budget": "This job would go over the monthly AI budget, so it was not started.",
    "key_rejected": "The AI provider rejected the API key.",
    "provider_quota": "The AI provider refused the request because of its rate limit or quota. Try again later.",
    "provider_rejected": "The AI provider rejected the request. The model name or backend settings may be wrong.",
    "provider_down": "The AI provider reported an error on its side. Try again later.",
    "provider_unreachable": "The AI provider could not be reached.",
    "provider_timeout": "The AI provider took too long to answer.",
    "wrong_document": "Some of the selected pages do not belong to this document.",
    "no_pages": "No pages were available to process.",
    "partial_failure": "AI segmentation review finished with some pages unavailable. Check the page results before relying on the suggestions.",
    "unexpected": "The job stopped because of an unexpected error. An administrator can see the details.",
}


def classify(exc):
    name = type(exc).__name__
    message = str(exc)
    if name == "RemoteAIForbidden":
        return "policy_unreadable" if "could not be checked" in message else "offsite_forbidden"
    if name == "APIKeyUnavailable": return "key_unreadable"
    if name == "BudgetExceeded": return "budget"
    if name == "CrossDocumentError": return "wrong_document"
    if name in {"Timeout", "ReadTimeout", "ConnectTimeout"}: return "provider_timeout"
    if name in {"ConnectionError", "SSLError"}: return "provider_unreachable"
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if not isinstance(status, int):
        match = re.match(r"^(?:Anthropic|OpenAI|chat completions|Gemini) (\d{3})\b", message)
        status = int(match.group(1)) if match else None
    if status in (401, 403): return "key_rejected"
    if status == 429: return "provider_quota"
    if status in (400, 404, 422): return "provider_rejected"
    if isinstance(status, int) and 500 <= status <= 599: return "provider_down"
    if message.startswith("No API key resolved") or "requires an API key" in message: return "no_api_key"
    if message == "provider redirect refused": return "provider_unreachable"
    return "unexpected"


def public_message(code, *, page_index=None, page_total=None):
    result = MESSAGES.get(code, MESSAGES["unexpected"])
    if isinstance(page_index, int) and isinstance(page_total, int):
        result += f" Stopped at page {page_index} of {page_total}; earlier pages were processed and the layer is marked for review."
    return result
