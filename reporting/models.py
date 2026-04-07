from django.db import models

class MonthlyReport(models.Model):
    # e.g., 'April 2026'
    month_year = models.CharField(max_length=20, unique=True)
    
    # Network Snapshot
    total_routers = models.IntegerField(default=0)
    total_switches = models.IntegerField(default=0)
    total_links = models.IntegerField(default=0)
    
    # Alarm & Activity Snapshot
    critical_alarms_count = models.IntegerField(default=0)
    provisioning_tasks_completed = models.IntegerField(default=0)
    provisioning_tasks_failed = models.IntegerField(default=0)
    
    # We can store the actual generated file later
    pdf_file = models.FileField(upload_to='reports/monthly/', null=True, blank=True)
    
    # Timestamps
    generated_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Network Report - {self.month_year}"
    
    class Meta:
        ordering = ['-generated_at']
