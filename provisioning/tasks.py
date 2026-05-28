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
            import time
            device_params = {
                'device_type': 'huawei',
                'host': device.loopback_ip,
                'username': device.ssh_username,
                'password': device.ssh_password,
                'port': 22,
                'global_delay_factor': 2,
            }
            output_lines = []

            def _send(conn, cmd, sleep=2, check_error=True):
                conn.write_channel(cmd + '\n')
                time.sleep(sleep)
                out = conn.read_channel()
                output_lines.append(f'[{cmd}] => {out.strip()}')
                if check_error and ('Error' in out or 'error' in out):
                    raise RuntimeError(f'Device error on "{cmd}": {out.strip()}')
                return out

            with ConnectHandler(**device_params) as conn:
                conn.write_channel('system-view\n')
                time.sleep(3)
                conn.read_channel()

                _send(conn, f'interface {upgrade.interface}', sleep=3)
                _send(conn, f'bandwidth {upgrade.new_bandwidth_mbps * 1000}')

                conn.write_channel('quit\n')
                time.sleep(2)
                conn.read_channel()

                conn.write_channel('commit\n')
                time.sleep(6)
                out = conn.read_channel()
                output_lines.append(f'[commit] => {out.strip()}')
                if 'Error' in out or 'error' in out:
                    raise RuntimeError(f'Commit failed: {out.strip()}')

                conn.write_channel('return\n')
                time.sleep(2)
                conn.read_channel()

            output = "\n".join(output_lines)
            upgrade.generated_commands = "\n".join([
                "system-view",
                f"interface {upgrade.interface}",
                f"bandwidth {upgrade.new_bandwidth_mbps * 1000}",
                "commit",
                "return",
            ])

        elif device.vendor.lower() == 'juniper':
            driver = get_network_driver('junos')
            juniper_device = driver(
                hostname=device.loopback_ip,
                username=device.ssh_username,
                password=device.ssh_password,
                optional_args={'port': 830},
            )
            juniper_device.open()

            config_string = (
                f"set class-of-service interfaces {upgrade.interface} "
                f"shaping-rate {upgrade.new_bandwidth_mbps}m"
            )
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
                optional_args={'port': 830},
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
            import time
            device_params = {
                'device_type': 'huawei',
                'host': router.loopback_ip,
                'username': router.ssh_username,
                'password': router.ssh_password,
                'port': 22,
                'global_delay_factor': 2,
            }
            output_lines = []

            def _send(conn, cmd, sleep=2, check_error=True):
                conn.write_channel(cmd + '\n')
                time.sleep(sleep)
                out = conn.read_channel()
                output_lines.append(f'[{cmd}] => {out.strip()}')
                if check_error and ('Error' in out or 'error' in out):
                    raise RuntimeError(f'Device error on "{cmd}": {out.strip()}')
                return out

            with ConnectHandler(**device_params) as conn:
                conn.write_channel('system-view\n')
                time.sleep(3)
                conn.read_channel()

                _send(conn, f'interface {port.port_full_name}', sleep=3)
                _send(conn, f'description {description}')
                _send(conn, 'undo shutdown')

                conn.write_channel('quit\n')
                time.sleep(2)
                conn.read_channel()

                conn.write_channel('commit\n')
                time.sleep(6)
                out = conn.read_channel()
                output_lines.append(f'[commit] => {out.strip()}')
                if 'Error' in out or 'error' in out:
                    raise RuntimeError(f'Commit failed: {out.strip()}')

                conn.write_channel('return\n')
                time.sleep(2)
                conn.read_channel()

        elif router.vendor.lower() == 'juniper':
            driver = get_network_driver('junos')
            device = driver(
                hostname=router.loopback_ip,
                username=router.ssh_username,
                password=router.ssh_password,
                optional_args={'port': 830},
            )
            device.open()

            junos_port = port.port_full_name[:-2] if port.port_full_name.endswith('.0') else port.port_full_name

            try:
                config_string = '\n'.join([
                    f'delete interfaces {junos_port} disable',
                    f'set interfaces {junos_port} description "{description}"',
                ])
                device.load_merge_candidate(config=config_string)
            except Exception as e:
                if 'statement not found' in str(e):
                    device.discard_config()
                    config_string = f'set interfaces {junos_port} description "{description}"'
                    device.load_merge_candidate(config=config_string)
                else:
                    raise

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
def _normalize_huawei_port(port_name: str) -> str:
    replacements = [
        ('GigabitEthernet', 'Ethernet'),
        ('gigabitethernet', 'Ethernet'),
    ]
    for old, new in replacements:
        if port_name.startswith(old):
            return new + port_name[len(old):]
    return port_name


def build_provisioning_payload(port_name, params):
    port_name = _normalize_huawei_port(port_name)
    subnet_type = params.get('subnet_type', '/31')
    media_type = params.get('media_type', 'fo').lower()
    debit_mbps = int(params.get('debit_mbps', 0))

    qos_rate_mbps = int(debit_mbps * 0.95) if media_type == 'fh' else debit_mbps
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
        'switch_ip': params.get('switch_ip'),
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
    def generate_config(self, payload):
        pass

    @abstractmethod
    def execute(self, config_payload):
        pass


class HuaweiDriver(ProvisioningDriver):
    # Only sub-interface config lines.
    # QoS omitted: NE40E EVE-NG image does not support 'traffic classifier'
    # or 'qos car' on sub-interfaces. Re-add when targeting a real NE40E.
    TEMPLATE = """
interface {{ port_name }}.{{ vlan }}
description B2B_INTERNET_{{ media_type }}_{{ client_name }}
vlan-type dot1q {{ vlan }}
ip binding vpn-instance INTERNET
ip address {{ pe_ip }} {{ subnet_mask }}
"""

    STATIC_ROUTE_TEMPLATE = (
        "ip route-static vpn-instance INTERNET {{ cust_lan_prefix }} {{ ce_ip }}"
    )

    def generate_config(self, payload):
        iface_lines = [
            line.strip()
            for line in Template(self.TEMPLATE.strip()).render(payload).splitlines()
            if line.strip()
        ]

        static_route = None
        if payload.get('nat_mode') == 'sans_nat_avec_cpe' and payload.get('ce_ip'):
            static_route = Template(self.STATIC_ROUTE_TEMPLATE).render(payload)

        return {
            'system_view_commands': [],
            'interface_commands': iface_lines,
            'static_route': static_route,
        }

    def execute(self, config_payload):
        import time

        device_params = {
            'device_type': 'huawei',
            'host': self.host,
            'username': self.username,
            'password': self.password,
            'port': 22,
            'session_log': '/tmp/netmiko_debug.log',
            'global_delay_factor': 2,
        }

        output_lines = []

        def _send(conn, cmd, sleep=2, check_error=True):
            conn.write_channel(cmd + '\n')
            time.sleep(sleep)
            out = conn.read_channel()
            output_lines.append(f'[{cmd}] => {out.strip()}')
            if check_error and ('Error' in out or 'error' in out):
                raise RuntimeError(f'Device error on "{cmd}": {out.strip()}')
            return out

        with ConnectHandler(**device_params) as conn:

            # 1. Enter system-view
            conn.write_channel('system-view\n')
            time.sleep(3)
            conn.read_channel()

            # 2. System-view-level commands (empty for this image)
            for cmd in config_payload['system_view_commands']:
                _send(conn, cmd)

            # 3. Enter sub-interface (first iface command)
            iface_cmds = config_payload['interface_commands']
            _send(conn, iface_cmds[0], sleep=3)

            # 4. Remaining sub-interface config
            for cmd in iface_cmds[1:]:
                _send(conn, cmd)

            # 5. Exit sub-interface back to system-view
            conn.write_channel('quit\n')
            time.sleep(2)
            conn.read_channel()

            # 6. Static route if needed
            if config_payload.get('static_route'):
                _send(conn, config_payload['static_route'])

            # 7. Commit
            conn.write_channel('commit\n')
            time.sleep(6)
            out = conn.read_channel()
            output_lines.append(f'[commit] => {out.strip()}')
            if 'Error' in out or 'error' in out:
                raise RuntimeError(f'Commit failed: {out.strip()}')

            # 8. Return to user-view
            conn.write_channel('return\n')
            time.sleep(2)
            conn.read_channel()

        return "\n".join(output_lines)


class JuniperDriver(ProvisioningDriver):
    TEMPLATE = """
delete interfaces {{ junos_port }} unit 0
set interfaces {{ junos_port }} vlan-tagging
set interfaces {{ junos_port }} flexible-vlan-tagging
set interfaces {{ junos_port }} per-unit-scheduler
set interfaces {{ junos_port }} unit {{ vlan }} description "B2B_INTERNET_{{ media_type }}_{{ client_name }}"
set interfaces {{ junos_port }} unit {{ vlan }} vlan-id {{ vlan }}
set interfaces {{ junos_port }} unit {{ vlan }} family inet address {{ pe_ip }}{{ subnet_cidr }}
set routing-instances INTERNET interface {{ junos_port }}.{{ vlan }}
set class-of-service interfaces {{ junos_port }} unit {{ vlan }} shaping-rate {{ qos_mbps }}m
{% if nat_mode == 'sans_nat_avec_cpe' and ce_ip %}
set routing-instances INTERNET routing-options static route {{ cust_lan_cidr }} next-hop {{ ce_ip }}
{% endif %}
"""

    def generate_config(self, payload):
        port_name = payload['port_name']
        junos_port = port_name[:-2] if port_name.endswith('.0') else port_name
        render_payload = {**payload, 'junos_port': junos_port}
        return Template(self.TEMPLATE.strip()).render(render_payload)

    
    def execute(self, config_string):
        import time
        from napalm.base.exceptions import ConnectionException
        from jnpr.junos.utils.config import Config
 
        driver = get_network_driver('junos')
        last_exc = None
        for attempt in range(1, 6):
            try:
                with driver(
                    hostname=self.host,
                    username=self.username,
                    password=self.password,
                    optional_args={'port': 830},
                ) as device:
                    
                    # 1. On récupère l'instance native Juniper PyEZ (Device)
                    pyez_device = device.device
                    
                    # 2. On instancie l'outil de gestion de configuration natif
                    cu = Config(pyez_device)
                    
                    # 3. On charge la configuration en forçant ignore_warning=True au niveau PyEZ
                    # Cela court-circuite la sévérité artificielle de NAPALM
                    cu.load(config_string, format="set", ignore_warning=True)
                    
                    # 4. On commit les changements de manière atomique
                    cu.commit()
                    
                return f"Juniper Config Committed:\n{config_string}"
            
            except ConnectionException as e:
                last_exc = e
                logger.warning(
                    f"Juniper NETCONF non prêt sur {self.host} (tentative {attempt}/5) — nouvel essai dans 15s"
                )
                time.sleep(15)
            except Exception as e:
                # Capture les autres exceptions critiques si la configuration est totalement invalide
                logger.error(f"Erreur d'injection brute PyEZ: {str(e)}")
                raise e

        raise ConnectionException(
            f"Impossible de se connecter à {self.host} après 5 tentatives: {last_exc}"
        )
       

def get_vendor_driver(vendor, host, username, password):
    vendors = {
        'huawei': HuaweiDriver,
        'juniper': JuniperDriver,
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
    from devices.models import Router, Port
    from provisioning.models import ProvisioningTask

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

        if payload.get('has_switch') and payload.get('switch_ip'):
            logger.info(
                f"Switched Topology: Triggering L2 VLAN {payload['vlan']} "
                f"on Switch {payload['switch_ip']}"
            )
            # execute_switch_l2_provisioning.delay(
            #     payload['switch_ip'], payload['vlan'], payload['client_name']
            # )

        driver = get_vendor_driver(
            router.vendor,
            router.loopback_ip,
            router.ssh_username,
            router.ssh_password,
        )

        config_data = driver.generate_config(payload)
        execution_output = driver.execute(config_data)

        task.status = 'completed'
        task.result = execution_output
        task.save()

        port.admin_status = 'active'
        port.port_description = (
            f"B2B_INTERNET_{payload['media_type']}_{payload['client_name']}"
        )
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
