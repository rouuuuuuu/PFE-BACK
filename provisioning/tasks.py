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

        # --- 3. Real Netmiko Execution (NE40 VRP8 compatible) ---
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
            output = conn.send_command('commit', expect_string=r'\[')  # commit before return
            conn.send_command('return', expect_string=r'\>')

        upgrade.generated_commands = "\n".join([
            f"interface {upgrade.interface}",
            f"bandwidth {upgrade.new_bandwidth_mbps * 1000}",
            "commit",
            "return"
        ])
        upgrade.save()

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
        # --- 6. Always Release Lock ---
        redis_client.delete(lock_key)

    return upgrade.status


@shared_task
def fetch_device_interfaces(device_id):
    """
    Connects to a device to retrieve a list of active interfaces.
    """
    from devices.models import Router
    try:
        device_obj = Router.objects.get(id=device_id)

        device_params = {
            'device_type': 'huawei',
            'host': device_obj.loopback_ip,
            'username': device_obj.ssh_username,
            'password': device_obj.ssh_password,
            'port': 22,
        }

        with ConnectHandler(**device_params) as conn:
            output = conn.send_command("display interface description")

        return {'status': 'success', 'data': output}

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
                output = net_connect.send_command('commit', expect_string=r'\[')  # commit before return
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
