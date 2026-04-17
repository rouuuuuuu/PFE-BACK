import redis
import logging
import time  # Added for simulation delay
from celery import shared_task
from django.utils import timezone
from netmiko import ConnectHandler
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
    # Use loopback_ip instead of ip_address
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

        # --- 3. Construct Commands ---
        commands = [
            f"interface {upgrade.interface}",
            f"bandwidth {upgrade.new_bandwidth_mbps * 1000}" 
        ]
        upgrade.generated_commands = "\n".join(commands)
        upgrade.save()

        # --- 4. Netmiko Execution (COMMENTED OUT FOR TESTING) ---
        """
        device_params = {
            'device_type': 'huawei', 
            'host': device.loopback_ip,
            'username': 'admin',  
            'password': 'password',
        }

        with ConnectHandler(**device_params) as conn:
            output = conn.send_config_set(commands)
            conn.save_config()
        """

        # --- FAKE TEST BLOCK (SIMULATION) ---
        time.sleep(5)  # Simulate 5 seconds of network configuration work
        output = (
            f"Connecting to {device.loopback_ip}...\n"
            f"Applying commands:\n{upgrade.generated_commands}\n"
            f"Configuration committed successfully."
        )
        # ------------------------------------

        # --- 5. Mark as Completed ---
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
        device_obj = Router.objects.get(id=device_id) # Using 'id' based on previous FieldError
        
        # --- Netmiko Execution (COMMENTED OUT FOR TESTING) ---
        """
        device_params = {
            'device_type': 'huawei', 
            'host': device_obj.loopback_ip,
            'username': 'admin',
            'password': 'password',
        }

        with ConnectHandler(**device_params) as conn:
            output = conn.send_command("display interface description")
        """

        # --- FAKE TEST BLOCK (SIMULATION) ---
        time.sleep(2)
        output = "Interface    Status    Description\nGE0/0/1      Up        To_Core\nGE0/0/2      Down      User_Access"
        # ------------------------------------
            
        return {'status': 'success', 'data': output}
    
    except Exception as e:
        return {'status': 'error', 'message': str(e)}
