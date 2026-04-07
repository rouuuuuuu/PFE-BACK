from rest_framework import serializers
from .models import ProvisioningTask

class ProvisioningTaskSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProvisioningTask
        fields = '__all__'
        read_only_fields = ['status', 'celery_task_id', 'result',
                           'created_at', 'started_at', 'completed_at']
