import csv
from django.shortcuts import render
from django.contrib import messages
from .models import BackhaulLink
from .serializers import BackhaulLinkSerializer
from rest_framework import viewsets, filters
from django_filters.rest_framework import DjangoFilterBackend

def import_isis_links(request):
    """
    Handles CSV upload, cleans incoming data, normalizes GE to 1GE,
    and performs a global cleanup of existing records.
    """
    if request.method == 'POST' and request.FILES.get('csv_file'):
        csv_file = request.FILES['csv_file']
        
        if not csv_file.name.endswith('.csv'):
            messages.error(request, 'This is not a valid CSV file.')
            return render(request, 'backhaul/import.html')

        # 1. Process the Uploaded CSV
        file_data = csv_file.read().decode('utf-8').splitlines()
        reader = csv.DictReader(file_data)
        
        imported_count = 0
        for row in reader:
            try:
                raw_source = row.get('Source Port', '').strip()
                raw_sink = row.get('Sink Port', '').strip()

                # Extract Link Level and normalize 'GE' to '1GE'
                raw_level = row.get('Link Level', '').strip()
                if raw_level.upper() == 'GE':
                    raw_level = '1GE'

                # Clean incoming strings for this row
                clean_source = raw_source.split('(')[1].split(')')[0] if '(' in raw_source else raw_source
                clean_sink = raw_sink.split('(')[1].split(')')[0] if '(' in raw_sink else raw_sink

                BackhaulLink.objects.update_or_create(
                    link_name=row.get('Link Name', '').strip(),
                    defaults={
                        'alarm_severity': row.get('Alarm Severity', 'Normal').lower(),
                        'source_ne': row.get('Source NE', '').strip(),
                        'source_port': clean_source,
                        'sink_ne': row.get('Sink NE', '').strip(),
                        'sink_port': clean_sink,
                        'link_level': raw_level, # Saves 1GE or whatever was in the CSV
                        'link_rate': row.get('Link Rate(bit/s)', '0').strip(),
                        'link_type': row.get('Link Type', '').strip(),
                    }
                )
                imported_count += 1
            except Exception as e:
                print(f"Error processing row: {str(e)}")
                continue

        # 2. Global Cleanup (The logic you provided + the new GE logic)
        # This ensures any records already in the DB are also cleaned
        all_links = BackhaulLink.objects.all()
        cleaned_in_db = 0
        for link in all_links:
            changed = False
            
            # Clean ports
            if link.source_port and '(' in link.source_port:
                link.source_port = link.source_port.split('(')[1].split(')')[0]
                changed = True
            if link.sink_port and '(' in link.sink_port:
                link.sink_port = link.sink_port.split('(')[1].split(')')[0]
                changed = True
            
            # Clean old 'GE' link levels in the database
            if link.link_level and link.link_level.upper() == 'GE':
                link.link_level = '1GE'
                changed = True
            
            if changed:
                link.save()
                cleaned_in_db += 1

        messages.success(request, f'Imported {imported_count} new links and cleaned {cleaned_in_db} existing records.')
        return render(request, 'backhaul/import.html')

    return render(request, 'backhaul/import.html')

# Keep your ViewSet below the import function
class BackhaulLinkViewSet(viewsets.ModelViewSet):
    queryset = BackhaulLink.objects.all().order_by('-id')
    serializer_class = BackhaulLinkSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['alarm_severity', 'link_type']
    search_fields = ['source_ne', 'sink_ne', 'link_name', 'source_port', 'sink_port']
