from django.db import models
from devices.models import Router


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
        # --- NOUVEAUX CHOIX AJOUTÉS POUR ANGULAR ---
        ('configure_vlan', 'Configure VLAN'),
        ('firmware_upgrade', 'Firmware Upgrade'),
        ('push_acl', 'Push ACL'),
    ]

    device_name    = models.CharField(max_length=100)
    device_ip      = models.GenericIPAddressField()
    # max_length=20 est suffisant car "firmware_upgrade" fait 16 caractères
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
    
    
# Add these imports at the top
from devices.models import Router

# Add this model class
class BandwidthUpgrade(models.Model):
    """B2B Bandwidth Upgrade/Downgrade Task"""
    
    STATUS_CHOICES = [
        ('pending', 'En attente'),
        ('in_progress', 'En cours'),
        ('completed', 'Terminé'),
        ('failed', 'Échec'),
    ]
    
    # Primary key
    upgrade_id = models.AutoField(primary_key=True)
    
    # Device reference
    device = models.ForeignKey(
        Router, 
        on_delete=models.CASCADE, 
        related_name='bandwidth_upgrades'
    )
    
    # Interface details
    interface = models.CharField(
        max_length=50,
        help_text="Interface name (e.g., GigabitEthernet0/0/1 or ge-0/0/1)"
    )
    vlan = models.CharField(
        max_length=10, 
        null=True, 
        blank=True,
        help_text="VLAN unit for Cisco/Juniper"
    )
    
    # Customer info
    customer_name = models.CharField(max_length=200)
    
    # Bandwidth change
    old_bandwidth_mbps = models.IntegerField(
        null=True, 
        blank=True,
        help_text="Previous bandwidth in Mbps"
    )
    new_bandwidth_mbps = models.IntegerField(
        help_text="New bandwidth in Mbps"
    )
    
    # Task execution
    status = models.CharField(
        max_length=20, 
        choices=STATUS_CHOICES, 
        default='pending'
    )
    celery_task_id = models.CharField(
        max_length=255, 
        null=True, 
        blank=True
    )
    
    # Generated CLI output
    generated_commands = models.TextField(
        help_text="CLI commands that were/will be executed"
    )
    execution_output = models.TextField(
        null=True, 
        blank=True,
        help_text="SSH command output"
    )
    
    # SWAN integration (optional)
    swan_id = models.CharField(max_length=100, null=True, blank=True)
    swan_updated = models.BooleanField(default=False)
    
    # Audit trail
    created_by = models.ForeignKey(
        'auth.User',
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_upgrades'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        db_table = 'bandwidth_upgrades'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['device', 'interface']),
            models.Index(fields=['status']),
            models.Index(fields=['created_at']),
        ]
    
    def __str__(self):
        return f"Upgrade {self.upgrade_id} - {self.customer_name} ({self.new_bandwidth_mbps}Mbps)"
    
    @property
    def is_upgrade(self):
        """True if bandwidth increased, False if decreased, None if old_bandwidth unknown"""
        if self.old_bandwidth_mbps is None:
            return None
        return self.new_bandwidth_mbps > self.old_bandwidth_mbps
