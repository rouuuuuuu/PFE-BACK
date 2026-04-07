from django.db import models


class ProvisioningTask(models.Model):

    STATUS_CHOICES = [
        ('queued',     'En attente'),
        ('running',    'En cours'),
        ('completed',  'Terminé'),
        ('failed',     'Échoué'),
    ]

    TYPE_CHOICES = [
        ('backhaul', 'Backhaul Link'),
        ('b2b',      'B2B Link'),
        ('internet', 'Internet Service'),
        ('voip',     'VoIP Service'),
        ('mpls',     'MPLS'),
        ('vpn',      'VPN'),
    ]

    device_name    = models.CharField(max_length=100)
    device_ip      = models.GenericIPAddressField()
    task_type      = models.CharField(max_length=20, choices=TYPE_CHOICES)
    status         = models.CharField(max_length=20, choices=STATUS_CHOICES, default='queued')
    celery_task_id = models.CharField(max_length=100, blank=True)
    parameters     = models.JSONField(default=dict)
    result         = models.TextField(blank=True)
    created_at     = models.DateTimeField(auto_now_add=True)
    started_at     = models.DateTimeField(null=True, blank=True)
    completed_at   = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'{self.device_name} — {self.task_type} ({self.status})'

    class Meta:
        ordering = ['-created_at']
