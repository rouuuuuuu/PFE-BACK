from rest_framework import serializers
from .models import BackhaulLink

class BackhaulLinkSerializer(serializers.ModelSerializer):
    class Meta:
        model = BackhaulLink
        fields = '__all__'
        # Force ALL date fields to be read-only so the form ignores them completely
        read_only_fields = ['creation_time', 'imported_at', 'updated_at']
