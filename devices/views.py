from rest_framework.response import Response
from rest_framework import status
from django.shortcuts import render, get_object_or_404
from rest_framework import viewsets
from rest_framework.decorators import api_view
from .models import Router, Switch, Port, Card, SFP, SubCard
from .serializers import PortSerializer, CardSerializer, SFPSerializer, RouterSerializer, SwitchSerializer
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters
from rest_framework.views import APIView
from django.db.models import Count
from backhaul.models import BackhaulLink
from .serializers import UnifiedDeviceDetailSerializer
from itertools import chain


class UnifiedDeviceListView(APIView):
    def get(self, request):
        routers = Router.objects.all()
        switches = Switch.objects.all()
        combined_devices = list(chain(routers, switches))
        serializer = UnifiedDeviceDetailSerializer(combined_devices, many=True)
        return Response(serializer.data)


class RouterViewSet(viewsets.ModelViewSet):
    queryset = Router.objects.all()
    serializer_class = RouterSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['vendor']
    search_fields = ['name', 'loopback_ip']


class SwitchViewSet(viewsets.ModelViewSet):
    queryset = Switch.objects.all()
    serializer_class = SwitchSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['model']
    search_fields = ['name', 'loopback_ip']


class PortViewSet(viewsets.ModelViewSet):
    queryset = Port.objects.all()
    serializer_class = PortSerializer
    filterset_fields = ['oper_status']


class CardViewSet(viewsets.ModelViewSet):
    queryset = Card.objects.all()
    serializer_class = CardSerializer


class SFPViewSet(viewsets.ModelViewSet):
    queryset = SFP.objects.all()
    serializer_class = SFPSerializer


class HardwareVerifyView(APIView):
    def get(self, request, device_ip):
        try:
            device = Router.objects.get(loopback_ip=device_ip)
        except Router.DoesNotExist:
            return Response(
                {"error": f"Router with IP {device_ip} not found."},
                status=status.HTTP_404_NOT_FOUND
            )

        ports    = Port.objects.filter(ne_name=device.name)
        cards    = Card.objects.filter(ne_name=device.name)
        sfps     = SFP.objects.filter(ne_name=device.name)
        subcards = SubCard.objects.filter(ne_name=device.name)

        ports_ok    = not ports.filter(oper_status__iexact='down').exists()
        cards_ok    = not cards.filter(board_status__iexact='abnormal').exists()
        sfps_ok     = not (sfps.filter(rx_status__iexact='abnormal').exists() or
                           sfps.filter(tx_status__iexact='abnormal').exists())
        subcards_ok = not subcards.filter(subboard_status__iexact='abnormal').exists()

        overall_ok = all([ports_ok, cards_ok, sfps_ok, subcards_ok])

        # ── Port details ──────────────────────────────────────
        port_details = []
        for p in ports:
            port_details.append({
                "name":         p.port_full_name or p.port_name or 'Unknown',
                "status":       p.oper_status.lower(),
                "description":  p.port_description or '',
                "rate":         p.port_rate or 'N/A',
                "admin_status": p.admin_status or 'N/A',
                "port_type":    p.port_type or 'N/A',
                "port_ip":      p.port_ip_address or '',
            })

        # ── Card details ──────────────────────────────────────
        card_details = []
        for c in cards:
            card_details.append({
                "name":             c.board_full_name or c.board_name or 'Unknown',
                "status":           c.board_status.lower(),
                "description":      c.description or '',
                "board_type":       c.board_type or 'N/A',
                "slot_id":          c.slot_id or 'N/A',
                "hardware_version": c.hardware_version or 'N/A',
                "software_version": c.software_version or 'N/A',
                "serial_number":    c.serial_number or 'N/A',
                "manufactured_on":  c.manufactured_on or 'N/A',
            })

        # ── SFP details ───────────────────────────────────────
        sfp_details = []
        for s in sfps:
            rx = s.rx_status.lower()
            tx = s.tx_status.lower()
            sfp_details.append({
                "name":         s.port_name or 'Unknown',
                "status":       'abnormal' if rx == 'abnormal' or tx == 'abnormal' else 'normal',
                "description":  s.port_description or '',
                "speed":        s.speed or 'N/A',
                "manufacturer": s.manufacturer or 'N/A',
                "rx_power":     s.rx_power or 'N/A',
                "tx_power":     s.tx_power or 'N/A',
                "rx_status":    rx,
                "tx_status":    tx,
                "optical_type": s.optical_type or 'N/A',
                "fiber_type":   s.fiber_type or 'N/A',
                "wavelength":   s.wavelength or 'N/A',
            })

        # ── SubCard details ───────────────────────────────────
        subcard_details = []
        for sc in subcards:
            subcard_details.append({
                "name":             sc.subboard_full_name or sc.subboard_name or 'Unknown',
                "status":           sc.subboard_status.lower(),
                "subboard_type":    sc.subboard_type or 'N/A',
                "slot_number":      sc.slot_number or 'N/A',
                "subslot_number":   sc.subslot_number or 'N/A',
                "hardware_version": sc.hardware_version or 'N/A',
                "serial_number":    sc.serial_number or 'N/A',
                "description":      sc.description or '',
                "manufactured_on":  sc.manufactured_on or 'N/A',
            })

        return Response({
            "device_name": device.name,
            "device_ip":   device.loopback_ip,
            "vendor":      device.vendor,
            "verification_results": {
                "ports":    "OK" if ports_ok    else "Alarm - Down ports detected",
                "cards":    "OK" if cards_ok    else "Alarm - Abnormal cards detected",
                "sfps":     "OK" if sfps_ok     else "Alarm - Abnormal SFPs detected",
                "subcards": "OK" if subcards_ok else "Alarm - Abnormal subcards detected",
            },
            "overall_status": "OK" if overall_ok else "Needs Attention",
            "counts": {
                "total_ports":    ports.count(),
                "total_cards":    cards.count(),
                "total_sfps":     sfps.count(),
                "total_subcards": subcards.count(),
            },
            "port_details":    port_details,
            "card_details":    card_details,
            "sfp_details":     sfp_details,
            "subcard_details": subcard_details,
        }, status=status.HTTP_200_OK)

class DashboardStatsView(APIView):
    def get(self, request):
        
        # --- NEW: Helper Function for Hardware Breakdowns ---
        def get_top_breakdown(model, field_name, limit=4):
            """
            Groups by field_name, counts occurrences, sorts by highest count.
            Takes the top N (limit), and sums the rest into 'Other'.
            """
            queryset = model.objects.values(field_name).annotate(count=Count('id')).order_by('-count')
            
            breakdown = {}
            other_count = 0
            
            for index, item in enumerate(queryset):
                key = item[field_name]
                if not key or str(key).strip() == '':
                    key = 'Unknown'
                
                if index < limit:
                    breakdown[key] = item['count']
                else:
                    other_count += item['count']
                    
            if other_count > 0:
                breakdown['Other'] = other_count
                
            return breakdown

        # --- EXISTING: Basic Counts ---
        total_routers  = Router.objects.count()
        total_switches = Switch.objects.count()

        routers_by_vendor = list(
            Router.objects.values('vendor')
            .annotate(count=Count('id'))
            .order_by('vendor')
        )

        total_links  = BackhaulLink.objects.count()
        normal_links = BackhaulLink.objects.filter(alarm_severity='normal').count()
        alarm_links  = total_links - normal_links

        links_by_alarm = list(
            BackhaulLink.objects.values('alarm_severity')
            .annotate(count=Count('id'))
            .order_by('alarm_severity')
        )

        total_ports    = Port.objects.count()
        ports_up       = Port.objects.filter(oper_status='up').count()
        ports_down     = Port.objects.filter(oper_status='down').count()

        total_cards    = Card.objects.count()
        cards_normal   = Card.objects.filter(board_status='normal').count()
        cards_abnormal = Card.objects.filter(board_status='abnormal').count()

        total_sfps     = SFP.objects.count()
        sfps_normal    = SFP.objects.filter(rx_status='normal').count()
        sfps_abnormal  = SFP.objects.filter(rx_status='abnormal').count()

        total_subcards    = SubCard.objects.count()
        subcards_normal   = SubCard.objects.filter(subboard_status='normal').count()
        subcards_abnormal = SubCard.objects.filter(subboard_status='abnormal').count()

        # --- NEW: Generate Breakdowns ---
        hardware_breakdowns = {
            "ports": {
                "by_rate": get_top_breakdown(Port, 'port_rate', limit=4)
            },
            "cards": {
                "by_board_type": get_top_breakdown(Card, 'board_type', limit=4)
            },
            "sfps": {
                "by_type": get_top_breakdown(SFP, 'wavelength', limit=4)
            },
            "subcards": {
                "by_board_type": get_top_breakdown(SubCard, 'subboard_type', limit=4)
            }
        }

        # --- COMBINED RESPONSE ---
        return Response({
            "devices": {
                "routers":           total_routers,
                "switches":          total_switches,
                "routers_by_vendor": routers_by_vendor,
            },
            "backhaul_links": {
                "total":          total_links,
                "normal":         normal_links,
                "alarm":          alarm_links,
                "by_alarm_level": links_by_alarm,
            },
            "hardware": {
                "ports": {
                    "total": total_ports,
                    "up":    ports_up,
                    "down":  ports_down,
                },
                "cards": {
                    "total":    total_cards,
                    "normal":   cards_normal,
                    "abnormal": cards_abnormal,
                },
                "sfps": {
                    "total":    total_sfps,
                    "normal":   sfps_normal,
                    "abnormal": sfps_abnormal,
                },
                "subcards": {
                    "total":    total_subcards,
                    "normal":   subcards_normal,
                    "abnormal": subcards_abnormal,
                },
            },
            # Add the new breakdowns object here!
            "hardware_breakdowns": hardware_breakdowns
            
        }, status=status.HTTP_200_OK)
@api_view(['GET'])
def get_port_id(request):
    router_id = request.query_params.get('router_id')
    port_name = request.query_params.get('port_name')
    router = get_object_or_404(Router, id=router_id)
    port   = get_object_or_404(Port, ne_name=router.name, port_full_name=port_name)
    return Response({'port_id': port.id})
