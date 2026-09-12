"""
DRF serializer for POST .../documents/{id}/ai_transcribe/.

Lives in ai/ (not api/) so core/api stay thin. Constrains parts and
transcription to *this* document so the API fails early; the task still
re-checks (raw .delay() must not bypass).
"""
from django.db.models import Q
from rest_framework import serializers

from api.serializers import ProcessSerializerMixin
from core.models import DocumentPart, Line, Transcription


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
from .models import AIBackendConfig, AIJob, AILayerGate, AIUsageLedger


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


class AIFixSerializer(serializers.Serializer):
    """POST .../documents/{id}/ai_fix/ — one stubborn line, sync."""
    line = serializers.PrimaryKeyRelatedField(queryset=Line.objects.all())
    transcription = serializers.PrimaryKeyRelatedField(
        queryset=Transcription.objects.all())
    backend = serializers.PrimaryKeyRelatedField(
        queryset=AIBackendConfig.objects.all(), required=False)
    partial = serializers.CharField(required=False, allow_blank=True, default="")

    def __init__(self, *args, document=None, user=None, **kwargs):
        self.document = document
        self.user = user
        super().__init__(*args, **kwargs)
        if document is None:
            return
        self.fields['transcription'].queryset = Transcription.objects.filter(
            document=document)
        self.fields['line'].queryset = Line.objects.filter(
            document_part__document=document)
        self.fields['backend'].queryset = AIBackendConfig.objects.filter(
            Q(public=True) | Q(owner=user) | Q(owner__isnull=True))

    def validate(self, data):
        line = data['line']
        trans = data['transcription']
        if trans.document_id != self.document.pk:
            raise serializers.ValidationError(
                {'transcription': 'Transcription must belong to this document.'})
        if line.document_part.document_id != self.document.pk:
            raise serializers.ValidationError(
                {'line': 'Line must belong to this document.'})
        config = data.get('backend')
        if config is None:
            job = (AIJob.objects
                   .filter(transcription=trans, backend__isnull=False)
                   .order_by('-pk').first())
            if job is None:
                raise serializers.ValidationError(
                    {'backend': 'No AI backend given and none on a prior job.'})
            config = job.backend
        try:
            assert_dispatch_allowed(self.document, config, user=self.user)
        except (RemoteAIForbidden, RuntimeError) as e:
            raise serializers.ValidationError(str(e))
        data['backend'] = config
        return data

    def save(self):
        from PIL import Image

        from .backends import get_backend
        from .conventions import conventions_prompt
        from .dispatch import resolve_api_key
        from .fixthis import LINE_KEY, extract_line_text, fix_prompt
        from .overlay import crop_line
        from .pipeline import stamp_line_transcription

        line = self.validated_data['line']
        trans = self.validated_data['transcription']
        config = self.validated_data['backend']
        partial = self.validated_data.get('partial') or ""
        mask = line.mask or []
        if not mask and line.baseline:
            xs = [p[0] for p in line.baseline]
            ys = [p[1] for p in line.baseline]
            mask = [(min(xs), min(ys) - 20), (max(xs), min(ys) - 20),
                    (max(xs), max(ys)), (min(xs), max(ys))]
        if not mask:
            raise serializers.ValidationError(
                {'line': 'Line has no mask or baseline to crop.'})
        api_key = resolve_api_key(config)
        backend = get_backend(config, api_key=api_key)
        prompt = fix_prompt(partial, conventions_prompt(config.conventions))
        with Image.open(line.document_part.image.path) as im:
            crop = crop_line(im, [tuple(p) for p in mask])
            result = backend.transcribe_region(crop, [LINE_KEY], prompt)
        text = extract_line_text(result)
        if not text:
            raise serializers.ValidationError(
                {'detail': 'The backend returned no text for this line.'})
        author = (self.user.username if self.user else '')[:128]
        stamp_line_transcription(
            line, trans, text, config.version_source, author)
        cost = backend.cost(result.tokens_in, result.tokens_out)
        AIUsageLedger.objects.create(
            job=None, provider=config.provider, model_id=config.model_id,
            tokens_in=result.tokens_in, tokens_out=result.tokens_out,
            actual_cost=cost, user=self.user, document=self.document)
        return {
            'line': line.pk,
            'text': text,
            'tokens_in': result.tokens_in,
            'tokens_out': result.tokens_out,
            'cost': cost,
            'version_source': config.version_source,
        }
