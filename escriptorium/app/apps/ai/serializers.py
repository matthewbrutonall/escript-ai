"""
DRF serializer for POST .../documents/{id}/ai_transcribe/.

Lives in ai/ (not api/) so core/api stay thin. Constrains parts and
transcription to *this* document so the API fails early; the task still
re-checks (raw .delay() must not bypass).
"""
from django.db.models import Q
from rest_framework import serializers

from api.serializers import ProcessSerializerMixin
from core.models import DocumentPart, Transcription


def narrow_related(field, queryset):
    """Same helper as api.serializers; inlined because older images lack it."""
    getattr(field, 'child_relation', field).queryset = queryset

from .dispatch import (
    CrossDocumentError,
    RemoteAIForbidden,
    assert_dispatch_allowed,
    assert_parts_belong,
)
from .gate import ACK_PHRASE, assemble_sample_lines
from .models import AIBackendConfig, AIJob, AILayerGate


class AILayerGateSerializer(serializers.ModelSerializer):
    transcription_name = serializers.CharField(
        source='transcription.name', read_only=True)
    sample_size = serializers.SerializerMethodField()
    sample_lines = serializers.SerializerMethodField()
    ack_phrase = serializers.SerializerMethodField()

    class Meta:
        model = AILayerGate
        fields = (
            'pk', 'transcription', 'transcription_name', 'state', 'mean_cer',
            'sample_size', 'sample_lines', 'ack_phrase', 'comparison',
            'acknowledged_at',
        )

    def get_sample_size(self, obj):
        return len(obj.sample_line_pks or [])

    def get_ack_phrase(self, obj):
        return ACK_PHRASE

    def get_sample_lines(self, obj):
        if self.context.get('include_sample_lines') is False:
            return []
        pks = obj.sample_line_pks or []
        disagreements = {
            d.line_id: {
                'ai_text': d.ai_text,
                'comparison_text': d.comparison_text,
                'cer': d.cer,
            }
            for d in obj.disagreements.filter(line_id__in=pks)
        }
        ai_text = {}
        if pks and len(disagreements) < len(pks):
            from core.models import LineTranscription
            for lt in LineTranscription.objects.filter(
                    transcription=obj.transcription, line_id__in=pks):
                ai_text[lt.line_id] = lt.content or ''
        return assemble_sample_lines(pks, disagreements, ai_text)


class AIBackendConfigSerializer(serializers.ModelSerializer):
    """Public listing for the Transcribe modal. No secrets."""
    is_local = serializers.BooleanField(read_only=True)

    class Meta:
        model = AIBackendConfig
        fields = ('pk', 'name', 'provider', 'model_id', 'is_local')


class AITranscribeSerializer(ProcessSerializerMixin, serializers.Serializer):
    PROCESS_NAME = 'ai-transcribe'

    parts = serializers.PrimaryKeyRelatedField(
        many=True, queryset=DocumentPart.objects.all(), required=False)
    backend = serializers.PrimaryKeyRelatedField(
        queryset=AIBackendConfig.objects.all())
    transcription = serializers.PrimaryKeyRelatedField(
        queryset=Transcription.objects.all(), required=False)
    layer_name = serializers.CharField(required=False, allow_blank=True)
    per_crop = serializers.IntegerField(required=False, min_value=1, max_value=8,
                                        default=6)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.document is None:
            return
        self.fields['transcription'].queryset = Transcription.objects.filter(
            document=self.document)
        self.fields['backend'].queryset = AIBackendConfig.objects.filter(
            Q(public=True) | Q(owner=self.user) | Q(owner__isnull=True))
        # Codex: API layer fails early even though the task is now protected.
        narrow_related(self.fields['parts'],
                       DocumentPart.objects.filter(document=self.document))

    def validate(self, data):
        data = super().validate(data)
        parts = data.get('parts') or list(self.document.parts.all())
        try:
            assert_parts_belong(self.document, parts)
        except CrossDocumentError as e:
            raise serializers.ValidationError({'parts': str(e)})
        trans = data.get('transcription')
        if trans is not None and trans.document_id != self.document.pk:
            raise serializers.ValidationError(
                {'transcription': 'Transcription must belong to this document.'})
        config = data['backend']
        try:
            assert_dispatch_allowed(self.document, config, user=self.user)
        except (RemoteAIForbidden, RuntimeError) as e:
            raise serializers.ValidationError(str(e))
        data['parts'] = parts
        return data

    def process(self):
        super().process()
        from .tasks import ai_transcribe

        config = self.validated_data['backend']
        transcription = self.validated_data.get('transcription')
        if transcription is None:
            name = (self.validated_data.get('layer_name')
                    or f"AI — {config.version_source}")
            transcription, _ = Transcription.objects.get_or_create(
                document=self.document, name=name)
        parts = self.validated_data['parts']
        job = AIJob.objects.create(
            backend=config,
            document=self.document,
            transcription=transcription,
            mode=AIJob.MODE_REGION,
            status=AIJob.STATUS_PENDING,
            parts_count=len(parts),
            created_by=self.user,
            task_group=self.task_group,
        )
        ai_transcribe.delay(
            instance_pks=[p.pk for p in parts],
            ai_config_pk=config.pk,
            transcription_pk=transcription.pk,
            user_pk=self.user.pk,
            job_pk=job.pk,
            per_crop=self.validated_data.get('per_crop') or 6,
        )
