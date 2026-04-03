from django.db import models

class BackhaulLink(models.Model):

    ALARM_CHOICES = [
        ('normal',   'Normal'),
        ('warning',  'Warning'),
        ('minor',    'Minor'),
        ('major',    'Major'),
        ('critical', 'Critical'),
    ]

    LINK_TYPE_CHOICES = [
        ('l2_link', 'L2 Link'),
        ('l3_link', 'L3 Link'),
    ]

    RATE_CHOICES = [
        ('1GE',  '1 GigabitEthernet'),
        ('10GE', '10 GigabitEthernet'),
        ('100GE','100 GigabitEthernet'),
    ]

    # Identity
    link_name      = models.CharField(max_length=255, unique=True)
    link_type      = models.CharField(max_length=20, choices=LINK_TYPE_CHOICES, default='l2_link')
    link_level     = models.CharField(max_length=10, choices=RATE_CHOICES, default='10GE')
    link_rate      = models.BigIntegerField(help_text="bit/s", null=True, blank=True)

    # Source device
    source_ne      = models.CharField(max_length=100)
    source_ip      = models.GenericIPAddressField(null=True, blank=True)
    source_port    = models.CharField(max_length=100)
    source_port_ip = models.CharField(max_length=50, blank=True)
    source_port_alias = models.CharField(max_length=100, blank=True)

    # Sink device
    sink_ne        = models.CharField(max_length=100)
    sink_ip        = models.GenericIPAddressField(null=True, blank=True)
    sink_port      = models.CharField(max_length=100)
    sink_port_ip   = models.CharField(max_length=50, blank=True)
    sink_port_alias = models.CharField(max_length=100, blank=True)

    # Status & monitoring
    alarm_severity         = models.CharField(max_length=20, choices=ALARM_CHOICES, default='normal')
    remaining_bw_upstream  = models.BigIntegerField(null=True, blank=True, help_text="Kbit/s")
    remaining_bw_downstream = models.BigIntegerField(null=True, blank=True, help_text="Kbit/s")
    delay_time             = models.IntegerField(null=True, blank=True, help_text="microseconds")

    # Metadata
    creation_time  = models.DateTimeField(null=True, blank=True)
    user_label     = models.CharField(max_length=100, blank=True)
    remarks        = models.TextField(blank=True)
    imported_at    = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.source_ne} → {self.sink_ne} ({self.alarm_severity})'

    class Meta:
        ordering = ['alarm_severity', 'source_ne']
