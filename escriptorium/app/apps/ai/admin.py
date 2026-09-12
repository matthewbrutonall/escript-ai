from django.contrib import admin, messages
from django.shortcuts import render
from django.utils import timezone

from .gate import (
    ACK_PHRASE, LayerNotEligible, acknowledge_sample, mark_training_eligible,
)
from .models import (
    AIBackendConfig, AIDocumentPolicy, AIExample, AIJob, AILayerGate,
    AILineDisagreement, AISegSuggestion, AIUsageLedger,
)


@admin.register(AIBackendConfig)
class AIBackendConfigAdmin(admin.ModelAdmin):
    list_display = ('name', 'provider', 'model_id', 'owner', 'public')
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
