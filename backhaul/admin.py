from django.contrib import admin
from .models import BackhaulLink

@admin.register(BackhaulLink)
class BackhaulLinkAdmin(admin.ModelAdmin):
    list_display  = ['link_name', 'source_ne', 'sink_ne', 'link_level', 'alarm_severity', 'imported_at']
    list_filter   = ['alarm_severity', 'link_level', 'link_type']
    search_fields = ['link_name', 'source_ne', 'sink_ne', 'source_ip', 'sink_ip']
