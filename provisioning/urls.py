from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import StartProvisioningView, ProvisioningStatusView, ProvisioningTaskViewSet
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
]

