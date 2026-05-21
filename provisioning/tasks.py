import redis
import logging
from celery import shared_task
from django.utils import timezone
from netmiko import ConnectHandler
from napalm import get_network_driver
from jinja2 import Template
from abc import ABC, abstractmethod
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

    lock_acquired = redis_client.set(lock_key, str(upgrade_id), nx=True, ex=600)

    if not lock_acquired:
        upgrade.status = 'queued'
        upgrade.execution_output = f"Device {device.loopback_ip} is busy. Retrying..."
        upgrade.save()
        raise self.retry(countdown=120, max_retries=5)

    try:
        upgrade.status = 'running'
        upgrade.started_at = timezone.now()
        upgrade.save()

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
        redis_client.delete(lock_key)

    return upgrade.status


@shared_task
def fetch_device_interfaces(device_id):
    """
    Connects to a device to retrieve a list of active interfaces.
    """
    from devices.models import Router
    try:
        device = Router.objects.get(id=device_id)

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

        elif device.vendor.lower() == 'juniper':
            driver = get_network_driver('junos')
            juniper_device = driver(
                hostname=device.loopback_ip,
                username=device.ssh_username,
                password=device.ssh_password,
                optional_args={'port': 22}
            )
            juniper_device.open()
            
            cli_result = juniper_device.cli(['show interfaces descriptions'])
            juniper_device.close()
            
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
    lock_acquired = redis_client.set(lock_key, f"port_res_{port_id}", nx=True, ex=300)

    if not lock_acquired:
        port.admin_status = 'inactive'
        port.save()
        logger.warning(f"Device {router.loopback_ip} is busy. Reservation queued.")
        raise self.retry(countdown=60, max_retries=3)

    try:
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
                output = net_connect.send_command('commit', expect_string=r'\[') 
                net_connect.send_command('return', expect_string=r'\>')

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

        else:
            raise ValueError(f"Unsupported vendor: {router.vendor}")

        port.admin_status = 'active'
        port.oper_status = 'up'
        port.port_description = description
        port.save()
        return f"Successfully provisioned {port.port_full_name}"

    except Exception as e:
        logger.error(f"Failed to provision port {port.port_full_name}: {str(e)}")
        port.admin_status = 'inactive'
        port.save()
        raise e

    finally:
        redis_client.delete(lock_key)


# ==========================================
# 1. BUSINESS LOGIC LAYER
# ==========================================
def build_provisioning_payload(port_name, params):
    """
    Extracts raw JSON parameters and calculates all necessary network variables.
    """
    subnet_type = params.get('subnet_type', '/31')
    media_type = params.get('media_type', 'fo').lower()
    debit_mbps = int(params.get('debit_mbps', 0))
    
    # Calculate QoS (e.g., 5% overhead reduction for Microwave/FH)
    qos_rate_mbps = int(debit_mbps * 0.95) if media_type == 'fh' else debit_mbps

    # Subnet mask mapping
    mask_map = {'/31': '255.255.255.254', '/29': '255.255.255.248', '/30': '255.255.255.252'}
    
    return {
        'client_name': params.get('client_name', 'Unknown'),
        'port_name': port_name,
        'vlan': params.get('vlan'),
        'pe_ip': params.get('pe_ip_address'),
        'subnet_cidr': subnet_type,
        'subnet_mask': mask_map.get(subnet_type, '255.255.255.0'),
        'qos_mbps': qos_rate_mbps,
        'qos_kbps': qos_rate_mbps * 1000,
        'media_type': media_type.upper(),
        'nat_mode': params.get('nat_mode'),
        'ce_ip': params.get('ce_ip_address'),
        'cust_lan_prefix': params.get('customer_lan_prefix', '0.0.0.0 0.0.0.0'),
        'cust_lan_cidr': params.get('customer_lan_cidr', '0.0.0.0/0'),
        'is_bundle': 'Eth-Trunk' in port_name or 'ae' in port_name.lower(),
        'has_switch': params.get('has_switch', False),
        'switch_ip': params.get('switch_ip')
    }

# ==========================================
# 2. VENDOR STRATEGY & TEMPLATE LAYER
# ==========================================
class ProvisioningDriver(ABC):
    def __init__(self, host, username, password):
        self.host = host
        self.username = username
        self.password = password

    @abstractmethod
    def generate_config(self, payload): pass

    @abstractmethod
    def execute(self, config_payload): pass


class HuaweiDriver(ProvisioningDriver):
    TEMPLATE = """
    system-view
    interface {{ port_name }}.{{ vlan }}
     description B2B_INTERNET_{{ media_type }}_{{ client_name }}
     vlan-type dot1q {{ vlan }}
     ip binding vpn-instance INTERNET
     ip address {{ pe_ip }} {{ subnet_mask }}
    {% if is_bundle %}
     qos car inbound cir {{ qos_kbps }}
     qos car outbound cir {{ qos_kbps }}
    {% else %}
     qos car inbound cir {{ qos_kbps }}
     qos car outbound cir {{ qos_kbps }}
    {% endif %}
     quit
    {% if nat_mode == 'sans_nat_avec_cpe' and ce_ip %}
    ip route-static vpn-instance INTERNET {{ cust_lan_prefix }} {{ ce_ip }}
    {% endif %}
    commit
    return
    """

    def generate_config(self, payload):
        template = Template(self.TEMPLATE.strip())
        return template.render(payload).split('\n')

    def execute(self, commands):
        device_params = {
            'device_type': 'huawei',
            'host': self.host,
            'username': self.username,
            'password': self.password,
            'port': 22,
        }
        with ConnectHandler(**device_params) as conn:
            output = conn.send_config_set(commands)
        return output


class JuniperDriver(ProvisioningDriver):
    TEMPLATE = """
    set interfaces {{ port_name }} vlan-tagging
    set interfaces {{ port_name }} unit {{ vlan }} description "B2B_INTERNET_{{ media_type }}_{{ client_name }}"
    set interfaces {{ port_name }} unit {{ vlan }} vlan-id {{ vlan }}
    set interfaces {{ port_name }} unit {{ vlan }} family inet address {{ pe_ip }}{{ subnet_cidr }}
    set routing-instances INTERNET interface {{ port_name }}.{{ vlan }}
    {% if is_bundle %}
    set class-of-service interfaces {{ port_name }} unit {{ vlan }} shaping-rate {{ qos_mbps }}m
    {% else %}
    set class-of-service interfaces {{ port_name }} unit {{ vlan }} shaping-rate {{ qos_mbps }}m
    {% endif %}
    {% if nat_mode == 'sans_nat_avec_cpe' and ce_ip %}
    set routing-instances INTERNET routing-options static route {{ cust_lan_cidr }} next-hop {{ ce_ip }}
    {% endif %}
    """

    def generate_config(self, payload):
        template = Template(self.TEMPLATE.strip())
        return template.render(payload)

    def execute(self, config_string):
        driver = get_network_driver('junos')
        with driver(hostname=self.host, username=self.username, password=self.password) as device:
            device.load_merge_candidate(config=config_string)
            device.commit_config()
        return f"Juniper Config Committed:\n{config_string}"

def get_vendor_driver(vendor, host, username, password):
    vendors = {
        'huawei': HuaweiDriver,
        'juniper': JuniperDriver
    }
    driver_class = vendors.get(vendor.lower())
    if not driver_class:
        raise ValueError(f"Unsupported vendor: {vendor}")
    return driver_class(host, username, password)

# ==========================================
# 3. CELERY COORDINATOR LAYER
# ==========================================
@shared_task(bind=True)
def execute_internet_provisioning(self, task_id, router_id, port_id):
    """
    Main entry point. Handles orchestration, topology checks, and state.
    """
    from devices.models import Router, Port, ProvisioningTask
    
    try:
        task = ProvisioningTask.objects.get(id=task_id)
        router = Router.objects.get(id=router_id)
        port = Port.objects.get(id=port_id)
    except Exception:
        return "Database records not found."

    payload = build_provisioning_payload(port.port_full_name, task.parameters)

    lock_key = f'device_lock_{router.loopback_ip}'
    if not redis_client.set(lock_key, f"internet_task_{task_id}", nx=True, ex=300):
        raise self.retry(countdown=60, max_retries=3)

    try:
        task.status = 'running'
        task.save()

        # Handle Switched Topology
        if payload.get('has_switch') and payload.get('switch_ip'):
            logger.info(f"Switched Topology: Triggering L2 VLAN {payload['vlan']} on Switch {payload['switch_ip']}")
            # execute_switch_l2_provisioning.delay(payload['switch_ip'], payload['vlan'], payload['client_name'])

        # Delegate to Vendor Driver
        driver = get_vendor_driver(
            router.vendor, 
            router.loopback_ip, 
            router.ssh_username, 
            router.ssh_password
        )
        
        config_data = driver.generate_config(payload)
        execution_output = driver.execute(config_data)

        # State Reconciliation (Success)
        task.status = 'completed'
        task.result = execution_output
        task.save()
        
        port.admin_status = 'active'
        port.port_description = f"B2B_INTERNET_{payload['media_type']}_{payload['client_name']}"
        port.save()

        return f"Internet provisioned for {payload['client_name']}"

    except Exception as e:
        logger.error(f"Provisioning failed on {router.loopback_ip}: {str(e)}")
        task.status = 'failed'
        task.result = str(e)
        task.save()
        raise e

    finally:
        redis_client.delete(lock_key)
