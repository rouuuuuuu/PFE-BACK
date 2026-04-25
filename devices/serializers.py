from rest_framework import serializers
from .models import Router, Switch, Port, Card, SFP

class RouterSerializer(serializers.ModelSerializer):
    class Meta:
        model = Router
        fields = '__all__'

class SwitchSerializer(serializers.ModelSerializer):
    class Meta:
        model = Switch
        fields = '__all__'

class PortSerializer(serializers.ModelSerializer):
    class Meta:
        model = Port
        fields = '__all__'
        
class CardSerializer(serializers.ModelSerializer):
    class Meta:
        model = Card
        fields = '__all__'

class SFPSerializer(serializers.ModelSerializer):
    class Meta:
        model = SFP
        fields = '__all__'
        
class UnifiedDeviceDetailSerializer(serializers.Serializer):
    # Base Device Fields
    id = serializers.IntegerField()
    name = serializers.CharField()
    ip_address = serializers.CharField(source='loopback_ip')
    model = serializers.CharField()
    vendor = serializers.CharField(required=False, default='N/A') 
    device_type = serializers.SerializerMethodField()

    # Nested Hardware Lists
    cards = serializers.SerializerMethodField()
    ports = serializers.SerializerMethodField()
    sfps = serializers.SerializerMethodField()

    def get_device_type(self, obj):
        # Determine if it's a Router or Switch
        return 'Router' if isinstance(obj, Router) else 'Switch'

    def get_cards(self, obj):
        # Find Cards where ne_name matches this device's name
        cards = Card.objects.filter(ne_name=obj.name)
        return CardSerializer(cards, many=True).data

    def get_ports(self, obj):
        # Find Ports where ne_name matches this device's name
        ports = Port.objects.filter(ne_name=obj.name)
        return PortSerializer(ports, many=True).data

    def get_sfps(self, obj):
        # Find SFPs where ne_name matches this device's name
        sfps = SFP.objects.filter(ne_name=obj.name)
        return SFPSerializer(sfps, many=True).data
