import redis
import logging
from celery import shared_task
from django.utils import timezone
from netmiko import ConnectHandler
from napalm import get_network_driver
from .models import BandwidthUpgrade

logger = logging.getLogger(__name__)
redis_client = redis.Redis(host='localhost', port=6379, db=0)


@shared_task(bind=True)
def execute_bandwidth_upgrade(self, upgrade_id):
    """
    Executes the bandwidth upgrade on the device with Redis locking.
    Supports both Huawei (Netmiko) and Juniper (Napalm).
    """
    try:
        upgrade = BandwidthUpgrade.objects.get(upgrade_id=upgrade_id)
    except BandwidthUpgrade.DoesNotExist:
        return "Upgrade record not found."

    device = upgrade.device
    lock_key = f'device_lock_{device.loopback_ip}'

    # --- 1. Acquire Redis Lock ---
    lock_acquired = redis_client.set(lock_key, str(upgrade_id), nx=True, ex=600)

    if not lock_acquired:
        upgrade.status = 'queued'
        upgrade.execution_output = f"Device {device.loopback_ip} is busy. Retrying..."
        upgrade.save()
        raise self.retry(countdown=120, max_retries=5)

    try:
        # --- 2. Mark as Running ---
        upgrade.status = 'running'
        upgrade.started_at = timezone.now()
        upgrade.save()

        # --- 3. Real Hardware Execution ---
        if device.vendor.lower() == 'huawei':
            device_params = {
                'device_type': 'huawei',
                'host': device.loopback_ip,
                'username': device.ssh_username,
                'password': device.ssh_password,
                'port': 22,
            }

            with ConnectHandler(**device_params) as conn:
                conn.send_command('system-view', expect_string=r'\[')
                conn.send_command(f'interface {upgrade.interface}', expect_string=r'\[')
                conn.send_command(f'bandwidth {upgrade.new_bandwidth_mbps * 1000}', expect_string=r'\[')
                output = conn.send_command('commit', expect_string=r'\[')  
                conn.send_command('return', expect_string=r'\>')

            upgrade.generated_commands = "\n".join([
                f"interface {upgrade.interface}",
                f"bandwidth {upgrade.new_bandwidth_mbps * 1000}",
                "commit",
                "return"
            ])

        elif device.vendor.lower() == 'juniper':
            driver = get_network_driver('junos')
            juniper_device = driver(
                hostname=device.loopback_ip,
                username=device.ssh_username,
                password=device.ssh_password
            )
            juniper_device.open()

            # Juniper traffic shaping is typically handled under class-of-service
            config_string = f'''
            set class-of-service interfaces {upgrade.interface} shaping-rate {upgrade.new_bandwidth_mbps}m
            '''
            
            juniper_device.load_merge_candidate(config=config_string)
            juniper_device.commit_config()
            juniper_device.close()

            output = f"Juniper Config Committed: shaped {upgrade.interface} to {upgrade.new_bandwidth_mbps}m"
            upgrade.generated_commands = config_string.strip()

        else:
            raise ValueError(f"Unsupported vendor: {device.vendor}")

        # --- 4. Mark as Completed ---
        upgrade.status = 'completed'
        upgrade.execution_output = output
        upgrade.completed_at = timezone.now()
        upgrade.save()

    except Exception as e:
        upgrade.status = 'failed'
        upgrade.execution_output = f"❌ Error: {str(e)}"
        upgrade.completed_at = timezone.now()
        upgrade.save()
        logger.error(f"Upgrade failed for {upgrade_id}: {str(e)}")

    finally:
        # --- 5. Always Release Lock ---
        redis_client.delete(lock_key)

    return upgrade.status

@shared_task
def fetch_device_interfaces(device_id):
    """
    Connects to a device to retrieve a list of active interfaces.
    Supports Huawei (Netmiko) and Juniper (NAPALM).
    """
    from devices.models import Router
    try:
        device = Router.objects.get(id=device_id)

        # ─── HUAWEI LOGIC ──────────────────────────────────────────
        if device.vendor.lower() == 'huawei':
            device_params = {
                'device_type': 'huawei',
                'host': device.loopback_ip,
                'username': device.ssh_username,
                'password': device.ssh_password,
                'port': 22,
            }

            with ConnectHandler(**device_params) as conn:
                output = conn.send_command("display interface description")

            return {'status': 'success', 'data': output}

        # ─── JUNIPER LOGIC ─────────────────────────────────────────
        elif device.vendor.lower() == 'juniper':
            driver = get_network_driver('junos')
            juniper_device = driver(
                hostname=device.loopback_ip,
                username=device.ssh_username,
                password=device.ssh_password,
                optional_args={'port': 22}
            )
            juniper_device.open()
            
            # Use NAPALM's cli() method to get the raw text output 
            # equivalent to Huawei's display command
            cli_result = juniper_device.cli(['show interfaces descriptions'])
            juniper_device.close()
            
            # NAPALM returns a dictionary where the key is the command executed
            output = cli_result.get('show interfaces descriptions', 'No output received.')

            return {'status': 'success', 'data': output}

        else:
            return {'status': 'error', 'message': f'Unsupported vendor: {device.vendor}'}

    except Exception as e:
        return {'status': 'error', 'message': str(e)}
@shared_task(bind=True)
def execute_port_reservation(self, port_id, router_id, description):
    """
    Executes a port reservation (no shut + description) with Redis locking.
    """
    from devices.models import Router, Port

    try:
        port = Port.objects.get(id=port_id)
        router = Router.objects.get(id=router_id)
    except (Port.DoesNotExist, Router.DoesNotExist):
        return "Port or Router record not found."

    lock_key = f'device_lock_{router.loopback_ip}'

    # --- 1. Acquire Redis Lock ---
    lock_acquired = redis_client.set(lock_key, f"port_res_{port_id}", nx=True, ex=300)

    if not lock_acquired:
        port.admin_status = 'inactive'
        port.save()
        logger.warning(f"Device {router.loopback_ip} is busy. Reservation for {port.port_full_name} queued/retrying.")
        raise self.retry(countdown=60, max_retries=3)

    try:
        # --- 3. Real Hardware Execution ---
        if router.vendor.lower() == 'huawei':
            device_params = {
                'device_type': 'huawei',
                'host': router.loopback_ip,
                'username': router.ssh_username,
                'password': router.ssh_password,
                'port': 22,
            }
            with ConnectHandler(**device_params) as net_connect:
                net_connect.send_command('system-view', expect_string=r'\[')
                net_connect.send_command(f'interface {port.port_full_name}', expect_string=r'\[')
                net_connect.send_command(f'description {description}', expect_string=r'\[')
                net_connect.send_command('undo shutdown', expect_string=r'\[')
                output = net_connect.send_command('commit', expect_string=r'\[')  # commit before return valable lel prsq all huawei routers 
                net_connect.send_command('return', expect_string=r'\>')
                logger.info(f"Huawei Output: {output}")

        elif router.vendor.lower() == 'juniper':
            driver = get_network_driver('junos')
            device = driver(
                hostname=router.loopback_ip,
                username=router.ssh_username,
                password=router.ssh_password
            )
            device.open()

            config_string = f'''
            delete interfaces {port.port_full_name} disable
            set interfaces {port.port_full_name} description "{description}"
            '''
            device.load_merge_candidate(config=config_string)
            device.commit_config()
            device.close()
            output = f"Juniper Config Committed for {port.port_full_name}"
            logger.info(output)

        else:
            raise ValueError(f"Unsupported vendor: {router.vendor}")

        # --- 4. Database Reconciliation (SUCCESS) ---
        port.admin_status = 'active'
        port.oper_status = 'up'
        port.port_description = description
        port.save()

        return f"Successfully provisioned {port.port_full_name}"

    except Exception as e:
        # --- 5. Database Reconciliation (FAILURE) ---
        logger.error(f"Failed to provision port {port.port_full_name}: {str(e)}")
        port.admin_status = 'inactive'
        port.save()
        raise e

    finally:
        # --- 6. Always Release Lock ---
        redis_client.delete(lock_key)
@shared_task(bind=True)
def execute_internet_provisioning(self, task_id, router_id, port_id):
    """
    Executes Internet Service provisioning reading from the generic ProvisioningTask model.
    """
    from devices.models import Router, Port, ProvisioningTask
    
    try:
        task = ProvisioningTask.objects.get(id=task_id)
        router = Router.objects.get(id=router_id)
        port = Port.objects.get(id=port_id)
    except Exception:
        return "Task, Router, or Port record not found."

    # Extract all parameters from the JSON field
    params = task.parameters
    client_name = params.get('client_name', 'Unknown')
    vlan = params.get('vlan')
    debit_mbps = int(params.get('debit_mbps', 0))
    pe_ip_address = params.get('pe_ip_address')
    subnet_mask = params.get('subnet_mask')
    subnet_type = params.get('subnet_type', '/31') # e.g. /31 or /29 for JunOS

    lock_key = f'device_lock_{router.loopback_ip}'
    lock_acquired = redis_client.set(lock_key, f"internet_task_{task_id}", nx=True, ex=300)

    if not lock_acquired:
        port.admin_status = 'inactive'
        port.save()
        logger.warning(f"Device {router.loopback_ip} busy. Retrying Internet provisioning.")
        raise self.retry(countdown=60, max_retries=3)

    try:
        task.status = 'running'
        task.started_at = timezone.now()
        task.save()

        # ─── HUAWEI LOGIC (Netmiko) ───
        if router.vendor.lower() == 'huawei':
            device_params = {
                'device_type': 'huawei',
                'host': router.loopback_ip,
                'username': router.ssh_username,
                'password': router.ssh_password,
                'port': 22,
            }
            
            sub_interface = f"{port.port_full_name}.{vlan}"
            commands = [
                'system-view',
                f'interface {sub_interface}',
                f'description B2B_INTERNET_{client_name}',
                f'vlan-type dot1q {vlan}',
                'ip binding vpn-instance INTERNET',
                f'ip address {pe_ip_address} {subnet_mask}',
                f'qos car inbound cir {debit_mbps * 1000}',
                f'qos car outbound cir {debit_mbps * 1000}',
                'commit',
                'return'
            ]

            with ConnectHandler(**device_params) as conn:
                output = conn.send_config_set(commands)
                logger.info(f"Huawei Internet Config: {output}")

        # ─── JUNIPER LOGIC (NAPALM) ───
        elif router.vendor.lower() == 'juniper':
            driver = get_network_driver('junos')
            juniper_device = driver(
                hostname=router.loopback_ip,
                username=router.ssh_username,
                password=router.ssh_password,
                optional_args={'port': 22}
            )
            juniper_device.open()

            junos_config = f'''
            set interfaces {port.port_full_name} vlan-tagging
            set interfaces {port.port_full_name} unit {vlan} description "B2B_INTERNET_{client_name}"
            set interfaces {port.port_full_name} unit {vlan} vlan-id {vlan}
            set interfaces {port.port_full_name} unit {vlan} family inet address {pe_ip_address}{subnet_type}
            set routing-instances INTERNET interface {port.port_full_name}.{vlan}
            set class-of-service interfaces {port.port_full_name} unit {vlan} shaping-rate {debit_mbps}m
            '''

            juniper_device.load_merge_candidate(config=junos_config)
            juniper_device.commit_config()
            juniper_device.close()
            output = f"Juniper Config Committed:\n{junos_config}"

        else:
            raise ValueError(f"Unsupported vendor: {router.vendor}")

        # ─── SUCCESS RECONCILIATION ───
        task.status = 'completed'
        task.result = output
        task.completed_at = timezone.now()
        task.save()

        port.admin_status = 'active'
        port.oper_status = 'up'
        port.port_description = f"B2B_INTERNET_{client_name}"
        port.save()

        return f"Internet provisioned for {client_name}"

    except Exception as e:
        # ─── FAILURE RECONCILIATION ───
        logger.error(f"Internet provisioning failed: {str(e)}")
        task.status = 'failed'
        task.result = str(e)
        task.completed_at = timezone.now()
        task.save()
        
        port.admin_status = 'inactive'
        port.save()
        raise e

    finally:
        redis_client.delete(lock_key)
