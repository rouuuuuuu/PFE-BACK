from django.db import models

class StockItem(models.Model):
    # --- ARTICLE ---
    reference = models.CharField(max_length=100, unique=True, verbose_name="Réf.")
    name = models.CharField(max_length=255, verbose_name="Nom")
    classification = models.CharField(max_length=100, verbose_name="Classification")
    vendor = models.CharField(max_length=150, null=True, blank=True, verbose_name="Fournisseur")
    
    # --- STOCK ---
    stock_qte = models.IntegerField(default=0, verbose_name="Stock")

    # --- TRANSFERT ---
    transfert_nbr_u = models.IntegerField(default=0, verbose_name="Transfert (Nbr. U)")
    transfert_qte = models.IntegerField(default=0, verbose_name="Transfert (Qté)")

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.reference} - {self.name} (Stock: {self.stock_qte})"

    class Meta:
        ordering = ['classification', 'name']
