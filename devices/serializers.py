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
