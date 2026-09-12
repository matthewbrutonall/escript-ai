"""
Data model for the AI transcription layer (ARCHITECTURE.md §8, §11).

Three tables, all additive — no change to core:
  - AIBackendConfig : a provider + model + prompt config (NOT an OcrModel; that is
                      a kraken file on disk). This is the "AI backend/source".
  - AIJob          : one run's record + provenance (what config, what parts, mode,
                     tokens, cost, status). Rich metadata that will not fit in the
                     128-char version_source.
  - AIUsageLedger  : the billing row (§11) — do not repurpose reporting.gpu_cost.
"""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import models
from django.utils.translation import gettext_lazy as _

User = get_user_model()


class AIBackendConfig(models.Model):
    """Provider + model + prompt + params. The AI 'backend', surfaced beside
    kraken models in the UI but stored separately (do NOT extend OcrModel)."""
    PROVIDER_GEMINI = 'gemini'
    PROVIDER_ANTHROPIC = 'anthropic'
    PROVIDER_OPENAI = 'openai'
    PROVIDER_LOCAL = 'local'          # OpenAI-compatible: Ollama / vLLM
    PROVIDER_CHOICES = (
        (PROVIDER_GEMINI, 'Google Gemini'),
        (PROVIDER_ANTHROPIC, 'Anthropic Claude'),
        (PROVIDER_OPENAI, 'OpenAI'),
        (PROVIDER_LOCAL, 'Local (OpenAI-compatible: Ollama/vLLM)'),
    )

    name = models.CharField(max_length=256)
    provider = models.CharField(max_length=32, choices=PROVIDER_CHOICES)
    model_id = models.CharField(max_length=128)          # e.g. 'gemini-2.5-flash'
    # for local/self-hosted OpenAI-compatible endpoints; null for hosted providers
    endpoint = models.URLField(null=True, blank=True)
    # API key is NOT stored here in cleartext; resolved at run time from a secret
    # store keyed by this name (instance env or per-user/team, §7/§12). Never logged.
    key_ref = models.CharField(max_length=128, null=True, blank=True)

    prompt_template = models.TextField(
        help_text=_("Diplomatic-transcription prompt. Colour keys are appended."))
    max_edge_px = models.PositiveIntegerField(
        default=1024, help_text=_("Downscale cap — image tokens dominate cost."))
    params = models.JSONField(default=dict, blank=True)   # temperature, etc.

    owner = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)
    public = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['name', 'owner']

    def __str__(self):
        return f"{self.name} ({self.provider}:{self.model_id})"

    @property
    def version_source(self):
        """What gets stamped on each LineTranscription (fits 128 chars)."""
        return f"{self.provider}:{self.model_id}"[:128]

    @property
    def is_local(self):
        return self.provider == self.PROVIDER_LOCAL


class AIJob(models.Model):
    """One transcription run: provenance + status + accounting."""
    MODE_REGION = 'region'   # colour-keyed region JSON (§5.A) — default
    MODE_LINE = 'line'       # per-line crops (§5.B) — GT-harvest / fallback
    MODE_CHOICES = ((MODE_REGION, 'Keyed region'), (MODE_LINE, 'Per-line'))

    STATUS_PENDING = 'pending'
    STATUS_RUNNING = 'running'
    STATUS_DONE = 'done'
    STATUS_ERROR = 'error'
    STATUS_CHOICES = (
        (STATUS_PENDING, 'Pending'), (STATUS_RUNNING, 'Running'),
        (STATUS_DONE, 'Done'), (STATUS_ERROR, 'Error'),
    )

    backend = models.ForeignKey(AIBackendConfig, null=True, on_delete=models.SET_NULL)
    document = models.ForeignKey('core.Document', on_delete=models.CASCADE,
                                 related_name='ai_jobs')
    transcription = models.ForeignKey('core.Transcription', null=True,
                                      on_delete=models.SET_NULL,
                                      help_text=_("The layer AI output was written to."))
    mode = models.CharField(max_length=16, choices=MODE_CHOICES, default=MODE_REGION)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES,
                              default=STATUS_PENDING)
    prompt_version = models.CharField(max_length=64, blank=True)

    parts_count = models.PositiveIntegerField(default=0)
    lines_written = models.PositiveIntegerField(default=0)
    lines_flagged = models.PositiveIntegerField(
        default=0, help_text=_("Skipped/left empty by a pre-flight or bad response."))
    tokens_in = models.BigIntegerField(default=0)
    tokens_out = models.BigIntegerField(default=0)
    est_cost = models.FloatField(default=0.0)
    actual_cost = models.FloatField(default=0.0)

    created_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)
    task_group = models.ForeignKey(
        'reporting.TaskGroup', null=True, blank=True, on_delete=models.SET_NULL,
        help_text=_("eScriptorium TaskGroup created at enqueue (ProcessSerializerMixin)."))
    task_id = models.CharField(
        max_length=64, null=True, blank=True,
        help_text=_("Celery task id, set when the worker starts. Not the TaskGroup pk."))
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"AIJob#{self.pk} {self.status} doc={self.document_id}"


class AIUsageLedger(models.Model):
    """Billing row (§11). Separate from reporting's cpu_cost/gpu_cost."""
    job = models.ForeignKey(AIJob, null=True, on_delete=models.SET_NULL,
                            related_name='ledger_entries')
    provider = models.CharField(max_length=32)
    model_id = models.CharField(max_length=128)
    tokens_in = models.BigIntegerField(default=0)
    tokens_out = models.BigIntegerField(default=0)
    est_cost = models.FloatField(default=0.0)
    actual_cost = models.FloatField(default=0.0)
    currency = models.CharField(max_length=8, default='USD')
    user = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)
    document = models.ForeignKey('core.Document', null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class AIDocumentPolicy(models.Model):
    """Per-document egress policy (§13). Lives in ai/ so core.Document is untouched.

    Enforced at task dispatch (not only in the UI): a remote backend must not
    run if never_send_offsite is set. Missing row = allowed (self-host default).
    """
    document = models.OneToOneField(
        'core.Document', on_delete=models.CASCADE, related_name='ai_policy')
    never_send_offsite = models.BooleanField(
        default=False,
        help_text=_("If set, only local (Ollama/vLLM) backends may run on this document."))
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"AIDocumentPolicy doc={self.document_id} offsite={'no' if self.never_send_offsite else 'ok'}"
