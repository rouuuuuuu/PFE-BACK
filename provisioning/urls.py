from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import StartProvisioningView, ProvisioningStatusView, ProvisioningTaskViewSet, reserve_port
from . import views

router = DefaultRouter()
router.register(r'tasks', ProvisioningTaskViewSet)

urlpatterns = [
    path('', include(router.urls)),
    path('start/', StartProvisioningView.as_view(), name='provisioning-start'),
    path('status/<int:task_id>/', ProvisioningStatusView.as_view(), name='provisioning-status'),
    path('bandwidth-upgrade/', views.create_bandwidth_upgrade, name='create-bandwidth-upgrade'),
    path('bandwidth-upgrades/', views.list_bandwidth_upgrades, name='list-bandwidth-upgrades'),
    path('bandwidth-upgrades/<int:upgrade_id>/', views.get_bandwidth_upgrade_detail, name='get-bandwidth-upgrade'),
    path('bandwidth-upgrades/<int:upgrade_id>/retry/', views.retry_upgrade, name='retry-upgrade'),
    path('fetch-interfaces/', views.fetch_interfaces, name='fetch-interfaces'),
    
    # ─── ADDED FOR ANGULAR PRE-CHECK LOOKUP ───
    path('port-id/', views.get_port_id, name='get-port-id'),
    
    path('reserve-port/', reserve_port, name='reserve-port'),
    path('port-reservations/', views.list_port_reservations, name='list-port-reservations'),
]
