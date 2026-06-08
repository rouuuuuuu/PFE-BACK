from django.db import models

class MonthlyReport(models.Model):
    month_year = models.CharField(max_length=20, unique=True)
    
    # --- Network Inventory ---
    total_routers = models.IntegerField(default=0)
    total_switches = models.IntegerField(default=0)
    total_links = models.IntegerField(default=0)
    
    # --- NOC Activity & Automation (DRS010 Focus) ---
    critical_alarms_count = models.IntegerField(default=0)
    b2b_services_activated = models.IntegerField(default=0)  # Nouveaux clients B2B
    backhaul_upgrades_completed = models.IntegerField(default=0) # Upgrades de liens
    hardware_failures_recorded = models.IntegerField(default=0) # SFP/Cartes en panne
    provisioning_tasks_failed = models.IntegerField(default=0)
    provisioning_tasks_completed = models.IntegerField(default=0) # Le champ qui manquait !
    
    # --- The Actual File ---
    pdf_file = models.FileField(upload_to='reports/monthly/', null=True, blank=True)
    generated_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Network Report - {self.month_year}"
    
    class Meta:
        ordering = ['-generated_at']
