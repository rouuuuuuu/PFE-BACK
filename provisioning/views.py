from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, viewsets
from rest_framework.decorators import api_view
from django.shortcuts import get_object_or_404
from django.db import transaction

from .serializers import ProvisioningTaskSerializer
from .models import BandwidthUpgrade, ProvisioningTask
from .tasks import execute_bandwidth_upgrade, fetch_device_interfaces
from devices.models import Router


# ─────────────────────────────────────────────────────────────
#  BANDWIDTH UPGRADE — CREATE
# ─────────────────────────────────────────────────────────────
@api_view(['POST'])
def create_bandwidth_upgrade(request):
    """
    Initiate a bandwidth upgrade.
    POST /api/provisioning/bandwidth-upgrade/
    """
    data = request.data
    device_id    = data.get('device_id')
    interface    = data.get('interface')
    customer_name = data.get('customer_name')
    new_bandwidth = data.get('new_bandwidth_mbps')

    if not all([device_id, interface, customer_name, new_bandwidth]):
        return Response({'error': 'Missing required fields: device_id, interface, customer_name, new_bandwidth_mbps'}, status=400)

    device = get_object_or_404(Router, id=device_id)

    upgrade = BandwidthUpgrade.objects.create(
        device=device,
        interface=interface,
        vlan=data.get('vlan', ''),
        customer_name=customer_name,
        old_bandwidth_mbps=data.get('old_bandwidth_mbps'),
        new_bandwidth_mbps=new_bandwidth,
        created_by=request.user if request.user.is_authenticated else None,
        status='pending'
    )

    # ─── KEY FIX ─────────────────────────────────────────────
    # Fire the Celery task ONLY after the DB transaction commits.
    # Without this, Celery picks up the task before the upgrade
    # record exists in the database, causing a silent DoesNotExist
    # error that leaves the status stuck at 'pending' forever.
    # ─────────────────────────────────────────────────────────
    def fire_task():
        task = execute_bandwidth_upgrade.delay(upgrade.upgrade_id)
        # Store the celery task id after we have it
        BandwidthUpgrade.objects.filter(upgrade_id=upgrade.upgrade_id).update(
            celery_task_id=task.id
        )

    transaction.on_commit(fire_task)

    return Response({
        'status':     'initiated',
        'upgrade_id': upgrade.upgrade_id,
        'message':    'Upgrade queued. Celery task will start shortly.'
    }, status=status.HTTP_201_CREATED)


# ─────────────────────────────────────────────────────────────
#  BANDWIDTH UPGRADE — LIST
# ─────────────────────────────────────────────────────────────
@api_view(['GET'])
def list_bandwidth_upgrades(request):
    """
    List all bandwidth upgrades with optional filters.
    GET /api/provisioning/bandwidth-upgrades/?status=completed&device_id=1
    """
    upgrades = BandwidthUpgrade.objects.all().order_by('-created_at')

    status_filter = request.query_params.get('status')
    if status_filter:
        upgrades = upgrades.filter(status=status_filter)

    device_id = request.query_params.get('device_id')
    if device_id:
        upgrades = upgrades.filter(device_id=device_id)

    data = []
    for upgrade in upgrades:
        data.append({
            'upgrade_id':         upgrade.upgrade_id,
            'device_name':        upgrade.device.name,
            'device_ip':          upgrade.device.loopback_ip,
            'vendor':             upgrade.device.vendor,
            'interface':          upgrade.interface,
            'vlan':               upgrade.vlan,
            'customer_name':      upgrade.customer_name,
            'old_bandwidth_mbps': upgrade.old_bandwidth_mbps,
            'new_bandwidth_mbps': upgrade.new_bandwidth_mbps,
            'status':             upgrade.status,
            'is_upgrade':         upgrade.is_upgrade,
            'celery_task_id':     upgrade.celery_task_id,
            'generated_commands': upgrade.generated_commands,
            'execution_output':   upgrade.execution_output,
            'created_at':         upgrade.created_at.isoformat(),
            'started_at':         upgrade.started_at.isoformat() if upgrade.started_at else None,
            'completed_at':       upgrade.completed_at.isoformat() if upgrade.completed_at else None,
            'created_by':         upgrade.created_by.username if upgrade.created_by else None,
        })

    return Response(data)


# ─────────────────────────────────────────────────────────────
#  BANDWIDTH UPGRADE — DETAIL
# ─────────────────────────────────────────────────────────────
@api_view(['GET'])
def get_bandwidth_upgrade_detail(request, upgrade_id):
    """
    Get single upgrade detail.
    GET /api/provisioning/bandwidth-upgrades/{upgrade_id}/
    """
    upgrade = get_object_or_404(BandwidthUpgrade, upgrade_id=upgrade_id)

    return Response({
        'upgrade_id':         upgrade.upgrade_id,
        'device_name':        upgrade.device.name,
        'device_ip':          upgrade.device.loopback_ip,
        'vendor':             upgrade.device.vendor,
        'interface':          upgrade.interface,
        'vlan':               upgrade.vlan,
        'customer_name':      upgrade.customer_name,
        'old_bandwidth_mbps': upgrade.old_bandwidth_mbps,
        'new_bandwidth_mbps': upgrade.new_bandwidth_mbps,
        'status':             upgrade.status,
        'is_upgrade':         upgrade.is_upgrade,
        'celery_task_id':     upgrade.celery_task_id,
        'generated_commands': upgrade.generated_commands,
        'execution_output':   upgrade.execution_output,
        'created_at':         upgrade.created_at.isoformat(),
        'started_at':         upgrade.started_at.isoformat() if upgrade.started_at else None,
        'completed_at':       upgrade.completed_at.isoformat() if upgrade.completed_at else None,
        'created_by':         upgrade.created_by.username if upgrade.created_by else None,
    })


# ─────────────────────────────────────────────────────────────
#  FETCH INTERFACES
# ─────────────────────────────────────────────────────────────
@api_view(['POST'])
def fetch_interfaces(request):
    """
    Fetch interfaces from device via SSH (sync Celery task with timeout).
    POST /api/provisioning/fetch-interfaces/
    Body: { "device_id": 1 }
    """
    device_id = request.data.get('device_id')

    if not device_id:
        return Response(
            {'error': 'device_id is required'},
            status=status.HTTP_400_BAD_REQUEST
        )

    task = fetch_device_interfaces.delay(device_id)

    try:
        result = task.get(timeout=30)   # Wait max 30 seconds
        return Response(result)
    except Exception as e:
        return Response(
            {'status': 'error', 'message': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


# ─────────────────────────────────────────────────────────────
#  RETRY UPGRADE
# ─────────────────────────────────────────────────────────────
@api_view(['POST'])
def retry_upgrade(request, upgrade_id):
    """
    Retry a failed upgrade.
    POST /api/provisioning/bandwidth-upgrades/{upgrade_id}/retry/
    """
    upgrade = get_object_or_404(BandwidthUpgrade, upgrade_id=upgrade_id)

    if upgrade.status not in ['failed']:
        return Response(
            {'error': 'Only failed upgrades can be retried'},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Reset to pending
    upgrade.status           = 'pending'
    upgrade.execution_output = None
    upgrade.started_at       = None
    upgrade.completed_at     = None
    upgrade.save()

    # Same fix: fire after commit
    def fire_retry():
        task = execute_bandwidth_upgrade.delay(upgrade.upgrade_id)
        BandwidthUpgrade.objects.filter(upgrade_id=upgrade.upgrade_id).update(
            celery_task_id=task.id
        )

    transaction.on_commit(fire_retry)

    return Response({
        'message': 'Upgrade retry initiated',
    })


# ─────────────────────────────────────────────────────────────
#  PROVISIONING TASKS (existing)
# ─────────────────────────────────────────────────────────────
class ProvisioningTaskViewSet(viewsets.ReadOnlyModelViewSet):
    """List and retrieve provisioning tasks."""
    queryset            = ProvisioningTask.objects.all()
    serializer_class    = ProvisioningTaskSerializer


class StartProvisioningView(APIView):
    """
    POST /api/provisioning/start/
    Start a new provisioning task for a device.
    """
    def post(self, request):
        serializer = ProvisioningTaskSerializer(data=request.data)
        if serializer.is_valid():
            task = serializer.save()

            celery_task = run_provisioning.delay(task.id)

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

