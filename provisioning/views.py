from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, viewsets
from .models import ProvisioningTask
from .serializers import ProvisioningTaskSerializer
from .tasks import run_provisioning


class ProvisioningTaskViewSet(viewsets.ReadOnlyModelViewSet):
    """List and retrieve provisioning tasks."""
    queryset = ProvisioningTask.objects.all()
    serializer_class = ProvisioningTaskSerializer


class StartProvisioningView(APIView):
    """
    POST /api/provisioning/start/
    Start a new provisioning task for a device.
    """
    def post(self, request):
        serializer = ProvisioningTaskSerializer(data=request.data)
        if serializer.is_valid():
            task = serializer.save()

            # Fire Celery task
            celery_task = run_provisioning.delay(task.id)

            # Save celery task ID
            task.celery_task_id = celery_task.id
            task.save()

            return Response({
                'task_id':        task.id,
                'celery_task_id': celery_task.id,
                'status':         task.status,
                'message':        f'Provisioning started for {task.device_name}'
            }, status=status.HTTP_202_ACCEPTED)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ProvisioningStatusView(APIView):
    """
    GET /api/provisioning/status/<task_id>/
    Get the current status of a provisioning task.
    """
    def get(self, request, task_id):
        try:
            task = ProvisioningTask.objects.get(id=task_id)
            return Response({
                'task_id':      task.id,
                'device_name':  task.device_name,
                'device_ip':    task.device_ip,
                'task_type':    task.task_type,
                'status':       task.status,
                'result':       task.result,
                'created_at':   task.created_at,
                'started_at':   task.started_at,
                'completed_at': task.completed_at,
            })
        except ProvisioningTask.DoesNotExist:
            return Response(
                {'error': 'Task not found'},
                status=status.HTTP_404_NOT_FOUND
            )

# Create your views here.
