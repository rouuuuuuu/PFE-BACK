from django.contrib import admin
from .models import MonthlyReport

@admin.register(MonthlyReport)
class MonthlyReportAdmin(admin.ModelAdmin):
    list_display = ('month_year', 'total_routers', 'total_switches', 'critical_alarms_count', 'generated_at')
    search_fields = ('month_year',)
    readonly_fields = ('generated_at',)
