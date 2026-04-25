from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    DashboardStatsView, RouterViewSet, SwitchViewSet,
    PortViewSet, CardViewSet, SFPViewSet,
    HardwareVerifyView, UnifiedDeviceListView, get_port_id
)

router = DefaultRouter()
router.register(r'routers', RouterViewSet)
router.register(r'switches', SwitchViewSet)
router.register(r'hardware/ports', PortViewSet)
router.register(r'hardware/cards', CardViewSet)
router.register(r'hardware/sfps', SFPViewSet)

urlpatterns = [
    path('', include(router.urls)),
    path('dashboard/stats/', DashboardStatsView.as_view(), name='dashboard-stats'),
    path('hardware/verify/<str:device_ip>/', HardwareVerifyView.as_view(), name='hardware-verify'),
    path('all-devices/', UnifiedDeviceListView.as_view()),
    path('port-id/', get_port_id, name='port-id'),
]
