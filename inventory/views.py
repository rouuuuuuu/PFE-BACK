from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import StockItem
from .serializers import StockItemSerializer

class StockItemViewSet(viewsets.ModelViewSet):
    """
    Full CRUD endpoints for Inventory Stock.
    """
    queryset = StockItem.objects.all()
    serializer_class = StockItemSerializer

    @action(detail=True, methods=['post'])
    def increment(self, request, pk=None):
        """
        Custom endpoint: POST /api/inventory/stock/{id}/increment/
        Increments the stock quantity by 1.
        """
        item = self.get_object()
        item.stock_qte += 1
        item.save()
        return Response({'status': 'stock incremented', 'new_qte': item.stock_qte})

    @action(detail=True, methods=['post'])
    def decrement(self, request, pk=None):
        """
        Custom endpoint: POST /api/inventory/stock/{id}/decrement/
        Decrements the stock quantity by 1 (stops at 0).
        """
        item = self.get_object()
        if item.stock_qte > 0:
            item.stock_qte -= 1
            item.save()
            return Response({'status': 'stock decremented', 'new_qte': item.stock_qte})
        else:
            return Response(
                {'error': 'Stock is already at 0'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
