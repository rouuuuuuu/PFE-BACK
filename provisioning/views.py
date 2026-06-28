import logging
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
    execute_voip_provisioning,
    execute_voip_liberation,
    execute_port_liberation,
    execute_l2vc_provisioning,
    execute_l2vc_liberation,
    execute_internet_provisioning,
    discover_switch_via_lldp,
)
from devices.models import Router, Port

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
#  BANDWIDTH UPGRADE — CREATE
# ─────────────────────────────────────────────────────────────
@api_view(['POST'])
def create_bandwidth_upgrade(request):
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
#  FETCH INTERFACES — LIVE FROM DEVICE (UNTOUCHED)
# ─────────────────────────────────────────────────────────────
@api_view(['POST'])
def fetch_interfaces(request):
    device_id = request.data.get('device_id') or request.data.get('router_id')
    if not device_id:
        return Response(
            {'error': 'device_id or router_id is required'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    device = get_object_or_404(Router, id=device_id)
    result = fetch_device_interfaces(device.id)

    if result['status'] != 'success':
        return Response(
            {'error': result.get('message', 'Failed to fetch interfaces')},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    interfaces = _parse_interfaces(result['data'], result.get('vendor', 'huawei'))

    return Response({
        'status':      'success',
        'vendor':      result.get('vendor', 'huawei'),
        'device_id':   device.id,
        'device_name': device.name,
        'interfaces':  interfaces,
    })


def _parse_interfaces(raw: str, vendor: str) -> list:
    interfaces = []

    if vendor == 'huawei':
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith('Interface') or stripped.startswith('PHY'):
                continue
            if 'More' in stripped or stripped.startswith('----'):
                continue
            if stripped.startswith('<') or stripped.startswith('['):
                continue
            if not stripped[0].isalpha():
                continue

            parts = stripped.split()
            if len(parts) < 3:
                continue

            name     = parts[0]
            physical = parts[1]
            protocol = parts[2]
            desc     = ' '.join(parts[3:]) if len(parts) > 3 else ''

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
            if line.startswith(' ') or line.startswith('\t'):
                continue
            stripped = line.strip()
            if not stripped or not stripped[0].isalpha():
                continue

            parts = stripped.split()
            if len(parts) < 3:
                continue

            name = parts[0]
            if any(skip in name for skip in (
                'lo0', 'fxp', 'bme', 'jsrv', 'dsc', 'gre', 'ipip',
                'lsi', 'mtun', 'pimd', 'pime', 'tap', 'lc-', 'pfe-',
                'pfh-', 'irb', 'mif', 'pip', 'fti', 'cbp', 'demux',
                'em', 'esi', 'pp0', 'rbeb', 'vtep',
            )):
                continue

            interfaces.append({
                'name':            name,
                'physical':        parts[1],
                'protocol':        parts[2],
                'description':     ' '.join(parts[5:]) if len(parts) > 5 else '',
                'is_subinterface': '.' in name,
            })

    return interfaces


# ─────────────────────────────────────────────────────────────
#  GET PORT ID
# ─────────────────────────────────────────────────────────────
@api_view(['GET'])
def get_port_id(request):
    router_id = request.query_params.get('router_id')
    port_name = request.query_params.get('port_name')

    if not router_id or not port_name:
        return Response(
            {'error': 'router_id and port_name are required'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    router = get_object_or_404(Router, id=router_id)

    port, created = Port.objects.get_or_create(
        ne_name=router.name,
        port_full_name=port_name,
        defaults={
            'port_name':    port_name,
            'admin_status': 'inactive',
            'oper_status':  'down',
        }
    )

    return Response({
        'port_id': port.id,
        'created': created,
        'message': 'Port created.' if created else 'Port found.',
    })


# ─────────────────────────────────────────────────────────────
#  PORT RESERVATION — CREATE
# ─────────────────────────────────────────────────────────────
@api_view(['POST'])
def reserve_port(request):
    router_id      = request.data.get('router_id') or request.data.get('device_id')
    port_id        = request.data.get('port_id')
    interface_name = request.data.get('interface_name') or request.data.get('interface')
    description    = request.data.get('description', 'Reserved via Automation')

    if not router_id:
        return Response(
            {'error': 'router_id is required'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    router = get_object_or_404(Router, id=router_id)

    with transaction.atomic():
        port = None

        if port_id:
            port = Port.objects.select_for_update().filter(id=port_id).first()

        if not port and interface_name:
            port = Port.objects.select_for_update().filter(
                ne_name=router.name,
                port_full_name=interface_name,
            ).first()

        if not port and interface_name:
            port = Port.objects.create(
                ne_name=router.name,
                port_full_name=interface_name,
                port_name=interface_name,
                admin_status='inactive',
                oper_status='down',
            )

        if not port:
            return Response(
                {'error': 'Port not found and could not be created. Provide port_id or interface_name.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        if port.admin_status not in ('inactive', 'pending_reservation'):
            return Response(
                {'error': f'Port {port.port_full_name} is already active or allocated.'},
                status=status.HTTP_409_CONFLICT,
            )

        port.admin_status = 'pending_reservation'
        port.save()

    def fire_ssh_task():
        execute_port_reservation.delay(port.id, router.id, description)

    transaction.on_commit(fire_ssh_task)

    return Response(
        {
            'status':    'processing',
            'port_id':   port.id,
            'port_name': port.port_full_name,
            'message':   f'Port {port.port_full_name} locked. Configuring router now...',
        },
        status=status.HTTP_202_ACCEPTED,
    )


# ─────────────────────────────────────────────────────────────
#  PORT RESERVATION — LIST
# ─────────────────────────────────────────────────────────────
@api_view(['GET'])
def list_port_reservations(request):
    reservations = Port.objects.filter(
        admin_status='pending_reservation'
    ).order_by('-id')

    return Response([
        {
            'id':          p.id,
            'port_name':   p.port_full_name,
            'router':      p.ne_name,
            'status':      p.admin_status,
            'description': p.port_description,
        }
        for p in reservations
    ])


# ─────────────────────────────────────────────────────────────
#  RETRY UPGRADE
# ─────────────────────────────────────────────────────────────
@api_view(['POST'])
def retry_upgrade(request, upgrade_id):
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
#  SYNC_SW — DÉCOUVERTE DU SWITCH EN DIRECT DEPUIS EVE-NG
# ─────────────────────────────────────────────────────────────
@api_view(['POST'])
def fetch_switch_for_port(request):
    router_id = request.data.get('router_id')
    port_name = request.data.get('port_name')

    if not router_id or not port_name:
        return Response(
            {'error': 'router_id and port_name are required'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    result = discover_switch_via_lldp(router_id, port_name)

    if result['status'] == 'error':
        return Response(
            {'has_switch': False, 'message': result['message']},
            status=status.HTTP_502_BAD_GATEWAY
        )

    return Response({
        'has_switch':         result.get('has_switch', False),
        'switch_name':        result.get('switch_name', ''),
        'switch_ip':          result.get('switch_ip', ''),
        'switch_uplink_port': result.get('switch_uplink_port', 'ge-0/1/0'),
        'switch_port':        result.get('switch_port', ''),
        'switch_vendor':      result.get('switch_vendor', 'juniper'), 
        'message':            result.get('message', ''),
    }, status=status.HTTP_200_OK)


# ─────────────────────────────────────────────────────────────
#  INTERNET PROVISIONING — START
# ─────────────────────────────────────────────────────────────
class StartProvisioningView(APIView):
    def post(self, request):
        serializer = ProvisioningTaskSerializer(data=request.data)
        
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        device_name = serializer.validated_data.get('device_name')

        try:
            router = Router.objects.get(name__iexact=device_name)
        except Router.DoesNotExist:
            return Response(
                {'error': f'Device "{device_name}" not found in database.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        task = serializer.save(device_ip=router.loopback_ip)

        if task.task_type == 'internet':
            params = task.parameters if isinstance(task.parameters, dict) else {}
            
            port_id = params.get('port_id')
            if not port_id:
                task.delete()
                return Response(
                    {'error': 'port_id is required in parameters for internet provisioning'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # ── AUTOMATED BACKEND IP INJECTION ──
            # The backend is now fully responsible for defining the public IP block.
            # It no longer requires or validates a public_range from the frontend.
            
            # NOTE: If you eventually add an IPAM/Database model to pull available ranges, 
            # query it here. For now, we inject the master range automatically:
            automated_public_range = "196.203.0.0/24"
            
            params['public_range'] = automated_public_range
            task.parameters = params
            task.save()

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
                    return Response(
                        {'error': 'Port unavailable or already allocated.'},
                        status=status.HTTP_409_CONFLICT,
                    )

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
                    'message': f'Internet provisioning queued for {router.name}',
                },
                status=status.HTTP_202_ACCEPTED,
            )

        elif task.task_type in ('configure_vlan', 'firmware_upgrade', 'push_acl'):
            params = task.parameters if isinstance(task.parameters, dict) else {}
            
            port = Port.objects.filter(ne_name=router.name).first()
            if not port:
                port = Port.objects.create(
                    ne_name=router.name,
                    port_full_name=params.get('interface', 'Ethernet1/0/1'),
                    port_name=params.get('interface', 'Ethernet1/0/1'),
                    admin_status='inactive',
                    oper_status='down',
                )

            def fire_task():
                celery_task = execute_port_reservation.delay(
                    port.id, router.id,
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
                    'message': f'Provisioning started for {router.name}',
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
            'script_output': getattr(task, 'script_output', None),
            'created_at':    task.created_at,
            'started_at':    task.started_at,
            'completed_at':  task.completed_at,
        })

@api_view(['POST'])
def liberate_port(request, task_id):
    from provisioning.models import ProvisioningTask
    from devices.models import Port, Router

    task   = get_object_or_404(ProvisioningTask, id=task_id)
    router = task.router          # adjust to your actual FK field name
    port   = task.port            # adjust to your actual FK field name

    job = execute_port_liberation.delay(task.id, router.id, port.id)
    return Response({'job_id': job.id, 'status': 'queued'})
    
    
# ============================================================
# PASTE THIS BLOCK AT THE BOTTOM OF views.py
# Also add to the imports at the top of views.py:
#
#   from .tasks import (
#       ...existing imports...,
#       execute_voip_provisioning,
#       execute_voip_liberation,
#   )
# ============================================================


# ─────────────────────────────────────────────────────────────
#  VOIP PROVISIONING — START
# ─────────────────────────────────────────────────────────────
class StartVoIPProvisioningView(APIView):
    def post(self, request):
        serializer = ProvisioningTaskSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        device_name = serializer.validated_data.get('device_name')

        try:
            router = Router.objects.get(name__iexact=device_name)
        except Router.DoesNotExist:
            return Response(
                {'error': f'Device "{device_name}" not found in database.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        task = serializer.save(device_ip=router.loopback_ip)

        # Force task_type to 'voip' regardless of what was submitted
        task.task_type = 'voip'

        params = task.parameters if isinstance(task.parameters, dict) else {}

        port_id = params.get('port_id')
        if not port_id:
            task.delete()
            return Response(
                {'error': 'port_id is required in parameters for VoIP provisioning.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate required VoIP-specific fields
        pe_ip       = params.get('pe_ip_address') or params.get('pe_ip', '')
        subnet_mask = params.get('subnet_mask', '')
        vlan        = params.get('vlan')

        if not pe_ip:
            task.delete()
            return Response(
                {'error': '@IP GW (pe_ip_address) is required for VoIP provisioning.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not vlan:
            task.delete()
            return Response(
                {'error': 'vlan is required for VoIP provisioning.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        task.parameters = params
        task.save()

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
                return Response(
                    {'error': 'Port unavailable or already allocated.'},
                    status=status.HTTP_409_CONFLICT,
                )

            port.admin_status = 'pending_provisioning'
            port.save()

        def fire_voip_task():
            celery_task = execute_voip_provisioning.delay(task.id, router.id, port.id)
            ProvisioningTask.objects.filter(id=task.id).update(
                celery_task_id=celery_task.id,
                status='queued',
            )

        transaction.on_commit(fire_voip_task)

        return Response(
            {
                'task_id': task.id,
                'status':  'queued',
                'message': f'VoIP provisioning queued for {router.name}',
            },
            status=status.HTTP_202_ACCEPTED,
        )


# ─────────────────────────────────────────────────────────────
#  VOIP PORT LIBERATION
# ─────────────────────────────────────────────────────────────
@api_view(['POST'])
def liberate_voip_port(request, task_id):
    from provisioning.models import ProvisioningTask
    from devices.models import Port, Router

    task = get_object_or_404(ProvisioningTask, id=task_id)

    if task.task_type != 'voip':
        return Response(
            {'error': f'Task {task_id} is not a VoIP task (type={task.task_type}).'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if task.status not in ('completed', 'failed'):
        return Response(
            {'error': f'Task must be completed or failed to liberate. Current status: {task.status}'},
            status=status.HTTP_409_CONFLICT,
        )

    params    = task.parameters if isinstance(task.parameters, dict) else {}
    port_id   = params.get('port_id')
    router_ip = task.device_ip

    if not port_id:
        return Response(
            {'error': 'port_id not found in task parameters.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        router = Router.objects.get(loopback_ip=router_ip)
    except Router.DoesNotExist:
        return Response(
            {'error': f'Router with IP {router_ip} not found.'},
            status=status.HTTP_404_NOT_FOUND,
        )

    try:
        port = Port.objects.get(id=port_id)
    except Port.DoesNotExist:
        return Response(
            {'error': f'Port {port_id} not found.'},
            status=status.HTTP_404_NOT_FOUND,
        )

    job = execute_voip_liberation.delay(task.id, router.id, port.id)

    return Response({
        'job_id':  job.id,
        'status':  'queued',
        'message': f'VoIP liberation queued for port {port.port_full_name}',
    })
    
# ─────────────────────────────────────────────────────────────
#  L2VC PROVISIONING — START
# ─────────────────────────────────────────────────────────────
class StartL2VCProvisioningView(APIView):
    def post(self, request):
        serializer = ProvisioningTaskSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        device_name = serializer.validated_data.get('device_name')

        try:
            router = Router.objects.get(name__iexact=device_name)
        except Router.DoesNotExist:
            return Response(
                {'error': f'Device "{device_name}" not found in database.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        task           = serializer.save(device_ip=router.loopback_ip)
        task.task_type = 'l2vc'

        params  = task.parameters if isinstance(task.parameters, dict) else {}
        port_id = params.get('port_id')
        vlan    = params.get('vlan')

        if not port_id:
            task.delete()
            return Response(
                {'error': 'port_id is required for L2VC provisioning.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not vlan:
            task.delete()
            return Response(
                {'error': 'vlan is required for L2VC provisioning.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        remote_device_name = params.get('remote_device_name', '')
        if not remote_device_name and not params.get('peer_ip'):
            task.delete()
            return Response(
                {'error': 'remote_device_name or peer_ip is required for L2VC provisioning.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        task.parameters = params
        task.save()

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
                return Response(
                    {'error': 'Port unavailable or already allocated.'},
                    status=status.HTTP_409_CONFLICT,
                )

            port.admin_status = 'pending_provisioning'
            port.save()

        def fire_l2vc_task():
            celery_task = execute_l2vc_provisioning.delay(task.id, router.id, port.id)
            ProvisioningTask.objects.filter(id=task.id).update(
                celery_task_id=celery_task.id,
                status='queued',
            )

        transaction.on_commit(fire_l2vc_task)

        return Response(
            {
                'task_id': task.id,
                'status':  'queued',
                'message': f'L2VC provisioning queued for {router.name}',
            },
            status=status.HTTP_202_ACCEPTED,
        )


# ─────────────────────────────────────────────────────────────
#  L2VC PORT LIBERATION
# ─────────────────────────────────────────────────────────────
@api_view(['POST'])
def liberate_l2vc_port(request, task_id):
    from provisioning.models import ProvisioningTask
    from devices.models import Port, Router

    task = get_object_or_404(ProvisioningTask, id=task_id)

    if task.task_type != 'l2vc':
        return Response(
            {'error': f'Task {task_id} is not an L2VC task (type={task.task_type}).'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if task.status not in ('completed', 'failed'):
        return Response(
            {'error': f'Task must be completed or failed to liberate. Current status: {task.status}'},
            status=status.HTTP_409_CONFLICT,
        )

    params    = task.parameters if isinstance(task.parameters, dict) else {}
    port_id   = params.get('port_id')
    router_ip = task.device_ip

    if not port_id:
        return Response(
            {'error': 'port_id not found in task parameters.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        router = Router.objects.get(loopback_ip=router_ip)
    except Router.DoesNotExist:
        return Response(
            {'error': f'Router with IP {router_ip} not found.'},
            status=status.HTTP_404_NOT_FOUND,
        )

    try:
        port = Port.objects.get(id=port_id)
    except Port.DoesNotExist:
        return Response(
            {'error': f'Port {port_id} not found.'},
            status=status.HTTP_404_NOT_FOUND,
        )

    job = execute_l2vc_liberation.delay(task.id, router.id, port.id)

    return Response({
        'job_id':  job.id,
        'status':  'queued',
        'message': f'L2VC liberation queued for port {port.port_full_name}',
    })
# ─────────────────────────────────────────────────────────────
#  PROVISIONING TASKS — VIEWSET
# ─────────────────────────────────────────────────────────────
class ProvisioningTaskViewSet(viewsets.ReadOnlyModelViewSet):
    queryset         = ProvisioningTask.objects.all()
    serializer_class = ProvisioningTaskSerializer
