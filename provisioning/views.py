from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, viewsets
from rest_framework.decorators import api_view
from django.shortcuts import get_object_or_404
from django.db import transaction

from .serializers import ProvisioningTaskSerializer
from .models import BandwidthUpgrade, ProvisioningTask
from .tasks import (
    execute_bandwidth_upgrade,
    fetch_device_interfaces,
    execute_port_reservation,
    execute_internet_provisioning,
)
from devices.models import Router, Port


# ─────────────────────────────────────────────────────────────
#  BANDWIDTH UPGRADE — CREATE
# ─────────────────────────────────────────────────────────────
@api_view(['POST'])
def create_bandwidth_upgrade(request):
    """
    POST /api/provisioning/bandwidth-upgrade/
    Body: { device_id, interface, customer_name, new_bandwidth_mbps, [old_bandwidth_mbps, vlan] }
    """
    data          = request.data
    device_id     = data.get('device_id')
    interface     = data.get('interface')
    customer_name = data.get('customer_name')
    new_bandwidth = data.get('new_bandwidth_mbps')

    if not all([device_id, interface, customer_name, new_bandwidth]):
        return Response(
            {'error': 'Missing required fields: device_id, interface, customer_name, new_bandwidth_mbps'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    device  = get_object_or_404(Router, id=device_id)
    upgrade = BandwidthUpgrade.objects.create(
        device=device,
        interface=interface,
        vlan=data.get('vlan', ''),
        customer_name=customer_name,
        old_bandwidth_mbps=data.get('old_bandwidth_mbps'),
        new_bandwidth_mbps=new_bandwidth,
        created_by=request.user if request.user.is_authenticated else None,
        status='pending',
    )

    # Fire Celery task AFTER the DB transaction commits to avoid
    # DoesNotExist errors when the worker picks up the task before the row exists.
    def fire_task():
        task = execute_bandwidth_upgrade.delay(upgrade.upgrade_id)
        BandwidthUpgrade.objects.filter(upgrade_id=upgrade.upgrade_id).update(
            celery_task_id=task.id
        )

    transaction.on_commit(fire_task)

    return Response(
        {
            'status':     'initiated',
            'upgrade_id': upgrade.upgrade_id,
            'message':    'Upgrade queued. Celery task will start shortly.',
        },
        status=status.HTTP_201_CREATED,
    )


# ─────────────────────────────────────────────────────────────
#  BANDWIDTH UPGRADE — LIST
# ─────────────────────────────────────────────────────────────
@api_view(['GET'])
def list_bandwidth_upgrades(request):
    """
    GET /api/provisioning/bandwidth-upgrades/?status=completed&device_id=1
    """
    upgrades = BandwidthUpgrade.objects.all().order_by('-created_at')

    if status_filter := request.query_params.get('status'):
        upgrades = upgrades.filter(status=status_filter)

    if device_id := request.query_params.get('device_id'):
        upgrades = upgrades.filter(device_id=device_id)

    data = [
        {
            'upgrade_id':         u.upgrade_id,
            'device_name':        u.device.name,
            'device_ip':          u.device.loopback_ip,
            'vendor':             u.device.vendor,
            'interface':          u.interface,
            'vlan':               u.vlan,
            'customer_name':      u.customer_name,
            'old_bandwidth_mbps': u.old_bandwidth_mbps,
            'new_bandwidth_mbps': u.new_bandwidth_mbps,
            'status':             u.status,
            'is_upgrade':         u.is_upgrade,
            'celery_task_id':     u.celery_task_id,
            'generated_commands': u.generated_commands,
            'execution_output':   u.execution_output,
            'created_at':         u.created_at.isoformat(),
            'started_at':         u.started_at.isoformat() if u.started_at else None,
            'completed_at':       u.completed_at.isoformat() if u.completed_at else None,
            'created_by':         u.created_by.username if u.created_by else None,
        }
        for u in upgrades
    ]
    return Response(data)


# ─────────────────────────────────────────────────────────────
#  BANDWIDTH UPGRADE — DETAIL
# ─────────────────────────────────────────────────────────────
@api_view(['GET'])
def get_bandwidth_upgrade_detail(request, upgrade_id):
    """GET /api/provisioning/bandwidth-upgrades/{upgrade_id}/"""
    u = get_object_or_404(BandwidthUpgrade, upgrade_id=upgrade_id)
    return Response({
        'upgrade_id':         u.upgrade_id,
        'device_name':        u.device.name,
        'device_ip':          u.device.loopback_ip,
        'vendor':             u.device.vendor,
        'interface':          u.interface,
        'vlan':               u.vlan,
        'customer_name':      u.customer_name,
        'old_bandwidth_mbps': u.old_bandwidth_mbps,
        'new_bandwidth_mbps': u.new_bandwidth_mbps,
        'status':             u.status,
        'is_upgrade':         u.is_upgrade,
        'celery_task_id':     u.celery_task_id,
        'generated_commands': u.generated_commands,
        'execution_output':   u.execution_output,
        'created_at':         u.created_at.isoformat(),
        'started_at':         u.started_at.isoformat() if u.started_at else None,
        'completed_at':       u.completed_at.isoformat() if u.completed_at else None,
        'created_by':         u.created_by.username if u.created_by else None,
    })


# ─────────────────────────────────────────────────────────────
#  FETCH INTERFACES — LIVE FROM DEVICE
# ─────────────────────────────────────────────────────────────
@api_view(['POST'])
def fetch_interfaces(request):
    """
    POST /api/provisioning/fetch-interfaces/
    Body: { "device_id": 5 }

    SSHes into the device and runs 'display ip interface brief' (Huawei)
    or 'show interfaces terse' (Juniper).

    Called synchronously (no Celery) so the SYNC_RT button gets an
    immediate response to populate the interface dropdown.

    Returns:
    {
        "status": "success",
        "vendor": "huawei",
        "interfaces": [
            { "name": "GigabitEthernet0/3/18", "ip": "unassigned", "physical": "up", "protocol": "down", "is_subinterface": false },
            { "name": "GigabitEthernet0/3/18.500", "ip": "197.31.38.65/29", "physical": "up", "protocol": "up", "is_subinterface": true },
            ...
        ]
    }
    """
    # Accept both 'device_id' and 'router_id' — Angular sends router_id
    device_id = request.data.get('device_id') or request.data.get('router_id')
    if not device_id:
        return Response({'error': 'device_id or router_id is required'}, status=status.HTTP_400_BAD_REQUEST)

    device = get_object_or_404(Router, id=device_id)

    # Call the task function directly (no .delay()) for synchronous execution
    result = fetch_device_interfaces(device.id)

    if result['status'] != 'success':
        return Response(
            {'error': result.get('message', 'Failed to fetch interfaces')},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    # Parse the raw CLI output into structured objects the Angular frontend can consume
    raw    = result['data']
    vendor = result.get('vendor', 'huawei')

    interfaces = _parse_interfaces(raw, vendor)

    return Response({
        'status':     'success',
        'vendor':     vendor,
        'device_id':  device.id,
        'device_name': device.name,
        'interfaces': interfaces,
    })


def _parse_interfaces(raw: str, vendor: str) -> list:
    """
    Parses raw CLI output into a list of interface dicts.

    Huawei 'display interface description' columns:
      Interface   PHY   Protocol   Description

    Juniper 'show interfaces terse' columns:
      Interface   Admin   Link   Proto   Local   Remote
    """
    interfaces = []

    if vendor == 'huawei':
        for line in raw.splitlines():
            stripped = line.strip()

            # Skip blank lines, header, pagination prompt
            if not stripped:
                continue
            if stripped.startswith('Interface'):
                continue
            if 'More' in stripped or stripped.startswith('----'):
                continue
            if stripped.startswith('<') or stripped.startswith('['):
                continue

            # Must start with a letter to be an interface line
            if not stripped[0].isalpha():
                continue

            parts = stripped.split()

            # Need at least: name + PHY + Protocol
            if len(parts) < 3:
                continue

            name     = parts[0]
            physical = parts[1]   # PHY column  e.g. 'up' / 'down'
            protocol = parts[2]   # Protocol column
            desc     = ' '.join(parts[3:]) if len(parts) > 3 else ''

            # Skip internal/management interfaces
            if any(skip in name for skip in ('NULL', 'LoopBack', 'MEth', 'InLoopBack')):
                continue

            interfaces.append({
                'name':            name,
                'physical':        physical,
                'protocol':        protocol,
                'description':     desc,
                'is_subinterface': '.' in name,
            })

    elif vendor == 'juniper':
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped or not stripped[0].isalpha():
                continue

            parts = stripped.split()
            name  = parts[0]

            if any(skip in name for skip in (
                'lo0', 'fxp', 'bme', 'jsrv', 'dsc',
                'gre', 'ipip', 'lsi', 'mtun', 'pimd', 'pime', 'tap',
            )):
                continue

            physical = parts[1] if len(parts) > 1 else 'unknown'
            protocol = parts[2] if len(parts) > 2 else 'unknown'
            desc     = ' '.join(parts[5:]) if len(parts) > 5 else ''

            interfaces.append({
                'name':            name,
                'physical':        physical,
                'protocol':        protocol,
                'description':     desc,
                'is_subinterface': '.' in name,
            })

    return interfaces


# ─────────────────────────────────────────────────────────────
#  PORT RESERVATION — CREATE
# ─────────────────────────────────────────────────────────────
@api_view(['POST'])
def reserve_port(request):
    """
    POST /api/provisioning/reserve-port/
    Body: { "router_id": 5, "port_id": 12, "description": "..." }
    """
    router_id   = request.data.get('router_id')
    port_id     = request.data.get('port_id')
    description = request.data.get('description', 'Reserved via Automation')

    if not router_id or not port_id:
        return Response(
            {'error': 'router_id and port_id are required'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    router = get_object_or_404(Router, id=router_id)

    with transaction.atomic():
        available_port = Port.objects.select_for_update().filter(
            id=port_id,
            ne_name=router.name,
            admin_status='inactive',
            oper_status='down',
        ).first()

        if not available_port:
            return Response(
                {
                    'status':  'error',
                    'message': 'Port is not available (must be inactive and down).',
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        available_port.admin_status = 'pending_reservation'
        available_port.save()

    def fire_ssh_task():
        execute_port_reservation.delay(available_port.id, router.id, description)

    transaction.on_commit(fire_ssh_task)

    return Response(
        {
            'status':    'processing',
            'port_name': available_port.port_full_name,
            'message':   f'Port {available_port.port_full_name} locked. Configuring router now...',
        },
        status=status.HTTP_202_ACCEPTED,
    )


# ─────────────────────────────────────────────────────────────
#  PORT RESERVATION — LIST
# ─────────────────────────────────────────────────────────────
@api_view(['GET'])
def list_port_reservations(request):
    """GET /api/provisioning/port-reservations/"""
    reservations = Port.objects.filter(
        admin_status='pending_reservation'
    ).order_by('-id')

    data = [
        {
            'id':          p.id,
            'port_name':   p.port_full_name,
            'router':      p.ne_name,
            'status':      p.admin_status,
            'description': p.port_description,
        }
        for p in reservations
    ]
    return Response(data)


# ─────────────────────────────────────────────────────────────
#  RETRY UPGRADE
# ─────────────────────────────────────────────────────────────
@api_view(['POST'])
def retry_upgrade(request, upgrade_id):
    """POST /api/provisioning/bandwidth-upgrades/{upgrade_id}/retry/"""
    upgrade = get_object_or_404(BandwidthUpgrade, upgrade_id=upgrade_id)

    if upgrade.status != 'failed':
        return Response(
            {'error': 'Only failed upgrades can be retried'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    upgrade.status           = 'pending'
    upgrade.execution_output = None
    upgrade.started_at       = None
    upgrade.completed_at     = None
    upgrade.save()

    def fire_retry():
        task = execute_bandwidth_upgrade.delay(upgrade.upgrade_id)
        BandwidthUpgrade.objects.filter(upgrade_id=upgrade.upgrade_id).update(
            celery_task_id=task.id
        )

    transaction.on_commit(fire_retry)
    return Response({'message': 'Upgrade retry initiated'})


# ─────────────────────────────────────────────────────────────
#  INTERNET PROVISIONING — START
# ─────────────────────────────────────────────────────────────
class StartProvisioningView(APIView):
    """
    POST /api/provisioning/start/
    Body:
    {
        "device_name": "PE2",
        "device_ip": "192.168.65.143",
        "task_type": "internet",
        "parameters": {
            "port_id": 12,
            "client_name": "HOTEL-EL-MOURADI",
            "vlan": 500,
            "debit_mbps": 500,
            "vrf_name": "Internet_vpn",
            "media_type": "fh",
            "pe_ip_address": "197.31.38.65",
            "subnet_type": "/29",
            "nat_mode": "sans_nat_sans_cpe",
            "has_switch": false,
            ...
        }
    }
    """
    def post(self, request):
        serializer = ProvisioningTaskSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        task = serializer.save()

        try:
            router = Router.objects.get(name=task.device_name)
        except Router.DoesNotExist:
            task.status = 'failed'
            task.save()
            return Response(
                {'error': f'Device "{task.device_name}" not found in database.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # ── Internet service provisioning ───────────────────────────
        if task.task_type == 'internet':
            port_id = task.parameters.get('port_id')
            if not port_id:
                return Response(
                    {'error': 'port_id is required in parameters for internet service'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            with transaction.atomic():
                port = Port.objects.select_for_update().filter(
                    id=port_id,
                    ne_name=router.name,
                    admin_status='inactive',
                ).first()

                if not port:
                    task.status = 'failed'
                    task.result = 'Port is already in use or does not exist.'
                    task.save()
                    return Response({'error': 'Port unavailable.'}, status=status.HTTP_409_CONFLICT)

                port.admin_status = 'pending_provisioning'
                port.save()

            def fire_internet_task():
                celery_task = execute_internet_provisioning.delay(task.id, router.id, port.id)
                ProvisioningTask.objects.filter(id=task.id).update(
                    celery_task_id=celery_task.id,
                    status='queued',
                )

            transaction.on_commit(fire_internet_task)

            return Response(
                {
                    'task_id': task.id,
                    'status':  'queued',
                    'message': f'Internet provisioning queued for {task.device_name}',
                },
                status=status.HTTP_202_ACCEPTED,
            )

        # ── Legacy task types ───────────────────────────────────────
        elif task.task_type in ('configure_vlan', 'firmware_upgrade', 'push_acl'):
            port = Port.objects.filter(ne_name=router.name).first()
            if not port:
                port = Port.objects.create(
                    ne_name=router.name,
                    port_full_name=task.parameters.get('interface', 'Ethernet1/0/1'),
                    port_name=task.parameters.get('interface', 'Ethernet1/0/1'),
                    admin_status='inactive',
                    oper_status='down',
                )

            def fire_task():
                celery_task = execute_port_reservation.delay(
                    port.id,
                    router.id,
                    f'{task.task_type} via NOC Dashboard',
                )
                ProvisioningTask.objects.filter(id=task.id).update(
                    celery_task_id=celery_task.id,
                    status='queued',
                )

            transaction.on_commit(fire_task)

            return Response(
                {
                    'task_id': task.id,
                    'status':  'queued',
                    'message': f'Provisioning started for {task.device_name}',
                },
                status=status.HTTP_202_ACCEPTED,
            )

        else:
            task.status = 'failed'
            task.save()
            return Response(
                {'error': f'Unknown task_type: {task.task_type}'},
                status=status.HTTP_400_BAD_REQUEST,
            )


# ─────────────────────────────────────────────────────────────
#  PROVISIONING TASK — STATUS
# ─────────────────────────────────────────────────────────────
class ProvisioningStatusView(APIView):
    """GET /api/provisioning/status/<task_id>/"""
    def get(self, request, task_id):
        try:
            task = ProvisioningTask.objects.get(id=task_id)
        except ProvisioningTask.DoesNotExist:
            return Response({'error': 'Task not found'}, status=status.HTTP_404_NOT_FOUND)

        return Response({
            'task_id':       task.id,
            'device_name':   task.device_name,
            'device_ip':     task.device_ip,
            'task_type':     task.task_type,
            'status':        task.status,
            'result':        task.result,
            'script_output': getattr(task, 'script_output', None),  # downloadable script
            'created_at':    task.created_at,
            'started_at':    task.started_at,
            'completed_at':  task.completed_at,
        })


# ─────────────────────────────────────────────────────────────
#  PROVISIONING TASKS — VIEWSET (read-only list/retrieve)
# ─────────────────────────────────────────────────────────────
class ProvisioningTaskViewSet(viewsets.ReadOnlyModelViewSet):
    queryset         = ProvisioningTask.objects.all()
    serializer_class = ProvisioningTaskSerializer
