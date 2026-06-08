from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import MonthlyReportViewSet

# 1. Initialize the router
router = DefaultRouter()

# 2. Register the ViewSet
# This single line generates /monthly/ AND /monthly/generate_now/
router.register(r'monthly', MonthlyReportViewSet, basename='monthly-report')

# 3. Expose the URLs
urlpatterns = [
    path('', include(router.urls)),
]
