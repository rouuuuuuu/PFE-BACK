from rest_framework import viewsets
from .models import StockItem
from .serializers import StockItemSerializer

class StockItemViewSet(viewsets.ReadOnlyModelViewSet):
    """
    GET /api/inventory/stock/
    """
    queryset = StockItem.objects.all()
    serializer_class = StockItemSerializer
