from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import StartProvisioningView, ProvisioningStatusView, ProvisioningTaskViewSet, reserve_port, fetch_switch_for_port, StartVoIPProvisioningView, liberate_voip_port,StartL2VCProvisioningView, liberate_l2vc_port
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
    path('port-id/', views.get_port_id, name='get-port-id'),
    path('fetch-switch/', fetch_switch_for_port, name='fetch-switch'), 
    path('reserve-port/', reserve_port, name='reserve-port'),
    path('port-reservations/', views.list_port_reservations, name='list-port-reservations'),
    path('voip/start/',StartVoIPProvisioningView.as_view(), name='voip-start'),
    path('voip/liberate/<int:task_id>/', liberate_voip_port, name='voip-liberate'),
    path('l2vc/start/', StartL2VCProvisioningView.as_view(), name='l2vc-start'),
    path('l2vc/liberate/<int:task_id>/', liberate_l2vc_port, name='l2vc-liberate'),
]
