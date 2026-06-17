from rest_framework import serializers
from .models import ProvisioningTask

class ProvisioningTaskSerializer(serializers.ModelSerializer):
    # Overriding device_ip to make it optional during validation, 
    # since the backend view now looks it up automatically.
    device_ip = serializers.IPAddressField(required=False, allow_blank=True)

    class Meta:
        model = ProvisioningTask
        fields = '__all__'
        read_only_fields = [
            'status', 'celery_task_id', 'result',
            'created_at', 'started_at', 'completed_at'
        ]
