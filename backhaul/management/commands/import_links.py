import pandas as pd
from django.core.management.base import BaseCommand
from backhaul.models import BackhaulLink
from django.utils import timezone


class Command(BaseCommand):
    help = 'Import Backhaul links from ISIS_Link_info.xlsx'

    def handle(self, *args, **kwargs):
        file_path = '/home/roua/bd/ISIS_Link_info.xlsx'

        self.stdout.write('Importing Backhaul links...')
        df = pd.read_excel(file_path)

        count = 0
        errors = 0

        for _, row in df.iterrows():
            try:
                # Clean up bandwidth values
                remaining_up   = None if str(row['Remaining Upstream Bandwidth(Kbit/s)']) == '--' else row['Remaining Upstream Bandwidth(Kbit/s)']
                remaining_down = None if str(row['Remaining Downstream Bandwidth(Kbit/s)']) == '--' else row['Remaining Downstream Bandwidth(Kbit/s)']
                delay          = None if str(row['Delay Time(μs)']) == '--' else row['Delay Time(μs)']
                link_rate      = None if str(row['Link Rate(bit/s)']) == '--' else row['Link Rate(bit/s)']

                BackhaulLink.objects.update_or_create(
                    link_name=row['Link Name'],
                    defaults={
                        'link_type':               str(row['Link Type']).lower().replace(' ', '_'),
                        'link_level':              str(row['Link Level']),
                        'link_rate':               link_rate,
                        'source_ne':               str(row['Source NE']),
                        'source_ip':               str(row['Source IP']) if str(row['Source IP']) != '--' else None,
                        'source_port':             str(row['Source Port']),
                        'source_port_ip':          str(row['Source Port IP']) if str(row['Source Port IP']) != 'nan' else '',
                        'source_port_alias':       str(row['Source Port Alias']) if str(row['Source Port Alias']) != 'nan' else '',
                        'sink_ne':                 str(row['Sink NE']),
                        'sink_ip':                 str(row['Sink IP']) if str(row['Sink IP']) != '--' else None,
                        'sink_port':               str(row['Sink Port']),
                        'sink_port_ip':            str(row['Sink Port IP']) if str(row['Sink Port IP']) != 'nan' else '',
                        'sink_port_alias':         str(row['Sink Port Alias']) if str(row['Sink Port Alias']) != 'nan' else '',
                        'alarm_severity':          str(row['Alarm Severity']).lower(),
                        'remaining_bw_upstream':   remaining_up,
                        'remaining_bw_downstream': remaining_down,
                        'delay_time':              delay,
                        'user_label':              str(row['User Label']) if str(row['User Label']) != 'nan' else '',
                        'remarks':                 str(row['Remarks']) if str(row['Remarks']) != 'nan' else '',
                    }
                )
                count += 1
            except Exception as e:
                errors += 1
                self.stdout.write(self.style.WARNING(f'⚠️  Skipped row: {e}'))

        self.stdout.write(self.style.SUCCESS(f'✅ {count} links imported, {errors} skipped'))
