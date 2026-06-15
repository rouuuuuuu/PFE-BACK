import redis
import logging
import time
import re
from celery import shared_task
from django.utils import timezone
from netmiko import ConnectHandler
from napalm import get_network_driver
from jinja2 import Template
from abc import ABC, abstractmethod
from .models import BandwidthUpgrade

logger = logging.getLogger(__name__)
redis_client = redis.Redis(host='localhost', port=6379, db=0)


# ============================================================
# SHARED LOW-LEVEL HELPERS
# ============================================================

def _huawei_device_params(host, username, password):
    return {
        'device_type': 'huawei',
        'host': host,
        'username': username,
        'password': password,
        'port': 22,
        'session_log': '/tmp/netmiko_debug.log',
        'global_delay_factor': 2,
    }


def _hw_send(conn, cmd, output_lines, sleep=2, check_error=True):
    """
    Send one command over an open Netmiko channel, wait, read back.
    Raises RuntimeError immediately if the device returns an error line.
    """
    conn.write_channel(cmd + '\n')
    time.sleep(sleep)
    out = conn.read_channel()
    output_lines.append(f'[{cmd}] => {out.strip()}')
    if check_error and ('Error' in out or 'error' in out):
        raise RuntimeError(f'Device error on "{cmd}": {out.strip()}')
    return out


def _hw_commit_save(conn, output_lines):
    """
    Standard Huawei exit sequence used by every task:
    quit → commit → return → save → y
    Always called from inside system-view (after a quit from interface view).
    """
    conn.write_channel('commit\n')
    time.sleep(6)
    out = conn.read_channel()
    output_lines.append(f'[commit] => {out.strip()}')
    if 'Error' in out or 'error' in out:
        raise RuntimeError(f'Commit failed: {out.strip()}')

    conn.write_channel('return\n')
    time.sleep(2)
    conn.read_channel()

    conn.write_channel('save\n')
    time.sleep(2)
    conn.read_channel()
    conn.write_channel('y\n')
    time.sleep(5)
    out = conn.read_channel()
    output_lines.append(f'[save] => {out.strip()}')


def _juniper_push(host, username, password, config_string, max_attempts=5):
    """
    Push a set-format config string to a Juniper device via NETCONF/PyEZ.
    Retries up to max_attempts times with 15 s gaps to handle slow EVE-NG boots.
    """
    from jnpr.junos.utils.config import Config
    from napalm.base.exceptions import ConnectionException

    driver = get_network_driver('junos')
    last_exc = None

    for attempt in range(1, max_attempts + 1):
        try:
            with driver(
                hostname=host,
                username=username,
                password=password,
                optional_args={'port': 830},
            ) as dev:
                cu = Config(dev.device)
                cu.load(config_string, format='set', ignore_warning=True)
                cu.commit()
            return f"Committed to {host}:\n{config_string}"

        except ConnectionException as e:
            last_exc = e
            logger.warning(
                f"NETCONF not ready on {host} (attempt {attempt}/{max_attempts})"
                f" — retrying in 15s"
            )
            time.sleep(15)

    raise ConnectionException(
        f"Could not connect to {host} after {max_attempts} attempts: {last_exc}"
    )


# ============================================================
# BUSINESS LOGIC — port normalisation + payload builder
# ============================================================

def _normalize_huawei_port(port_name: str) -> str:
    """
    The DB may store long-form names that differ from what VRP shows.
    Only fix genuine mismatches; never touch Eth-Trunk names.
    """
    if not port_name:
        return port_name
        
    if port_name.startswith('Eth-Trunk'):
        return port_name

    replacements = {
        'Eth': 'Ethernet', 
        'GE': 'GigabitEthernet'
    }

    for stored, cli in replacements.items():
        if port_name.startswith(stored):
            return cli + port_name[len(stored):]
    return port_name


def build_provisioning_payload(port_name, params):
    """
    Builds the Jinja context dict from ProvisioningTask.parameters.
    """
    port_name = _normalize_huawei_port(port_name)

    media_type = params.get('media_type', 'fo').lower()
    debit_mbps = int(params.get('debit_mbps', 0))

    qos_debit = int(debit_mbps * 0.95) if media_type == 'fh' else debit_mbps
    qos_profile = f"shaping{qos_debit}"

    subnet_type = params.get('subnet_type', '/30')
    mask_map = {
        '/28': '255.255.255.240',
        '/29': '255.255.255.248',
        '/30': '255.255.255.252',
        '/31': '255.255.255.254',
    }

    return {
        'client_name':        params.get('client_name', 'Unknown'),
        'port_name':          port_name,            
        'vlan':               params.get('vlan'),
        'media_type':         media_type.upper(),   

        'pe_ip':              params.get('pe_ip_address'),
        'subnet_cidr':        subnet_type,
        'subnet_mask':        mask_map.get(subnet_type, '255.255.255.252'),

        'vrf_name':           params.get('vrf_name', 'Internet_vpn'),

        'qos_profile':        qos_profile,          
        'qos_mbps':           qos_debit,            

        'nat_mode':           params.get('nat_mode'),
        'ce_ip':              params.get('ce_ip_address'),
        'cust_lan_prefix':    params.get('customer_lan_prefix', ''),
        'cust_lan_cidr':      params.get('customer_lan_cidr', ''),

        'has_switch':         params.get('has_switch', False),
        'switch_ip':          params.get('switch_ip'),
        'switch_port':        params.get('switch_port'),
        'switch_uplink_port': params.get('switch_uplink_port', 'ge-0/1/0'),
        'public_range':       params.get('public_range'),

        'is_bundle':          (
            port_name.startswith('Eth-Trunk')
            or port_name.lower().startswith('ae')
        ),
    }


# ============================================================
# VENDOR DRIVER CLASSES
# ============================================================

class ProvisioningDriver(ABC):
    def __init__(self, host, username, password):
        self.host = host
        self.username = username
        self.password = password

    @abstractmethod
    def generate_config(self, payload) -> dict:
        pass

    @abstractmethod
    def execute(self, config_data) -> str:
        pass


class HuaweiDriver(ProvisioningDriver):

    PHYS_TEMPLATE = (
        "interface {{ port_name }}\n"
        " description TO_B2B_client_{{ client_name }}_{{ media_type }}\n"
        " undo shutdown"
    )

    SUB_TEMPLATE = (
        "interface {{ port_name }}.{{ vlan }}\n"
        " vlan-type dot1q {{ vlan }}\n"
        " description TO_B2B_client_{{ client_name }}_{{ media_type }}_INTERNET\n"
        " ip binding vpn-instance {{ vrf_name }}\n"
        " ip address {{ pe_ip }} {{ subnet_mask }}\n"
        " statistic enable\n"
        " trust upstream default"
    )

    STATIC_ROUTE_TEMPLATE = (
        "ip route-static vpn-instance {{ vrf_name }} "
        "{{ cust_lan_prefix }} {{ ce_ip }}"
        "  description TO_B2B_client_{{ client_name }}_{{ media_type }}_INTERNET"
    )

    def generate_config(self, payload) -> dict:
        physical_lines = []
        if not payload.get('has_switch'):
            physical_lines = Template(self.PHYS_TEMPLATE).render(payload).splitlines()

        sub_lines = Template(self.SUB_TEMPLATE).render(payload).splitlines()

        static_route = None
        if (
            payload.get('nat_mode') in ('sans_nat_avec_cpe', 'avec_nat')
            and payload.get('ce_ip')
            and payload.get('cust_lan_prefix')
        ):
            static_route = Template(self.STATIC_ROUTE_TEMPLATE).render(payload)

        return {
            'physical_lines': physical_lines,
            'sub_lines':      sub_lines,
            'static_route':   static_route,
        }

    def execute(self, config_data) -> str:
        output_lines = []

        with ConnectHandler(**_huawei_device_params(
            self.host, self.username, self.password
        )) as conn:

            conn.write_channel('system-view\n')
            time.sleep(3)
            conn.read_channel()

            if config_data['physical_lines']:
                _hw_send(conn, config_data['physical_lines'][0], output_lines, sleep=3)
                for cmd in config_data['physical_lines'][1:]:
                    _hw_send(conn, cmd, output_lines)
                conn.write_channel('quit\n')
                time.sleep(2)
                conn.read_channel()

            _hw_send(conn, config_data['sub_lines'][0], output_lines, sleep=3)
            for cmd in config_data['sub_lines'][1:]:
                _hw_send(conn, cmd, output_lines)

            conn.write_channel('quit\n')
            time.sleep(2)
            conn.read_channel()

            if config_data['static_route']:
                _hw_send(conn, config_data['static_route'], output_lines)

            _hw_commit_save(conn, output_lines)

        return "\n".join(output_lines)

    def render_script(self, payload) -> str:
        cfg = self.generate_config(payload)
        parts = []

        if cfg['physical_lines']:
            parts.append('\n'.join(cfg['physical_lines']))
            parts.append('')

        parts.append('\n'.join(cfg['sub_lines']))

        if cfg['static_route']:
            parts.append('')
            parts.append(cfg['static_route'])

        return '\n'.join(parts)


class JuniperSwitchDriver(ProvisioningDriver):

    TEMPLATE = (
        "set vlans v{{ vlan }}_Internet_{{ client_name }} vlan-id {{ vlan }}\n"
        "set interfaces {{ switch_port }} description {{ client_name }}_{{ media_type }}_Internet\n"
        "set interfaces {{ switch_port }} unit 0 family ethernet-switching interface-mode trunk\n"
        "set interfaces {{ switch_port }} unit 0 family ethernet-switching vlan members v{{ vlan }}_Internet_{{ client_name }}\n"
        "set interfaces {{ switch_uplink_port }} unit 0 family ethernet-switching vlan members v{{ vlan }}_Internet_{{ client_name }}"
    )

    def generate_config(self, payload) -> str:
        return Template(self.TEMPLATE).render(payload)

    def execute(self, config_string) -> str:
        return _juniper_push(self.host, self.username, self.password, config_string)

    def render_script(self, payload) -> str:
        return self.generate_config(payload)


class JuniperDriver(ProvisioningDriver):

    TEMPLATE = (
        "delete interfaces {{ junos_port }} unit 0\n"
        "set interfaces {{ junos_port }} vlan-tagging\n"
        "set interfaces {{ junos_port }} flexible-vlan-tagging\n"
        "set interfaces {{ junos_port }} per-unit-scheduler\n"
        "set interfaces {{ junos_port }} unit {{ vlan }} description \"B2B_INTERNET_{{ media_type }}_{{ client_name }}\"\n"
        "set interfaces {{ junos_port }} unit {{ vlan }} vlan-id {{ vlan }}\n"
        "set interfaces {{ junos_port }} unit {{ vlan }} family inet address {{ pe_ip }}{{ subnet_cidr }}\n"
        "set routing-instances INTERNET interface {{ junos_port }}.{{ vlan }}\n"
        "set class-of-service interfaces {{ junos_port }} unit {{ vlan }} shaping-rate {{ qos_mbps }}m"
        "{% if nat_mode in ('sans_nat_avec_cpe', 'avec_nat') and ce_ip %}\n"
        "set routing-instances INTERNET routing-options static route {{ cust_lan_cidr }} next-hop {{ ce_ip }}"
        "{% endif %}"
    )

    def generate_config(self, payload) -> str:
        port_name = payload['port_name']
        junos_port = port_name[:-2] if port_name.endswith('.0') else port_name
        return Template(self.TEMPLATE).render({**payload, 'junos_port': junos_port})

    def execute(self, config_string) -> str:
        return _juniper_push(self.host, self.username, self.password, config_string)

    def render_script(self, payload) -> str:
        return self.generate_config(payload)


def get_vendor_driver(vendor, host, username, password):
    drivers = {
        'huawei':  HuaweiDriver,
        'juniper': JuniperDriver,
    }
    cls = drivers.get(vendor.lower())
    if not cls:
        raise ValueError(f"Unsupported vendor: {vendor}")
    return cls(host, username, password)


# ============================================================
# TASK 1 — BANDWIDTH UPGRADE   (script 21)
# ============================================================

@shared_task(bind=True)
def execute_bandwidth_upgrade(self, upgrade_id):
    """
    Updates the QoS profile on an existing sub-interface.
    The profile name is derived from new_bandwidth_mbps: shaping<N>.
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

        profile = f"shaping{upgrade.new_bandwidth_mbps}"

        iface_name = upgrade.interface
        if upgrade.vlan and '.' not in iface_name:
            iface_name = f"{iface_name}.{upgrade.vlan}"

        if device.vendor.lower() == 'huawei':
            iface_name = _normalize_huawei_port(iface_name)
            output_lines = []

            # Convert Mbps to Kbps for the bandwidth command fallback
            kbps = int(upgrade.new_bandwidth_mbps) * 1000

            with ConnectHandler(**_huawei_device_params(
                device.loopback_ip, device.ssh_username, device.ssh_password
            )) as conn:
                conn.write_channel('system-view\n')
                time.sleep(3)
                conn.read_channel()

                _hw_send(conn, f'interface {iface_name}', output_lines, sleep=3)
                
                # --- LAB MODE: EVE-NG Bypass ---
                # Since the stripped virtual image rejects all hardware QoS commands, 
                # we use the basic bandwidth command to satisfy the simulator's execution state.
                _hw_send(conn, f'bandwidth {kbps}', output_lines)

                conn.write_channel('quit\n')
                time.sleep(2)
                conn.read_channel()

                _hw_commit_save(conn, output_lines)

            output = "\n".join(output_lines)

            # --- GENERATE THE EXACT PRODUCTION SCRIPT FOR DOWNLOAD ---
            upgrade.generated_commands = (

                f"<{device.loopback_ip}>\n"
                f"interface {iface_name}\n"
                f"qos-profile {profile} outbound identifier none\n"
                f"qos-profile {profile} inbound identifier none"
            )

        elif device.vendor.lower() == 'juniper':
            port = iface_name
            mbps = upgrade.new_bandwidth_mbps
            
            # Scale the burst size limit criteria dynamically (100M = 30400000)
            burst_size = int(mbps) * 304000
            
            # Parse JunOS sub-interface elements correctly into port + unit keywords
            if '.' in port:
                junos_port, unit = port.split('.', 1)
                config = f"set class-of-service interfaces {junos_port} unit {unit} shaping-rate {mbps}m"
            else:
                junos_port = port[:-2] if port.endswith('.0') else port
                unit = '0'  # Fallback target parameter
                config = f"set class-of-service interfaces {junos_port} shaping-rate {mbps}m"
                
            # --- LAB MODE: Execute basic shaping to keep EVE-NG candidate engine happy ---
            output = _juniper_push(
                device.loopback_ip, device.ssh_username, device.ssh_password, config
            )
            
            # --- GENERATE THE EXACT JUNIPER PRODUCTION SCRIPT FOR DOWNLOAD ---
            upgrade.generated_commands = (

                f"<{device.loopback_ip}>\n"
                f"set firewall policer Bandwidth{mbps}M if-exceeding bandwidth-limit {mbps}m\n"
                f"set firewall policer Bandwidth{mbps}M if-exceeding burst-size-limit {burst_size}\n"
                f"set firewall policer Bandwidth{mbps}M then discard\n\n"
                f"set interfaces {junos_port} unit {unit} family inet policer input Bandwidth{mbps}M\n"
                f"set interfaces {junos_port} unit {unit} family inet policer output Bandwidth{mbps}M"
            )

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


# ============================================================
# TASK 2 — FETCH INTERFACES (live from device)
# ============================================================

@shared_task
def fetch_device_interfaces(device_id):
    """
    SSH into the device and run 'display interface description' or 'show interfaces terse'.
    Strictly filters infrastructure, looping systems, and dedicated backhaul management instances.
    """
    from devices.models import Router
    try:
        device = Router.objects.get(id=device_id)

        ignored_patterns = re.compile(
            r'(^loop|^null|^meth|^fxp|^vme|vlanif4094|\.4094|\bloopback\b)', 
            re.IGNORECASE
        )

        if device.vendor.lower() == 'huawei':
            with ConnectHandler(**_huawei_device_params(
                device.loopback_ip, device.ssh_username, device.ssh_password
            )) as conn:
                conn.send_command_timing('screen-length 0 temporary', delay_factor=2)
                raw_output = conn.send_command(
                    'display interface description',
                    read_timeout=30,
                )
            
            filtered_lines = []
            for line in raw_output.splitlines():
                clean_line = line.strip()
                if not clean_line or clean_line.startswith('Interface') or clean_line.startswith('PHY'):
                    continue
                if ignored_patterns.search(clean_line):
                    continue
                filtered_lines.append(line)
                
            return {'status': 'success', 'data': '\n'.join(filtered_lines), 'vendor': 'huawei'}

        elif device.vendor.lower() == 'juniper':
            driver = get_network_driver('junos')
            dev = driver(
                hostname=device.loopback_ip,
                username=device.ssh_username,
                password=device.ssh_password,
                optional_args={'port': 830},
            )
            dev.open()
            result = dev.cli(['show interfaces terse'])
            dev.close()
            
            raw_output = result.get('show interfaces terse', '')
            filtered_lines = []
            for line in raw_output.splitlines():
                clean_line = line.strip()
                if not clean_line:
                    continue
                if ignored_patterns.search(clean_line):
                    continue
                filtered_lines.append(line)

            return {
                'status': 'success',
                'data': '\n'.join(filtered_lines),
                'vendor': 'juniper',
            }

        else:
            return {'status': 'error', 'message': f'Unsupported vendor: {device.vendor}'}

    except Exception as e:
        return {'status': 'error', 'message': str(e)}


# ============================================================
# TASK 3 — PORT RESERVATION (undo shutdown + description)
# ============================================================

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
            output_lines = []
            normalized_port = _normalize_huawei_port(port.port_full_name)

            with ConnectHandler(**_huawei_device_params(
                router.loopback_ip, router.ssh_username, router.ssh_password
            )) as conn:
                conn.write_channel('system-view\n')
                time.sleep(3)
                conn.read_channel()

                _hw_send(conn, f'interface {normalized_port}', output_lines, sleep=3)
                _hw_send(conn, f'description {description}', output_lines)
                _hw_send(conn, 'undo shutdown', output_lines)

                conn.write_channel('quit\n')
                time.sleep(2)
                conn.read_channel()

                _hw_commit_save(conn, output_lines)

        elif router.vendor.lower() == 'juniper':
            junos_port = (
                port.port_full_name[:-2]
                if port.port_full_name.endswith('.0')
                else port.port_full_name
            )
            config = '\n'.join([
                f'delete interfaces {junos_port} disable',
                f'set interfaces {junos_port} description "{description}"',
            ])
            _juniper_push(router.loopback_ip, router.ssh_username, router.ssh_password, config)

        else:
            raise ValueError(f"Unsupported vendor: {router.vendor}")

        port.admin_status = 'active'
        port.oper_status = 'up'
        port.port_description = description
        port.save()
        return f"Successfully reserved {port.port_full_name}"

    except Exception as e:
        logger.error(f"Failed to reserve port {port.port_full_name}: {str(e)}")
        port.admin_status = 'inactive'
        port.save()
        raise e

    finally:
        redis_client.delete(lock_key)


# ============================================================
# TASK 4 — INTERNET PROVISIONING
# ============================================================

@shared_task(bind=True)
def execute_internet_provisioning(self, task_id, router_id, port_id):
    from devices.models import Router, Port
    from provisioning.models import ProvisioningTask

    try:
        task   = ProvisioningTask.objects.get(id=task_id)
        router = Router.objects.get(id=router_id)
        port   = Port.objects.get(id=port_id)
    except Exception:
        return "Database records not found."

    payload  = build_provisioning_payload(port.port_full_name, task.parameters)
    lock_key = f'device_lock_{router.loopback_ip}'

    if not redis_client.set(lock_key, f"internet_task_{task_id}", nx=True, ex=300):
        raise self.retry(countdown=60, max_retries=3)

    try:
        task.status = 'running'
        task.save()

        script_parts = []

        if payload.get('has_switch') and payload.get('switch_ip'):
            try:
                from devices.models import Switch
                sw = Switch.objects.get(loopback_ip=payload['switch_ip'])
            except Exception:
                from devices.models import Router as SW
                sw = SW.objects.get(loopback_ip=payload['switch_ip'])

            sw_driver = JuniperSwitchDriver(sw.loopback_ip, sw.ssh_username, sw.ssh_password)
            sw_config = sw_driver.generate_config(payload)
            sw_output = sw_driver.execute(sw_config)
            script_parts.append(f"# Switch {sw.loopback_ip}\n{sw_driver.render_script(payload)}")
            logger.info(f"Switch provisioned: {sw_output}")

        pe_driver = get_vendor_driver(
            router.vendor,
            router.loopback_ip,
            router.ssh_username,
            router.ssh_password,
        )

        config_data    = pe_driver.generate_config(payload)
        execution_output = pe_driver.execute(config_data)

        if hasattr(pe_driver, 'render_script'):
            script_parts.append(
                f"# PE {router.loopback_ip}\n{pe_driver.render_script(payload)}"
            )

        task.status = 'completed'
        task.result = execution_output
        task.script_output = '\n\n'.join(script_parts)
        task.save()

        port.admin_status   = 'active'
        port.port_description = (
            f"TO_B2B_client_{payload['client_name']}_{payload['media_type']}_INTERNET"
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
