from django.contrib import admin

from .models import AIBackendConfig, AIDocumentPolicy, AIJob, AIUsageLedger


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


@admin.register(AIUsageLedger)
class AIUsageLedgerAdmin(admin.ModelAdmin):
    list_display = ('pk', 'provider', 'model_id', 'tokens_in', 'tokens_out',
                    'actual_cost', 'currency', 'user', 'created_at')
    list_filter = ('provider', 'currency')
