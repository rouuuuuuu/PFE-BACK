from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import StartProvisioningView, ProvisioningStatusView, ProvisioningTaskViewSet

router = DefaultRouter()
router.register(r'tasks', ProvisioningTaskViewSet)

urlpatterns = [
    path('', include(router.urls)),
    path('start/', StartProvisioningView.as_view(), name='provisioning-start'),
    path('status/<int:task_id>/', ProvisioningStatusView.as_view(), name='provisioning-status'),
]
