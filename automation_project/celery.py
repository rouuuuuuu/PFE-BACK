import os
from celery import Celery
from celery.schedules import crontab
os.environ.setdefault('DJANGO_SETTINGS_MODULE',
'automation_project.settings')
app = Celery('automation_project')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()
# Tâches planifiées automatiques
app.conf.beat_schedule = {
'rapport-mensuel': {
'task': 'reporting.tasks.generate_monthly_report',
'schedule': crontab(day_of_month=1, hour=8, minute=0),
},
'supervision-liens': {
'task': 'monitoring.tasks.check_all_links',
'schedule': crontab(minute='*/5'), # toutes les 5 min
},
}
