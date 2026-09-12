"""DRF views that live in ai/ so api/views.py stays thin."""
from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ReadOnlyModelViewSet

from core.models import Document

from .gate import LayerNotEligible, acknowledge_sample, mark_training_eligible
from .models import AIBackendConfig, AILayerGate
from .serializers import AIBackendConfigSerializer, AILayerGateSerializer


class AIBackendConfigViewSet(ReadOnlyModelViewSet):
    """GET /api/ai-backends/ — configs the current user may run."""
    serializer_class = AIBackendConfigSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        user = self.request.user
        return AIBackendConfig.objects.filter(
            Q(public=True) | Q(owner=user) | Q(owner__isnull=True)
        ).order_by('name')


class AILayerGateViewSet(ReadOnlyModelViewSet):
    """GET/POST /api/documents/{id}/ai-gates/ — sample review + eligibility."""
    serializer_class = AILayerGateSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        doc = Document.objects.for_user(self.request.user).filter(
            pk=self.kwargs['document_pk']).first()
        if doc is None:
            return AILayerGate.objects.none()
        return (AILayerGate.objects
                .filter(transcription__document=doc)
                .select_related('transcription')
                .prefetch_related('disagreements'))

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx['include_sample_lines'] = self.action == 'retrieve'
        return ctx

    @action(detail=True, methods=['post'])
    def acknowledge(self, request, **kwargs):
        gate = self.get_object()
        try:
            acknowledge_sample(
                gate, user=request.user,
                phrase=request.data.get('phrase', ''),
                now=timezone.now())
            gate.save()
        except ValueError as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(AILayerGateSerializer(
            gate, context={**self.get_serializer_context(),
                           'include_sample_lines': True}).data)

    @action(detail=True, methods=['post'])
    def mark_eligible(self, request, **kwargs):
        gate = self.get_object()
        try:
            mark_training_eligible(gate)
            gate.save()
        except LayerNotEligible as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(AILayerGateSerializer(
            gate, context={**self.get_serializer_context(),
                           'include_sample_lines': True}).data)
