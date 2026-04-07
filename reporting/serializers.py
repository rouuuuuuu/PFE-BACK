from rest_framework import serializers
from .models import MonthlyReport

class MonthlyReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = MonthlyReport
        fields = '__all__'
