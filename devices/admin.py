from django.contrib import admin
from .models import Router, Switch, Port, Card, SFP, SubCard

@admin.register(Router)
class RouterAdmin(admin.ModelAdmin):
    list_display  = ['name', 'loopback_ip', 'model', 'vendor', 'imported_at']
    list_filter   = ['vendor', 'model']
    search_fields = ['name', 'loopback_ip']

@admin.register(Switch)
class SwitchAdmin(admin.ModelAdmin):
    list_display  = ['name', 'loopback_ip', 'model', 'connected_router']
    list_filter   = ['model']
    search_fields = ['name', 'loopback_ip']

@admin.register(Port)
class PortAdmin(admin.ModelAdmin):
    list_display  = ['ne_name', 'port_full_name', 'port_type', 'port_rate', 'admin_status', 'oper_status']
    list_filter   = ['oper_status', 'admin_status', 'port_type']
    search_fields = ['ne_name', 'port_full_name', 'port_description']

@admin.register(Card)
class CardAdmin(admin.ModelAdmin):
    list_display  = ['ne_name', 'board_full_name', 'board_type', 'slot_id', 'board_status']
    list_filter   = ['board_status']
    search_fields = ['ne_name', 'board_full_name', 'serial_number']

@admin.register(SFP)
class SFPAdmin(admin.ModelAdmin):
    list_display  = ['ne_name', 'port_name', 'optical_type', 'rx_power', 'tx_power', 'rx_status', 'tx_status']
    list_filter   = ['rx_status', 'tx_status', 'fiber_type']
    search_fields = ['ne_name', 'port_name', 'manufacturer', 'serial_number']

@admin.register(SubCard)
class SubCardAdmin(admin.ModelAdmin):
    list_display  = ['ne_name', 'subboard_full_name', 'subboard_type', 'slot_number', 'subslot_number', 'subboard_status']
    list_filter   = ['subboard_status']
    search_fields = ['ne_name', 'subboard_full_name', 'serial_number']
