from django.db import models

class Router(models.Model):
    VENDOR_CHOICES = [
        ('huawei',  'Huawei'),
        ('cisco',   'Cisco'),
        ('juniper', 'Juniper'),
    ]
    ssh_username = models.CharField(max_length=100, null=True, blank=True)
    ssh_password = models.CharField(max_length=100, null=True, blank=True)

    name        = models.CharField(max_length=100, unique=True)
    loopback_ip = models.GenericIPAddressField(unique=True)
    model       = models.CharField(max_length=50)
    vendor      = models.CharField(max_length=20, choices=VENDOR_CHOICES)
    imported_at = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.name} — {self.loopback_ip} ({self.vendor})'

    class Meta:
        ordering = ['name']
    
class Switch(models.Model):
    name           = models.CharField(max_length=100, unique=True)
    loopback_ip    = models.GenericIPAddressField(unique=True)
    model          = models.CharField(max_length=50)
    interface_sw   = models.CharField(max_length=50, blank=True)
    connected_router = models.ForeignKey(
        Router,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='switches'
    )
    interface_rt   = models.CharField(max_length=50, blank=True)
    imported_at    = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.name} — {self.loopback_ip} ({self.model})'

    class Meta:
        ordering = ['name']
class Port(models.Model):

    STATUS_CHOICES = [
        ('up',       'Up'),
        ('down',     'Down'),
        ('unknown',  'Unknown'),
    ]

    ADMIN_CHOICES = [
        ('active',   'Active'),
        ('inactive', 'Inactive'),
    ]

    ne_name          = models.CharField(max_length=100)
    ne_type          = models.CharField(max_length=100, blank=True)
    port_full_name   = models.CharField(max_length=100, blank=True)
    port_name        = models.CharField(max_length=100, blank=True)
    port_type        = models.CharField(max_length=50, blank=True)
    port_rate        = models.CharField(max_length=20, blank=True)
    port_level       = models.CharField(max_length=10, blank=True)
    admin_status     = models.CharField(max_length=20, choices=ADMIN_CHOICES, default='inactive')
    oper_status      = models.CharField(max_length=20, choices=STATUS_CHOICES, default='unknown')
    port_alias       = models.CharField(max_length=200, blank=True)
    port_description = models.CharField(max_length=200, blank=True)
    port_ip_address  = models.CharField(max_length=50, blank=True)
    imported_at      = models.DateTimeField(auto_now_add=True)
    updated_at       = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.ne_name} — {self.port_full_name} ({self.oper_status})'

    class Meta:
        ordering = ['ne_name', 'port_full_name']


class Card(models.Model):

    STATUS_CHOICES = [
        ('normal',   'Normal'),
        ('abnormal', 'Abnormal'),
        ('unknown',  'Unknown'),
    ]

    ne_name          = models.CharField(max_length=100)
    ne_ip_address    = models.CharField(max_length=50, blank=True)
    ne_type          = models.CharField(max_length=100, blank=True)
    board_full_name  = models.CharField(max_length=100, blank=True)
    board_name       = models.CharField(max_length=100, blank=True)
    board_type       = models.CharField(max_length=100, blank=True)
    slot_id          = models.CharField(max_length=20, blank=True)
    hardware_version = models.CharField(max_length=200, blank=True)
    software_version = models.CharField(max_length=200, blank=True)
    serial_number    = models.CharField(max_length=200, blank=True)
    board_status     = models.CharField(max_length=200, choices=STATUS_CHOICES, default='normal')
    model            = models.CharField(max_length=200, blank=True)
    description      = models.TextField(blank=True)
    manufactured_on  = models.CharField(max_length=200, blank=True)
    imported_at      = models.DateTimeField(auto_now_add=True)
    updated_at       = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.ne_name} — {self.board_full_name} ({self.board_status})'

    class Meta:
        ordering = ['ne_name', 'board_full_name']


class SFP(models.Model):

    STATUS_CHOICES = [
        ('normal',   'Normal'),
        ('abnormal', 'Abnormal'),
        ('unknown',  'Unknown'),
    ]

    ne_name              = models.CharField(max_length=100)
    port_name            = models.CharField(max_length=100, blank=True)
    port_description     = models.CharField(max_length=200, blank=True)
    port_type            = models.CharField(max_length=50, blank=True)
    optical_type         = models.CharField(max_length=50, blank=True)
    rx_power             = models.CharField(max_length=20, blank=True)
    tx_power             = models.CharField(max_length=20, blank=True)
    rx_status            = models.CharField(max_length=20, choices=STATUS_CHOICES, default='normal')
    tx_status            = models.CharField(max_length=20, choices=STATUS_CHOICES, default='normal')
    speed                = models.CharField(max_length=20, blank=True)
    fiber_type           = models.CharField(max_length=20, blank=True)
    manufacturer         = models.CharField(max_length=100, blank=True)
    serial_number        = models.CharField(max_length=100, blank=True)
    wavelength           = models.CharField(max_length=20, blank=True)
    transmission_distance = models.CharField(max_length=20, blank=True)
    imported_at          = models.DateTimeField(auto_now_add=True)
    updated_at           = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.ne_name} — {self.port_name} ({self.rx_status})'

    class Meta:
        ordering = ['ne_name', 'port_name']
        verbose_name = 'SFP'
class SubCard(models.Model):

    STATUS_CHOICES = [
        ('normal',   'Normal'),
        ('abnormal', 'Abnormal'),
        ('unknown',  'Unknown'),
    ]

    ne_name          = models.CharField(max_length=100)
    ne_type          = models.CharField(max_length=100, blank=True)
    subboard_full_name = models.CharField(max_length=200, blank=True)
    subboard_name    = models.CharField(max_length=100, blank=True)
    subboard_type    = models.CharField(max_length=100, blank=True)
    subrack_id       = models.CharField(max_length=20, blank=True)
    slot_number      = models.CharField(max_length=20, blank=True)
    subslot_number   = models.CharField(max_length=20, blank=True)
    hardware_version = models.CharField(max_length=200, blank=True)
    software_version = models.CharField(max_length=200, blank=True)
    serial_number    = models.CharField(max_length=200, blank=True)
    subboard_status  = models.CharField(max_length=20, choices=STATUS_CHOICES, default='normal')
    description      = models.TextField(blank=True)
    model            = models.CharField(max_length=200, blank=True)
    manufactured_on  = models.CharField(max_length=200, blank=True)
    imported_at      = models.DateTimeField(auto_now_add=True)
    updated_at       = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.ne_name} — {self.subboard_full_name} ({self.subboard_status})'

    class Meta:
        ordering = ['ne_name', 'subboard_full_name']
        verbose_name = 'SubCard'
