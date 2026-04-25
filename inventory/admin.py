from django.contrib import admin
from .models import StockItem

@admin.register(StockItem)
class StockItemAdmin(admin.ModelAdmin):
    # This makes the admin view look much more professional
    list_display = ('reference', 'name', 'classification', 'vendor', 'stock_qte', 'transfert_qte')
    search_fields = ('reference', 'name', 'vendor')
    list_filter = ('classification', 'vendor')
