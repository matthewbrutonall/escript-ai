"""DRF views that live in ai/ so api/views.py stays thin."""
from django.db.models import Q
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ReadOnlyModelViewSet

from .models import AIBackendConfig
from .serializers import AIBackendConfigSerializer


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
