from rest_framework import viewsets
from .models import BackhaulLink
from .serializers import BackhaulLinkSerializer
from django_filters.rest_framework import DjangoFilterBackend

class BackhaulLinkViewSet(viewsets.ModelViewSet):
    queryset = BackhaulLink.objects.all().order_by('-imported_at')
    serializer_class = BackhaulLinkSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['alarm_severity', 'link_type'] # Filter links by alarm severity
