import pandas as pd
from django.core.management.base import BaseCommand
from devices.models import Port, Card, SFP


class Command(BaseCommand):
    help = 'Import Port, Card and SFP data from Excel files'

    def handle(self, *args, **kwargs):

        # ── Import Ports ─────────────────────────────────────────
        self.stdout.write('Importing ports...')
        df = pd.read_excel('/home/roua/bd/Port_info.xlsx')
        count = 0
        for _, row in df.iterrows():
            try:
                Port.objects.update_or_create(
                    ne_name=str(row['NE']),
                    port_full_name=str(row['Port Full Name']) if str(row['Port Full Name']) != 'nan' else '',
                    defaults={
                        'ne_type':         str(row['NE Type (MPU TYPE)']) if str(row['NE Type (MPU TYPE)']) != 'nan' else '',
                        'port_name':       str(row['Port Name']) if str(row['Port Name']) != 'nan' else '',
                        'port_type':       str(row['Port Type']) if str(row['Port Type']) != 'nan' else '',
                        'port_rate':       str(row['Port Rate (kbit/s)']) if str(row['Port Rate (kbit/s)']) != 'nan' else '',
                        'port_level':      str(row['Port Level']) if str(row['Port Level']) != 'nan' else '',
                        'admin_status':    str(row['Administrative Status']).lower() if str(row['Administrative Status']) != 'nan' else 'inactive',
                        'oper_status':     str(row['Operational Status']).lower() if str(row['Operational Status']) != 'nan' else 'unknown',
                        'port_alias':      str(row['Port Alias']) if str(row['Port Alias']) != 'nan' else '',
                        'port_description':str(row['Port Description']) if str(row['Port Description']) != 'nan' else '',
                        'port_ip_address': str(row['Port IP Address']) if str(row['Port IP Address']) != 'nan' else '',
                    }
                )
                count += 1
            except Exception as e:
                self.stdout.write(self.style.WARNING(f'⚠️  Port skipped: {e}'))
        self.stdout.write(self.style.SUCCESS(f'✅ {count} ports imported'))

        # ── Import Cards ─────────────────────────────────────────
        self.stdout.write('Importing cards...')
        df = pd.read_excel('/home/roua/bd/Card_info.xlsx')
        count = 0
        for _, row in df.iterrows():
            try:
                Card.objects.update_or_create(
                    ne_name=str(row['NE Name']),
                    board_full_name=str(row['Board Full Name']) if str(row['Board Full Name']) != 'nan' else '',
                    slot_id=str(row['Slot ID']) if str(row['Slot ID']) != 'nan' else '',
                    defaults={
                        'ne_ip_address':  str(row['NE IP Address']) if str(row['NE IP Address']) != 'nan' else '',
                        'ne_type':        str(row['NE Type (MPU TYPE)']) if str(row['NE Type (MPU TYPE)']) != 'nan' else '',
                        'board_name':     str(row['Board Name']) if str(row['Board Name']) != 'nan' else '',
                        'board_type':     str(row['Board Type']) if str(row['Board Type']) != 'nan' else '',
                        'hardware_version':str(row['Hardware Version']) if str(row['Hardware Version']) != 'nan' else '',
                        'software_version':str(row['Software Version']) if str(row['Software Version']) != 'nan' else '',
                        'serial_number':  str(row['SN(Bar Code)']) if str(row['SN(Bar Code)']) != 'nan' else '',
                        'board_status':   str(row['Board Status']).lower() if str(row['Board Status']) != 'nan' else 'unknown',
                        'model':          str(row['Model']) if str(row['Model']) != 'nan' else '',
                        'description':    str(row['Description']) if str(row['Description']) != 'nan' else '',
                        'manufactured_on':str(row['Manufactured On']) if str(row['Manufactured On']) != 'nan' else '',
                    }
                )
                count += 1
            except Exception as e:
                self.stdout.write(self.style.WARNING(f'⚠️  Card skipped: {e}'))
        self.stdout.write(self.style.SUCCESS(f'✅ {count} cards imported'))

        # ── Import SFPs ──────────────────────────────────────────
        self.stdout.write('Importing SFPs...')
        df = pd.read_excel('/home/roua/bd/sfp_info.xlsx')
        count = 0
        for _, row in df.iterrows():
            try:
                SFP.objects.update_or_create(
                    ne_name=str(row['NE Name']),
                    port_name=str(row['Port Name']) if str(row['Port Name']) != 'nan' else '',
                    defaults={
                        'port_description':      str(row['Port Description']) if str(row['Port Description']) != 'nan' else '',
                        'port_type':             str(row['Port Type']) if str(row['Port Type']) != 'nan' else '',
                        'optical_type':          str(row['Optical/Electrical Type']) if str(row['Optical/Electrical Type']) != 'nan' else '',
                        'rx_power':              str(row['Receive Optical Power(dBm)']) if str(row['Receive Optical Power(dBm)']) != 'nan' else '',
                        'tx_power':              str(row['Transmit Optical Power(dBm)']) if str(row['Transmit Optical Power(dBm)']) != 'nan' else '',
                        'rx_status':             str(row['Receive Status']).lower() if str(row['Receive Status']) != 'nan' else 'unknown',
                        'tx_status':             str(row['Transmit Status']).lower() if str(row['Transmit Status']) != 'nan' else 'unknown',
                        'speed':                 str(row['Speed(Mb/s)']) if str(row['Speed(Mb/s)']) != 'nan' else '',
                        'fiber_type':            str(row['Fiber Type']) if str(row['Fiber Type']) != 'nan' else '',
                        'manufacturer':          str(row['Manufacturer']) if str(row['Manufacturer']) != 'nan' else '',
                        'serial_number':         str(row['Serial No']) if str(row['Serial No']) != 'nan' else '',
                        'wavelength':            str(row['Wave Length(nm)']) if str(row['Wave Length(nm)']) != 'nan' else '',
                        'transmission_distance': str(row['Transmission Distance(m)']) if str(row['Transmission Distance(m)']) != 'nan' else '',
                    }
                )
                count += 1
            except Exception as e:
                self.stdout.write(self.style.WARNING(f'⚠️  SFP skipped: {e}'))
        self.stdout.write(self.style.SUCCESS(f' {count} SFPs imported'))
