from django.contrib import admin
from .models import ProvisioningTask, BandwidthUpgrade

@admin.register(ProvisioningTask)
class ProvisioningTaskAdmin(admin.ModelAdmin):
    list_display = ('device_name', 'device_ip', 'task_type', 'status', 'created_at', 'completed_at')
    list_filter = ('status', 'task_type')
    search_fields = ('device_name', 'device_ip')
    readonly_fields = ('celery_task_id', 'created_at', 'started_at', 'completed_at')

@admin.register(BandwidthUpgrade)
class BandwidthUpgradeAdmin(admin.ModelAdmin):
    # This matches the 'upgrade_id' primary key you defined
    list_display = ('upgrade_id', 'device', 'interface', 'new_bandwidth_mbps', 'status', 'created_at')
    list_filter = ('status', 'device', 'created_at')
    search_fields = ('customer_name', 'interface')
    # It's helpful to see the commands/output in the admin detail view
    readonly_fields = ('generated_commands', 'execution_output', 'celery_task_id', 'created_at')
