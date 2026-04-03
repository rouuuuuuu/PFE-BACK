from rest_framework.response import Response
from rest_framework import status
from django.shortcuts import render
from rest_framework import viewsets
from .models import Router, Switch , Port , Card, SFP
from .serializers import PortSerializer, CardSerializer, SFPSerializer, RouterSerializer , SwitchSerializer
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters
from rest_framework.views import APIView

class RouterViewSet(viewsets.ModelViewSet):
    queryset = Router.objects.all()
    serializer_class = RouterSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['vendor']  # Filter by vendor (Huawei/Cisco)
    search_fields = ['name', 'ip_address']   # Search by name or I

class SwitchViewSet(viewsets.ModelViewSet):
    queryset = Switch.objects.all()
    serializer_class = SwitchSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['vendor']
    search_fields = ['name', 'ip_address']

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
