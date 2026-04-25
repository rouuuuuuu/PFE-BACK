from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import StockItemViewSet

router = DefaultRouter()
router.register(r'stock', StockItemViewSet, basename='stock')

urlpatterns = [
    path('', include(router.urls)),
]
