import pandas as pd
from django.core.management.base import BaseCommand
from devices.models import Router, Switch


class Command(BaseCommand):
    help = 'Import routers and switches from RT_SW_DB.xlsx'

    def handle(self, *args, **kwargs):
        file_path = '/home/roua/bd/RT_SW_DB.xlsx'

        # ── Import Routers ──────────────────────────────────────
        self.stdout.write('Importing routers...')
        df_routers = pd.read_excel(file_path, sheet_name='Router DB')

        router_count = 0
        for _, row in df_routers.iterrows():
            Router.objects.update_or_create(
                name=row['Nom DEVICE'],
                defaults={
                    'loopback_ip': row['LOo DEVICE'],
                    'model':       str(row['Catégorie']),
                    'vendor':      str(row['Fournisseur']).lower(),
                }
            )
            router_count += 1

        self.stdout.write(self.style.SUCCESS(f'✅ {router_count} routers imported'))

        # ── Import Switches ─────────────────────────────────────
        self.stdout.write('Importing switches...')
        df_switches = pd.read_excel(file_path, sheet_name='switch DB')

        switch_count = 0
        for _, row in df_switches.iterrows():
            # Find connected router
            router_str = str(row['routeur'])
            router_name = router_str.split(' ')[0] if ' ' in router_str else router_str
            router = Router.objects.filter(name__icontains=router_name).first()

            Switch.objects.update_or_create(
                name=row['namedevice'],
                defaults={
                    'loopback_ip':       row['loopswitch'],
                    'model':             str(row['Modele']),
                    'interface_sw':      str(row['interfaceswrt']),
                    'connected_router':  router,
                    'interface_rt':      str(row['interfacertsw']),
                }
            )
            switch_count += 1

        self.stdout.write(self.style.SUCCESS(f'✅ {switch_count} switches imported'))
