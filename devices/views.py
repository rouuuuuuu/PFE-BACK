from rest_framework.response import Response
from rest_framework import status
from django.shortcuts import render
from rest_framework import viewsets
from .models import Router, Switch , Port , Card, SFP
from .serializers import PortSerializer, CardSerializer, SFPSerializer, RouterSerializer , SwitchSerializer
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters
from rest_framework.views import APIView
from django.db.models import Count
from backhaul.models import BackhaulLink

class RouterViewSet(viewsets.ModelViewSet):
    queryset = Router.objects.all()
    serializer_class = RouterSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['vendor']  # Filter by vendor (Huawei/Cisco)
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
    filterset_fields = ['status']            # Filter ports by status (Up/Down)

class CardViewSet(viewsets.ModelViewSet):
    queryset = Card.objects.all()
    serializer_class = CardSerializer

class SFPViewSet(viewsets.ModelViewSet):
    queryset = SFP.objects.all()
    serializer_class = SFPSerializer

class HardwareVerifyView(APIView):
    """
    Custom endpoint to verify the hardware status of a specific device by its IP.
    URL: /api/hardware/verify/<device_ip>/
    """
    def get(self, request, device_ip):
        # 1. Find the Router by loopback_ip
        try:
            device = Router.objects.get(loopback_ip=device_ip) 
        except Router.DoesNotExist:
            return Response(
                {"error": f"Router with IP {device_ip} not found."}, 
                status=status.HTTP_404_NOT_FOUND
            )

        # 2. Query related hardware using 'ne_name' as the linking key
        # We match the device.name to the ne_name in the hardware tables
        ports = Port.objects.filter(ne_name=device.name)
        cards = Card.objects.filter(ne_name=device.name)
        sfps = SFP.objects.filter(ne_name=device.name)

        # 3. Calculate status logic based on your specific STATUS_CHOICES
        # Port is bad if oper_status is 'down'
        ports_ok = not ports.filter(oper_status='down').exists()
        
        # Card is bad if board_status is 'abnormal'
        cards_ok = not cards.filter(board_status='abnormal').exists()
        
        # SFP is bad if either rx_status or tx_status is 'abnormal'
        sfps_ok = not (sfps.filter(rx_status='abnormal').exists() or 
                       sfps.filter(tx_status='abnormal').exists())

        # 4. Construct the summary response
        overall_ok = all([ports_ok, cards_ok, sfps_ok])
        
        return Response({
            "device_name": device.name,
            "device_ip": device.loopback_ip,
            "vendor": device.vendor,
            "verification_results": {
                "ports": "OK" if ports_ok else "Alarm - Down ports detected",
                "cards": "OK" if cards_ok else "Alarm - Abnormal cards detected",
                "sfps": "OK" if sfps_ok else "Alarm - Abnormal SFPs detected"
            },
            "overall_status": "OK" if overall_ok else "Needs Attention",
            "counts": {
                "total_ports": ports.count(),
                "total_cards": cards.count(),
                "total_sfps": sfps.count()
            }
        }, status=status.HTTP_200_OK)
class DashboardStatsView(APIView):
    """
    GET /api/dashboard/stats/
    Returns aggregate stats for the NOC dashboard.
    """
    def get(self, request):
        # ── Devices ──────────────────────────────────────────
        total_routers  = Router.objects.count()
        total_switches = Switch.objects.count()

        routers_by_vendor = list(
            Router.objects.values('vendor')
            .annotate(count=Count('id'))
            .order_by('vendor')
        )

        # ── Backhaul Links ───────────────────────────────────
        total_links  = BackhaulLink.objects.count()
        normal_links = BackhaulLink.objects.filter(alarm_severity='normal').count()
        alarm_links  = total_links - normal_links

        links_by_alarm = list(
            BackhaulLink.objects.values('alarm_severity')
            .annotate(count=Count('id'))
            .order_by('alarm_severity')
        )

        # ── Hardware ─────────────────────────────────────────
        total_ports = Port.objects.count()
        ports_up    = Port.objects.filter(oper_status='up').count()
        ports_down  = Port.objects.filter(oper_status='down').count()

        total_cards    = Card.objects.count()
        cards_normal   = Card.objects.filter(board_status='normal').count()
        cards_abnormal = Card.objects.filter(board_status='abnormal').count()

        total_sfps    = SFP.objects.count()
        sfps_normal   = SFP.objects.filter(rx_status='normal').count()
        sfps_abnormal = SFP.objects.filter(rx_status='abnormal').count()

        return Response({
            "devices": {
                "routers":          total_routers,
                "switches":         total_switches,
                "routers_by_vendor": routers_by_vendor,
            },
            "backhaul_links": {
                "total":           total_links,
                "normal":          normal_links,
                "alarm":           alarm_links,
                "by_alarm_level":  links_by_alarm,
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
                }
            }
        }, status=status.HTTP_200_OK)
