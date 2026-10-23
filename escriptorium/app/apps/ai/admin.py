from django.contrib import admin, messages
from django.shortcuts import render
from django.utils import timezone

from .gate import (
    ACK_PHRASE, LayerNotEligible, acknowledge_sample, mark_training_eligible,
)
from django import forms
from django.conf import settings

from .htr_engine_client import probe_configs
from .models import (
    AIBackendConfig, AIDocumentPolicy, AIExample, AIJob, AILayerGate,
    AIBillableCall, AIBudgetHold, AILineDisagreement, AIModelPrice,
    AISegSuggestion,
    AIUsageLedger,
    AIUserKey, AIUserQuota, ExternalHTREngineConfig, ExternalHTRJob,
    ExternalHTRLineResult,
)
from .secrets import encrypt_key


class AIUserKeyForm(forms.ModelForm):
    plaintext = forms.CharField(
        required=False, widget=forms.PasswordInput(render_value=False),
        help_text="Set or rotate the key. Leave blank to keep the stored one.")

    class Meta:
        model = AIUserKey
        fields = ('user', 'provider')

    def clean(self):
        cleaned = super().clean()
        raw = (cleaned.get('plaintext') or "").strip()
        if not self.instance.pk and not raw:
            raise forms.ValidationError("Provide a key when creating.")
        return cleaned

    def save(self, commit=True):
        obj = super().save(commit=False)
        raw = (self.cleaned_data.get('plaintext') or "").strip()
        if raw:
            obj.ciphertext = encrypt_key(raw, settings.SECRET_KEY)
        if commit:
            obj.save()
        return obj


@admin.register(AIUserQuota)
class AIUserQuotaAdmin(admin.ModelAdmin):
    list_display = ('user', 'monthly_usd')


@admin.register(AIUserKey)
class AIUserKeyAdmin(admin.ModelAdmin):
    form = AIUserKeyForm
    list_display = ('user', 'provider', 'updated_at')


@admin.register(ExternalHTREngineConfig)
class ExternalHTREngineConfigAdmin(admin.ModelAdmin):
    list_display = ("name", "enabled", "tier", "experimental", "timeout_seconds")
    list_filter = ("enabled", "tier", "experimental")
    search_fields = ("name",)
    actions = ["test_connection"]

    @admin.action(description="Test connection")
    def test_connection(self, request, queryset):
        levels = {
            "success": messages.SUCCESS,
            "warning": messages.WARNING,
            "error": messages.ERROR,
        }
        for level, text in probe_configs(queryset):
            self.message_user(request, text, levels[level])


@admin.register(ExternalHTRJob)
class ExternalHTRJobAdmin(admin.ModelAdmin):
    list_display = (
        "pk", "status", "document", "model_id", "requested_line_count",
        "result_count", "created_at",
    )
    list_filter = ("status",)
    search_fields = ("model_id", "engine")
    raw_id_fields = ("document", "part", "config", "created_by")
    readonly_fields = ("imported", "imported_transcription")


@admin.register(ExternalHTRLineResult)
class ExternalHTRLineResultAdmin(admin.ModelAdmin):
    list_display = ("pk", "job", "line_id", "status", "position", "timing_ms")
    list_filter = ("status",)
    raw_id_fields = ("job",)


@admin.register(AIBackendConfig)
class AIBackendConfigAdmin(admin.ModelAdmin):
    list_display = ('name', 'provider', 'model_id', 'price_model_id', 'owner',
                    'public')
    list_filter = ('provider', 'public')


@admin.register(AIJob)
class AIJobAdmin(admin.ModelAdmin):
    list_display = ('pk', 'status', 'document', 'mode', 'lines_written',
                    'lines_flagged', 'actual_cost', 'task_id', 'created_at')
    list_filter = ('status', 'mode')
    raw_id_fields = ('task_group',)


@admin.register(AIDocumentPolicy)
class AIDocumentPolicyAdmin(admin.ModelAdmin):
    list_display = ('document', 'never_send_offsite', 'updated_at')
    list_filter = ('never_send_offsite',)


@admin.register(AILayerGate)
class AILayerGateAdmin(admin.ModelAdmin):
    list_display = ('transcription', 'state', 'mean_cer', 'job', 'updated_at')
    list_filter = ('state',)
    actions = ['acknowledge_sample_action', 'mark_eligible_action']

    @admin.action(description="Acknowledge review sample")
    def acknowledge_sample_action(self, request, queryset):
        if 'phrase' not in request.POST:
            return render(request, 'admin/ai/ailayergate/acknowledge.html', {
                'title': 'Acknowledge sample',
                'queryset': queryset,
                'phrase': ACK_PHRASE,
            })
        phrase = request.POST.get('phrase', '')
        ok = 0
        for gate in queryset:
            try:
                acknowledge_sample(
                    gate, user=request.user, phrase=phrase, now=timezone.now())
                gate.save()
                ok += 1
            except ValueError as e:
                self.message_user(request, str(e), messages.ERROR)
                return
        self.message_user(request, f"Acknowledged {ok} layer(s).")

    @admin.action(description="Mark training-eligible")
    def mark_eligible_action(self, request, queryset):
        ok = 0
        for gate in queryset:
            try:
                mark_training_eligible(gate)
                gate.save()
                ok += 1
            except LayerNotEligible as e:
                self.message_user(request, str(e), messages.ERROR)
                return
        self.message_user(request, f"Marked {ok} layer(s) training-eligible.")


@admin.register(AILineDisagreement)
class AILineDisagreementAdmin(admin.ModelAdmin):
    list_display = ('line', 'cer', 'in_sample', 'gate')
    list_filter = ('in_sample',)


@admin.register(AIExample)
class AIExampleAdmin(admin.ModelAdmin):
    list_display = ('document', 'line', 'pinned', 'updated_at')
    list_filter = ('pinned',)


@admin.register(AISegSuggestion)
class AISegSuggestionAdmin(admin.ModelAdmin):
    list_display = ('pk', 'document', 'part', 'kind', 'status', 'line', 'updated_at')
    list_filter = ('kind', 'status')
    raw_id_fields = ('document', 'part', 'line', 'job')


@admin.register(AIUsageLedger)
class AIUsageLedgerAdmin(admin.ModelAdmin):
    list_display = ('pk', 'provider', 'model_id', 'tokens_in', 'tokens_out',
                    'actual_cost', 'currency', 'user', 'created_at')
    list_filter = ('provider', 'currency')


@admin.register(AIModelPrice)
class AIModelPriceAdmin(admin.ModelAdmin):
    """Hosted models without a row here are refused before any request."""
    list_display = ('provider', 'model_id', 'input_usd_per_1m',
                    'output_usd_per_1m', 'source', 'updated_at')
    list_filter = ('provider',)
    search_fields = ('model_id',)


@admin.register(AIBudgetHold)
class AIBudgetHoldAdmin(admin.ModelAdmin):
    list_display = ('pk', 'job', 'user', 'amount_usd', 'state',
                    'close_reason', 'created_at', 'closed_at')
    list_filter = ('state',)
    raw_id_fields = ('job', 'user')


@admin.register(AIBillableCall)
class AIBillableCallAdmin(admin.ModelAdmin):
    """Estimated and unknown calls stay held until resolved here."""
    list_display = ('pk', 'state', 'provider', 'model_id', 'reserved_usd',
                    'confirmed_usd', 'estimated_usd', 'sent', 'error', 'job',
                    'user', 'created_at', 'resolved_by')
    list_filter = ('state', 'provider', 'sent')
    raw_id_fields = ('job', 'hold', 'user', 'document', 'ledger', 'resolved_by')
    actions = ['resolve_charged_action', 'resolve_not_charged_action']

    def _resolve(self, request, queryset, *, charged, action, instructions):
        from decimal import InvalidOperation

        from django.core.exceptions import PermissionDenied

        from .billing_store import resolve_call
        # Actions declare permissions=['change']; checked here as well.
        if not self.has_change_permission(request):
            raise PermissionDenied
        if 'confirm' not in request.POST:
            return render(request, 'admin/ai/aibillablecall/resolve.html', {
                'title': 'Resolve provider calls', 'queryset': queryset,
                'charged': charged, 'action': action,
                'instructions': instructions,
            })
        note = request.POST.get('note', '').strip()
        if not note:
            self.message_user(request, "A note is required.", messages.ERROR)
            return
        amount = None
        if charged:
            from .billing import usd
            try:
                amount = usd(request.POST.get('amount', ''))
            except (ValueError, InvalidOperation):
                amount = None
            if amount is None or amount < 0:
                self.message_user(request, "Enter the charged amount in USD.",
                                  messages.ERROR)
                return
        ok = 0
        for call in queryset:
            try:
                resolve_call(call, by=request.user, charged=charged,
                             amount=amount, note=note)
                ok += 1
            except ValueError as e:
                self.message_user(request, str(e), messages.ERROR)
        self.message_user(request, f"Resolved {ok} call(s).")

    @admin.action(description="Resolve as charged (enter amount)",
                  permissions=['change'])
    def resolve_charged_action(self, request, queryset):
        return self._resolve(
            request, queryset, charged=True, action='resolve_charged_action',
            instructions="Use the provider's billing records. Each selected "
                         "call gets this amount as a ledger row.")

    @admin.action(description="Resolve as not charged",
                  permissions=['change'])
    def resolve_not_charged_action(self, request, queryset):
        return self._resolve(
            request, queryset, charged=False,
            action='resolve_not_charged_action',
            instructions="Only when the provider's billing records show no "
                         "charge. This releases the held amount.")
