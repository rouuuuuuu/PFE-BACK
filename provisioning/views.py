from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, viewsets
from rest_framework.decorators import api_view
from django.shortcuts import get_object_or_404
from django.db import transaction

from .serializers import ProvisioningTaskSerializer
from .models import BandwidthUpgrade, ProvisioningTask

# --- ADDED: execute_port_reservation task import ---
from .tasks import execute_bandwidth_upgrade, fetch_device_interfaces, execute_port_reservation, execute_internet_provisioning 

# --- ADDED: Port model import ---
from devices.models import Router, Port


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
    Fetch interfaces from the database by matching the device's ne_name.
    """
    device_id = request.data.get('device_id')

    if not device_id:
        return Response({'error': 'device_id is required'}, status=400)

    # 1. Find the device (assuming Router for now, we will unify this later)
    device = get_object_or_404(Router, id=device_id)

    # 2. Find all ports where ne_name matches the device name
    db_ports = Port.objects.filter(ne_name=device.name)

    # 3. Translate Django Database keys to Angular Frontend keys
    formatted_interfaces = []
    for port in db_ports:
        is_up = port.oper_status.lower() == 'up'
        
        formatted_interfaces.append({
            'interface': port.port_full_name,        # Matches Angular 'interface'
            'description': port.port_description,    # Matches Angular 'description'
            'client_name': port.port_alias,          # Matches Angular 'client_name'
            'status': port.oper_status.upper(),      # Matches Angular 'status'
            'is_available': is_up                    # Matches Angular 'is_available'
        })

    return Response({
        'status': 'success',
        'interfaces': formatted_interfaces
    })


# ─────────────────────────────────────────────────────────────
#  PORT RESERVATION — CREATE
# ─────────────────────────────────────────────────────────────
@api_view(['POST'])
def reserve_port(request):
    """
    Reserves a port and triggers the background configuration task.
    POST /api/provisioning/reserve-port/
    """
    # --- GET DATA FROM FRONTEND ---
    router_id = request.data.get('router_id')
    port_id = request.data.get('port_id')  # <--- THIS WAS THE MISSING VARIABLE
    description = request.data.get('description', 'Reserved via Automation')

    # --- VALIDATE ---
    if not router_id or not port_id:
        return Response(
            {'error': 'router_id and port_id are required'}, 
            status=status.HTTP_400_BAD_REQUEST
        )

    router = get_object_or_404(Router, id=router_id)

    # 1. Start a database transaction to prevent Race Conditions
    with transaction.atomic():
        # select_for_update() locks the rows until the transaction finishes.
        available_port = Port.objects.select_for_update().filter(
            id=port_id,  # <-- Now this knows exactly what to look for!
            ne_name=router.name,
            admin_status='inactive',
            oper_status='down'
        ).first()

        if not available_port:
            return Response({
                'status': 'error', 
                'message': 'This specific port is not available (it must be down and inactive).'
            }, status=status.HTTP_404_NOT_FOUND)

        # 2. Lock the port temporarily so no one else can reserve it
        available_port.admin_status = 'pending_reservation'
        available_port.save()

    # 3. Fire the background Celery Task
    def fire_ssh_task():
        execute_port_reservation.delay(available_port.id, router.id, description)
    
    transaction.on_commit(fire_ssh_task)

    # 4. Immediately reply to the frontend
    return Response({
        'status': 'processing',
        'port_name': available_port.port_full_name,
        'message': f'Port {available_port.port_full_name} locked. Configuring router now...'
    }, status=status.HTTP_202_ACCEPTED)
# ─────────────────────────────────────────────────────────────
#  PORT RESERVATION — LIST (Le complément manquant)
# ─────────────────────────────────────────────────────────────
@api_view(['GET'])
def list_port_reservations(request):
    """
    Retourne la liste des réservations de ports.
    """
    # Ici, remplace 'PortReservation' par le nom de ton modèle s'il est différent
    # Si tu n'as pas de modèle dédié et que tu veux juste lister les ports réservés :
    from devices.models import Port
    
    reservations = Port.objects.filter(admin_status='pending_reservation').order_by('-id')
    
    data = []
    for p in reservations:
        data.append({
            'id': p.id,
            'port_name': p.port_full_name,
            'router': p.ne_name,
            'status': p.admin_status,
            'description': p.port_description
        })
        
    return Response(data)
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

            # Look up the router by device_name
            try:
                router = Router.objects.get(name=task.device_name)
            except Router.DoesNotExist:
                task.status = 'failed'
                task.save()
                return Response(
                    {'error': f'Device "{task.device_name}" not found in database.'},
                    status=status.HTTP_404_NOT_FOUND
                )

            # ─── INTERNET SERVICE ROUTING ───
            if task.task_type == 'internet':
                port_id = task.parameters.get('port_id')
                
                if not port_id:
                    return Response({'error': 'port_id is required in parameters for internet service'}, status=400)

                # Lock the port safely
                with transaction.atomic():
                    port = Port.objects.select_for_update().filter(
                        id=port_id, 
                        ne_name=router.name,
                        admin_status='inactive'
                    ).first()

                    if not port:
                        task.status = 'failed'
                        task.result = 'Port is already in use or does not exist.'
                        task.save()
                        return Response({'error': 'Port unavailable.'}, status=409)

                    # Lock it temporarily
                    port.admin_status = 'pending_provisioning'
                    port.save()

                def fire_internet_task():
                    celery_task = execute_internet_provisioning.delay(task.id, router.id, port.id)
                    ProvisioningTask.objects.filter(id=task.id).update(
                        celery_task_id=celery_task.id,
                        status='queued'
                    )

                transaction.on_commit(fire_internet_task)
                
                return Response({
                    'task_id': task.id,
                    'status': 'queued',
                    'message': f'Internet provisioning queued for {task.device_name}'
                }, status=status.HTTP_202_ACCEPTED)

            # ─── EXISTING TASK ROUTING ───
            elif task.task_type in ['configure_vlan', 'firmware_upgrade', 'push_acl']:
                port = Port.objects.filter(ne_name=router.name).first()

                if not port:
                    port = Port.objects.create(
                        ne_name=router.name,
                        port_full_name=task.parameters.get('interface', 'Ethernet1/0/1'),
                        port_name=task.parameters.get('interface', 'Ethernet1/0/1'),
                        admin_status='inactive',
                        oper_status='down'
                    )

                def fire_task():
                    celery_task = execute_port_reservation.delay(
                        port.id,
                        router.id,
                        f'{task.task_type} via NOC Dashboard'
                    )
                    ProvisioningTask.objects.filter(id=task.id).update(
                        celery_task_id=celery_task.id,
                        status='queued'
                    )

                transaction.on_commit(fire_task)

            else:
                task.status = 'failed'
                task.save()
                return Response(
                    {'error': f'Unknown task_type: {task.task_type}'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            return Response({
                'task_id':   task.id,
                'status':    'queued',
                'message':   f'Provisioning started for {task.device_name}'
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

