from django.contrib import admin
from .models import ProvisioningTask

@admin.register(ProvisioningTask)
class ProvisioningTaskAdmin(admin.ModelAdmin):
    list_display  = ['device_name', 'device_ip', 'task_type', 'status', 'created_at', 'completed_at']
    list_filter   = ['status', 'task_type']
    search_fields = ['device_name', 'device_ip']
    readonly_fields = ['celery_task_id', 'created_at', 'started_at', 'completed_at']
