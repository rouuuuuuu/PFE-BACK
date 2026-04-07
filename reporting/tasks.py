from celery import shared_task
from django.utils import timezone
from datetime import timedelta

# Import all your models
from .models import MonthlyReport
from devices.models import Router, Switch
from backhaul.models import BackhaulLink
from provisioning.models import ProvisioningTask

@shared_task
def generate_monthly_network_report():
    # 1. Calculate the target month (get the last day of the previous month)
    today = timezone.now()
    first_of_this_month = today.replace(day=1)
    last_month_date = first_of_this_month - timedelta(days=1)
    
    month_str = last_month_date.strftime("%B %Y") # e.g., "March 2026"

    # 2. Prevent duplicate reports
    if MonthlyReport.objects.filter(month_year=month_str).exists():
        return f"Report for {month_str} already exists."

    # 3. Snapshot the Network Inventory
    total_routers = Router.objects.count()
    total_switches = Switch.objects.count()
    total_links = BackhaulLink.objects.count()

    # 4. Snapshot the Alarms (e.g., Critical backhaul links)
    critical_alarms = BackhaulLink.objects.filter(alarm_severity='critical').count()

    # 5. Snapshot Provisioning Activity (Tasks created last month)
    tasks_last_month = ProvisioningTask.objects.filter(
        created_at__year=last_month_date.year,   # Changed from updated_at
        created_at__month=last_month_date.month  # Changed from updated_at
    )
    completed_tasks = tasks_last_month.filter(status='completed').count()
    failed_tasks = tasks_last_month.filter(status='failed').count()
    

    # 6. Save the Report to the Database
    report = MonthlyReport.objects.create(
        month_year=month_str,
        total_routers=total_routers,
        total_switches=total_switches,
        total_links=total_links,
        critical_alarms_count=critical_alarms,
        provisioning_tasks_completed=completed_tasks,
        provisioning_tasks_failed=failed_tasks
    )

    return f"Successfully generated report for {month_str} (ID: {report.id})"
