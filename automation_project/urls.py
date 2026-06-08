"""
URL configuration for automation_project project.
"""
from django.contrib import admin
from django.urls import path, include
from django.http import JsonResponse
from django.conf import settings
from django.conf.urls.static import static

def ping(request):
    return JsonResponse({'status': 'ok', 'message': 'DRS 010 API RUNNING'})

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/ping/', ping),
    
    # --- Project Modules ---
    path('api/backhaul/', include('backhaul.urls')),
    path('api/', include('devices.urls')),
    path('api/provisioning/', include('provisioning.urls')),
    
    # FIXED: Changed from 'api/reporting/' to 'api/reports/' to match frontend
    path('api/reports/', include('reporting.urls')), 
    
    path('api/ai/', include('ai.urls')),
    path('api/inventory/', include('inventory.urls')), 
]
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
