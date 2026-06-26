import redis
import logging
import time
import re
import random
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
    """Write one command, wait, read back. Raises on VRP Error lines."""
    conn.write_channel(cmd + '\n')
    time.sleep(sleep)
    out = conn.read_channel()
    output_lines.append(f'[{cmd}] => {out.strip()}')
    if check_error and ('Error' in out or 'error' in out):
        raise RuntimeError(f'Device error on "{cmd}": {out.strip()}')
    return out


def _hw_commit_save(conn, output_lines):
    """commit → return → save → y  (always called from system-view)"""
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
    Push set-format config to Juniper via NETCONF.
    Handles EVE-NG vJunos ConnectClosedError during commit by reconnecting
    and verifying the config was actually applied.
    """
    from jnpr.junos.utils.config import Config
    from jnpr.junos.exception import ConnectClosedError, CommitError
    from napalm.base.exceptions import ConnectionException
    import time

    driver = get_network_driver('junos')
    last_exc = None

    def _open_dev():
        dev = driver(
            hostname=host,
            username=username,
            password=password,
            optional_args={'port': 830},
        )
        dev.open()
        dev.device.timeout = 180
        return dev

    for attempt in range(1, max_attempts + 1):
        try:
            dev = _open_dev()
        except Exception as e:
            last_exc = e
            logger.warning(f"NETCONF not ready on {host} (attempt {attempt}/{max_attempts}) — retrying in 15s")
            time.sleep(15)
            continue

        try:
            cu = Config(dev.device)
            cu.load(config_string, format='set', ignore_warning=True)

            commit_ok = False
            try:
                cu.commit(timeout=180)
                commit_ok = True
            except ConnectClosedError:
                # vJunos EVE-NG drops the socket mid-commit but often commits anyway.
                # Reconnect and verify rather than failing immediately.
                logger.warning(
                    f"[{host}] ConnectClosedError during commit (attempt {attempt}) — "
                    f"reconnecting to verify..."
                )
                time.sleep(8)  # Give vJunos time to finish the commit before reconnecting

                try:
                    dev2 = _open_dev()
                    # Probe: if we can open a new session, the device is alive.
                    # Check candidate vs running diff — empty diff = commit landed.
                    cu2 = Config(dev2.device)
                    diff = cu2.diff()
                    dev2.close()

                    if diff is None or diff.strip() == '':
                        # No pending diff → config was committed successfully
                        logger.info(f"[{host}] Post-reconnect diff is empty — commit confirmed.")
                        commit_ok = True
                    else:
                        # There's still a diff — commit did NOT land, retry
                        logger.warning(f"[{host}] Post-reconnect diff not empty — commit may not have landed.")
                        last_exc = ConnectClosedError(host)
                except Exception as verify_exc:
                    logger.warning(f"[{host}] Reconnect/verify also failed: {verify_exc}")
                    last_exc = verify_exc

            if commit_ok:
                try:
                    dev.close()
                except Exception:
                    pass
                return f"Committed to {host}:\n{config_string}"

        except CommitError as e:
            logger.error(f"[{host}] CommitError: {e}")
            try:
                cu.rollback()
                dev.close()
            except Exception:
                pass
            raise RuntimeError(f"Juniper commit error on {host}: {e}")

        except Exception as e:
            last_exc = e
            logger.warning(f"[{host}] Exception during push (attempt {attempt}): {e}")
            try:
                dev.close()
            except Exception:
                pass

        time.sleep(15)

    raise ConnectionException(f"Could not commit to {host} after {max_attempts} attempts: {last_exc}")

# ============================================================
# SWITCH TOPOLOGY LOOKUP
# ============================================================

def find_switch_for_port(router, port_name):
    """
    Returns the Switch object attached to this router port, or None.
    Queries: Switch where router_port == port_name AND routeur contains router.name
    """
    try:
        from devices.models import Switch
        router_fragment = router.name.split('_')[1] if '_' in router.name else router.name
        sw = Switch.objects.filter(
            interface_rt=port_name,
            connected_router__name__icontains=router_fragment
        ).first()
        if sw:
            return sw

        sw = Switch.objects.filter(
            interface_rt=port_name,
            connected_router__loopback_ip=router.loopback_ip
        ).first()
        return sw
    except Exception as e:
        logger.warning(f"Switch lookup failed for {router.name}/{port_name}: {e}")
        return None


# ============================================================
# BUSINESS LOGIC — PE IP auto-derivation + payload builder
# ============================================================

def _get_pe_ip_from_range(public_range: str, subnet_type: str) -> str:
    """
    Auto-derives the PE IP from the selected public range and subnet type.
    """
    import ipaddress
    try:
        network    = ipaddress.ip_network(public_range, strict=False)
        prefix_len = int(subnet_type.replace('/', ''))
        subnets    = list(network.subnets(new_prefix=prefix_len))
        if not subnets:
            return ''
            
        # LAB FIX: Pick a random subnet instead of always grabbing [0]
        # This prevents IP conflicts when running multiple tests in EVE-NG
        chosen_subnet = random.choice(subnets)
        
        hosts = list(chosen_subnet.hosts())
        # /31 has no hosts() in Python — use network addresses directly
        if not hosts:
            hosts = list(chosen_subnet)
        return str(hosts[0]) if hosts else ''
    except Exception as e:
        logger.warning(f"PE IP derivation failed for {public_range} + {subnet_type}: {e}")
        return ''


def _get_ce_ip(pe_ip: str, subnet_type: str) -> str:
    """
    Derives the CE IP from the PE IP and subnet mask.
    Assuming the PE is the first host, the CE will be the second.
    """
    if not pe_ip or not subnet_type:
        return ''
        
    import ipaddress
    try:
        network = ipaddress.ip_network(f"{pe_ip}{subnet_type}", strict=False)
        hosts = list(network.hosts())
        
        # Handle /31 which has no "hosts" in the traditional sense
        if not hosts:
            hosts = list(network)
            
        if len(hosts) > 1:
            # If PE is the first IP, CE is the second
            if str(hosts[0]) == pe_ip:
                return str(hosts[1])
            # If PE is the second IP, CE is the first
            elif str(hosts[1]) == pe_ip:
                return str(hosts[0])
        return ''
    except Exception as e:
        logger.warning(f"CE IP derivation failed for {pe_ip} + {subnet_type}: {e}")
        return ''


def _normalize_huawei_port(port_name: str) -> str:
    if not port_name:
        return port_name
    if port_name.startswith('Eth-Trunk') or port_name.startswith('Ethernet') or port_name.startswith('GigabitEthernet'):
        return port_name

    replacements = {
        'Eth': 'Ethernet',
        'GE': 'GigabitEthernet'
    }
    for stored, cli in replacements.items():
        if port_name.startswith(stored):
            return cli + port_name[len(stored):]
    return port_name


def build_provisioning_payload(port_name, params, router=None):
    """Builds the Jinja context dict from ProvisioningTask.parameters."""
    port_name = _normalize_huawei_port(port_name)

    media_type = params.get('media_type', 'fo').lower()
    debit_mbps = int(params.get('debit_mbps', 0))

    qos_debit   = int(debit_mbps * 0.95) if media_type == 'fh' else debit_mbps
    qos_profile = f"shaping{qos_debit}"

    # ── SAFE SUBNET TYPE CLEANING ──
    raw_subnet = params.get('subnet_type')
    if not raw_subnet or str(raw_subnet).strip().lower() in ['none', 'null', '']:
        subnet_type = '/30'
    else:
        subnet_type = str(raw_subnet).strip()

    mask_map = {
        '/28': '255.255.255.240',
        '/29': '255.255.255.248',
        '/30': '255.255.255.252',
        '/31': '255.255.255.254',
    }

    has_switch   = params.get('has_switch', False)
    switch_ip    = params.get('switch_ip')
    switch_uplink = params.get('switch_uplink_port', 'ge-0/1/0')
    switch_port  = params.get('switch_port')
    switch_vendor = params.get('switch_vendor', 'juniper')

    if has_switch and not switch_ip and router:
        sw = find_switch_for_port(router, port_name)
        if sw:
            switch_ip     = sw.loopback_ip
            switch_uplink = getattr(sw, 'interface_sw', 'ge-0/1/0')
            switch_port   = getattr(sw, 'interface_sw', '')
            switch_vendor = 'cisco' if 'cisco' in getattr(sw, 'model', '').lower() else 'juniper'

    # ── PE IP: auto-derive from public_range + subnet_type if not provided ──
    pe_ip = params.get('pe_ip_address') or params.get('pe_ip') or ''
    if not pe_ip and params.get('public_range') and subnet_type:
        pe_ip = _get_pe_ip_from_range(params['public_range'], subnet_type)

    # GUARD: pe_ip must not be empty
    if not pe_ip:
        logger.error(f"❌ DERIVATION FAILED! Received parameters: public_range='{params.get('public_range')}', subnet_type='{subnet_type}'")
        raise ValueError(
            "PE IP address is required. Send 'pe_ip_address' in the form parameters."
        )

    # ── CE IP: Auto-derive based on the PE IP ──
    ce_ip = params.get('ce_ip_address') or params.get('ce_ip') or ''
    if not ce_ip:
        ce_ip = _get_ce_ip(pe_ip, subnet_type)
        
    # ── LAN Prefix: Handle your backend generation logic here ──
    cust_lan_prefix = params.get('customer_lan_prefix', '')
    if not cust_lan_prefix:
        pass

    return {
        'client_name':        params.get('client_name', 'Unknown'),
        'port_name':          port_name,
        'vlan':               params.get('vlan'),
        'media_type':         media_type.upper(),
        'pe_ip':              pe_ip,
        'subnet_cidr':        subnet_type,
        'subnet_mask':        mask_map.get(subnet_type, '255.255.255.252'),
        'vrf_name':           'Internet_vpn',
        'qos_profile':        qos_profile,
        'qos_mbps':           qos_debit,
        'nat_mode':           params.get('nat_mode'),
        'ce_ip':              ce_ip,
        'cust_lan_prefix':    cust_lan_prefix,
        'cust_lan_cidr':      params.get('customer_lan_cidr', ''),
        'has_switch':         has_switch,
        'switch_ip':          switch_ip,
        'switch_port':        switch_port,
        'switch_uplink_port': switch_uplink,
        'switch_vendor':      switch_vendor,
        'is_bundle': (
            port_name.startswith('Eth-Trunk')
            or port_name.lower().startswith('ae')
        ),
    }


# ============================================================
# VENDOR DRIVER CLASSES
# ============================================================

class ProvisioningDriver(ABC):
    def __init__(self, host, username, password):
        self.host     = host
        self.username = username
        self.password = password

    @abstractmethod
    def generate_config(self, payload) -> dict:
        pass

    @abstractmethod
    def execute(self, config_data) -> str:
        pass

    @abstractmethod
    def render_script(self, payload, router_ip='') -> str:
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
        " qos-profile {{ qos_profile }} outbound identifier none\n"
        " qos-profile {{ qos_profile }} inbound identifier none\n"
        " statistic enable \n"
        " trust upstream default"
    )

    STATIC_ROUTE_TEMPLATE = (
        "ip route-static vpn-instance {{ vrf_name }} "
        "{{ cust_lan_prefix }} {{ ce_ip }}"
        "  description TO_B2B_client_{{ client_name }}_{{ media_type }}_INTERNET"
    )

    LAB_SUB_TEMPLATE = (
        "ip vpn-instance {{ vrf_name }}\n"
        " route-distinguisher 100:1\n"
        " vpn-target 100:1 both\n"
        "quit\n"
        "interface {{ port_name }}.{{ vlan }}\n"
        "vlan-type dot1q {{ vlan }}\n"
        "description TO_B2B_client_{{ client_name }}_{{ media_type }}_INTERNET\n"
        "ip binding vpn-instance {{ vrf_name }}\n"
        "ip address {{ pe_ip }} {{ subnet_mask }}\n"
        "statistic enable"
    )

    def generate_config(self, payload) -> dict:
        has_switch = payload.get('has_switch', False)

        phys_lines = []
        if not has_switch:
            phys_lines = Template(self.PHYS_TEMPLATE).render(payload).splitlines()

        prod_sub_lines = Template(self.SUB_TEMPLATE).render(payload).splitlines()
        lab_sub_lines  = Template(self.LAB_SUB_TEMPLATE).render(payload).splitlines()

        static_route = None
        if (
            payload.get('nat_mode') in ('sans_nat_avec_cpe', 'avec_nat')
            and payload.get('ce_ip')
            and payload.get('cust_lan_prefix')
        ):
            static_route = Template(self.STATIC_ROUTE_TEMPLATE).render(payload)

        return {
            'phys_lines':     phys_lines,
            'prod_sub_lines': prod_sub_lines,
            'lab_sub_lines':  lab_sub_lines,
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

            if config_data['phys_lines']:
                phys_cmds = [l.strip() for l in config_data['phys_lines'] if l.strip()]
                _hw_send(conn, phys_cmds[0], output_lines, sleep=3)
                for cmd in phys_cmds[1:]:
                    _hw_send(conn, cmd, output_lines)
                conn.write_channel('quit\n')
                time.sleep(2)
                conn.read_channel()

            lab_cmds = [l.strip() for l in config_data['lab_sub_lines'] if l.strip()]
            _hw_send(conn, lab_cmds[0], output_lines, sleep=3)
            for cmd in lab_cmds[1:]:
                _hw_send(conn, cmd, output_lines)

            conn.write_channel('quit\n')
            time.sleep(2)
            conn.read_channel()

            if config_data['static_route']:
                _hw_send(conn, config_data['static_route'].strip(), output_lines)

            _hw_commit_save(conn, output_lines)

        return "\n".join(output_lines)

    def render_script(self, payload, router_ip='') -> str:
        mode_labels = {
            'sans_nat_sans_cpe': 'port dédié sur RT Huawei Sans NAT sans CPE',
            'sans_nat_avec_cpe': 'port dédié sur RT Huawei Sans NAT avec CPE',
            'avec_nat':          'port dédié sur RT Huawei Avec NAT',
        }
        label = mode_labels.get(payload.get('nat_mode', ''), 'provisioning B2B Internet')

        cfg   = self.generate_config(payload)
        parts = [
            f"==> {label}",
            "",
            f"<{router_ip}>",
            "",
        ]

        if cfg['phys_lines']:
            parts.append('\n'.join(cfg['phys_lines']))
            parts.append('')

        parts.append('\n'.join(cfg['prod_sub_lines']))

        if cfg['static_route']:
            parts.append(cfg['static_route'])

        return '\n'.join(parts)


class CiscoSwitchDriver(ProvisioningDriver):
    """Driver L2/VLAN pour switchs Cisco IOS."""
    TEMPLATE = (
        "vlan {{ vlan }}\n"
        " name Internet_{{ client_name }}\n"
        "exit\n"
        "interface {{ switch_port }}\n"
        " switchport mode trunk\n"
        " switchport trunk allowed vlan add {{ vlan }}\n"
        " description {{ client_name }}_{{ media_type }}_Internet\n"
        "exit\n"
        "interface {{ switch_uplink_port }}\n"
        " switchport mode trunk\n"
        " switchport trunk allowed vlan add {{ vlan }}"
    )

    def generate_config(self, payload) -> str:
        return Template(self.TEMPLATE).render(payload)

    def execute(self, config_string) -> str:
        output_lines = []
        with ConnectHandler(
            device_type='cisco_ios',
            host=self.host,
            username=self.username,
            password=self.password
        ) as conn:
            conn.enable()
            output = conn.send_config_set(config_string.splitlines())
            output_lines.append(output)
            conn.send_command('write memory')
        return "\n".join(output_lines)

    def render_script(self, payload, router_ip='') -> str:
        return self.generate_config(payload)


class JuniperSwitchDriver(ProvisioningDriver):
    TEMPLATE = (
        "set vlans v{{ vlan }}_Internet_{{ client_name }} vlan-id {{ vlan }}\n"
        "set interfaces {{ switch_port }}  description {{ client_name }}_{{ media_type }}_Internet\n"
        "set interfaces {{ switch_port }}  unit 0 family ethernet-switching interface-mode trunk\n"
        "set interfaces {{ switch_port }}  unit 0 family ethernet-switching vlan members v{{ vlan }}_Internet_{{ client_name }}\n"
        "set interfaces {{ switch_uplink_port }} unit 0 family ethernet-switching vlan members  v{{ vlan }}_Internet_{{ client_name }}"
    )

    def generate_config(self, payload) -> str:
        return Template(self.TEMPLATE).render(payload)

    def execute(self, config_string) -> str:
        return _juniper_push(self.host, self.username, self.password, config_string)

    def render_script(self, payload, router_ip='') -> str:
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
        "{% if nat_mode in ('sans_nat_avec_cpe', 'avec_nat') and ce_ip and cust_lan_cidr %}\n"
        "set routing-instances INTERNET routing-options static route {{ cust_lan_cidr }} next-hop {{ ce_ip }}"
        "{% endif %}"
    )

    def generate_config(self, payload) -> str:
        port_name  = payload['port_name']
        junos_port = port_name[:-2] if port_name.endswith('.0') else port_name
        return Template(self.TEMPLATE).render({**payload, 'junos_port': junos_port})

    def execute(self, config_string) -> str:
        return _juniper_push(self.host, self.username, self.password, config_string)

    def render_script(self, payload, router_ip='') -> str:
        return self.generate_config(payload)


def get_vendor_driver(vendor, host, username, password):
    drivers = {'huawei': HuaweiDriver, 'juniper': JuniperDriver, 'cisco': CiscoSwitchDriver}
    cls = drivers.get(vendor.lower())
    if not cls:
        raise ValueError(f"Unsupported vendor: {vendor}")
    return cls(host, username, password)


# ============================================================
# TASK 1 — BANDWIDTH UPGRADE
# ============================================================

@shared_task(bind=True)
def execute_bandwidth_upgrade(self, upgrade_id):
    try:
        upgrade = BandwidthUpgrade.objects.get(upgrade_id=upgrade_id)
    except BandwidthUpgrade.DoesNotExist:
        return "Upgrade record not found."

    device    = upgrade.device
    lock_key  = f'device_lock_{device.loopback_ip}'
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

        profile    = f"shaping{upgrade.new_bandwidth_mbps}"
        iface_name = upgrade.interface

        if upgrade.vlan and '.' not in iface_name:
            iface_name = f"{iface_name}.{upgrade.vlan}"
        iface_name = _normalize_huawei_port(iface_name)

        if device.vendor.lower() == 'huawei':
            output_lines = []
            kbps = int(upgrade.new_bandwidth_mbps) * 1000

            with ConnectHandler(**_huawei_device_params(
                device.loopback_ip, device.ssh_username, device.ssh_password
            )) as conn:
                conn.write_channel('system-view\n')
                time.sleep(3)
                conn.read_channel()

                _hw_send(conn, f'interface {iface_name}', output_lines, sleep=3)
                _hw_send(conn, f'bandwidth {kbps}', output_lines)

                conn.write_channel('quit\n')
                time.sleep(2)
                conn.read_channel()

                _hw_commit_save(conn, output_lines)

            output = "\n".join(output_lines)
            upgrade.generated_commands = (
                f"<{device.loopback_ip}>\n"
                f"interface {iface_name}\n"
                f" qos-profile {profile} outbound identifier none\n"
                f" qos-profile {profile} inbound identifier none"
            )

        elif device.vendor.lower() == 'juniper':
            mbps = upgrade.new_bandwidth_mbps
            if '.' in iface_name:
                junos_port, unit = iface_name.split('.', 1)
            else:
                junos_port = iface_name[:-2] if iface_name.endswith('.0') else iface_name
                unit = '0'

            burst_size = int(mbps) * 304000
            config = f"set class-of-service interfaces {junos_port} unit {unit} shaping-rate {mbps}m"
            output = _juniper_push(device.loopback_ip, device.ssh_username, device.ssh_password, config)
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
# TASK 2 — FETCH INTERFACES
# ============================================================

@shared_task
def fetch_device_interfaces(device_id):
    from devices.models import Router
    try:
        device = Router.objects.get(id=device_id)
        ignored = re.compile(
            r'(^loop|^null|^meth|^fxp|^vme|vlanif4094|\.4094|\bloopback\b)',
            re.IGNORECASE
        )

        if device.vendor.lower() == 'huawei':
            with ConnectHandler(**_huawei_device_params(
                device.loopback_ip, device.ssh_username, device.ssh_password
            )) as conn:
                conn.send_command_timing('screen-length 0 temporary', delay_factor=2)
                raw = conn.send_command('display interface description', read_timeout=30)

            filtered = [
                line for line in raw.splitlines()
                if line.strip()
                and not line.strip().startswith('Interface')
                and not line.strip().startswith('PHY')
                and not ignored.search(line.strip())
            ]
            return {'status': 'success', 'data': '\n'.join(filtered), 'vendor': 'huawei'}

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
            raw = result.get('show interfaces terse', '')
            filtered = [
                line for line in raw.splitlines()
                if line.strip() and not ignored.search(line.strip())
            ]
            return {'status': 'success', 'data': '\n'.join(filtered), 'vendor': 'juniper'}

        else:
            return {'status': 'error', 'message': f'Unsupported vendor: {device.vendor}'}

    except Exception as e:
        return {'status': 'error', 'message': str(e)}


# ============================================================
# TASK 2.5 — LLDP SWITCH DISCOVERY
# ============================================================

@shared_task
def discover_switch_via_lldp(router_id, port_name):
    """
    Connects to the router via SSH/NETCONF and runs vendor-specific LLDP commands
    to dynamically discover the connected switch on EVE-NG.
    """
    from devices.models import Router, Switch
    try:
        router = Router.objects.get(id=router_id)

        # ── JUNIPER ROUTER INTERFACE DISCOVERY ──
        if router.vendor.lower() == 'juniper':
            driver = get_network_driver('junos')
            dev = driver(
                hostname=router.loopback_ip,
                username=router.ssh_username,
                password=router.ssh_password,
                optional_args={'port': 830},
            )
            dev.open()
            result = dev.cli(['show lldp neighbors'])
            dev.close()
            
            raw_output = result.get('show lldp neighbors', '')
            
            for line in raw_output.splitlines():
                line_clean = line.strip()
                if not line_clean or line_clean.startswith('Local Interface') or not line_clean.startswith('ge-'):
                    continue
                
                parts = line_clean.split()
                if len(parts) >= 5:
                    local_intf    = parts[0].strip()   
                    neighbor_port = parts[3].strip()   
                    neighbor_name = parts[4].strip()   
                    
                    if local_intf.lower() == port_name.lower():
                        sw_ip = "10.41.74.199" 
                        sw_vendor = 'cisco' if 'cisco' in neighbor_name.lower() or 'sw' in neighbor_name.lower() else 'juniper'
                        
                        sw_obj = Switch.objects.filter(name__iexact=neighbor_name).first()
                        if sw_obj:
                            sw_ip = sw_obj.loopback_ip
                            if 'cisco' in sw_obj.model.lower():
                                sw_vendor = 'cisco'
                                
                        return {
                            'status':             'success',
                            'has_switch':          True,
                            'switch_name':        neighbor_name,
                            'switch_ip':          sw_ip,
                            'switch_port':        neighbor_port,
                            'switch_uplink_port': neighbor_port,
                            'switch_vendor':      sw_vendor,
                            'message':            f"Switch {neighbor_name} ({sw_vendor.upper()}) detected dynamically on Juniper {router.name}.",
                        }

        # ── HUAWEI ROUTER INTERFACE DISCOVERY ──
        elif router.vendor.lower() == 'huawei':
            port_name_norm = _normalize_huawei_port(port_name)

            with ConnectHandler(**_huawei_device_params(
                router.loopback_ip, router.ssh_username, router.ssh_password
            )) as conn:
                conn.send_command_timing('screen-length 0 temporary', delay_factor=2)
                raw_output = conn.send_command('display lldp neighbor brief', read_timeout=30)

            for line in raw_output.splitlines():
                line_clean = line.strip()
                if not line_clean or 'Local Intf' in line_clean or '----' in line_clean:
                    continue

                parts = line_clean.split()
                if len(parts) >= 3:
                    local_intf = _normalize_huawei_port(parts[0])

                    if local_intf.lower() == port_name_norm.lower():
                        neighbor_name = parts[1].strip()
                        neighbor_port = parts[2].strip()

                        sw_ip     = "10.41.236.34"
                        sw_vendor = 'juniper'

                        sw_obj = Switch.objects.filter(name__iexact=neighbor_name).first()
                        if sw_obj:
                            sw_ip = sw_obj.loopback_ip
                            if 'cisco' in sw_obj.model.lower():
                                sw_vendor = 'cisco'
                        else:
                            rt_obj = Router.objects.filter(name__iexact=neighbor_name).first()
                            if rt_obj:
                                sw_ip = rt_obj.loopback_ip
                                if 'cisco' in rt_obj.model.lower():
                                    sw_vendor = 'cisco'

                        return {
                            'status':             'success',
                            'has_switch':          True,
                            'switch_name':        neighbor_name,
                            'switch_ip':          sw_ip,
                            'switch_port':        neighbor_port,
                            'switch_uplink_port': neighbor_port,
                            'switch_vendor':      sw_vendor,
                            'message':            f"Switch {neighbor_name} detected dynamically on Huawei {router.name}.",
                        }

        return {
            'status':     'success',
            'has_switch': False,
            'message':    f"No switch detected via LLDP on port {port_name}.",
        }

    except Exception as e:
        return {'status': 'error', 'message': str(e)}


# ============================================================
# TASK 3 — PORT RESERVATION
# ============================================================

@shared_task(bind=True)
def execute_port_reservation(self, port_id, router_id, description):
    from devices.models import Router, Port

    try:
        port   = Port.objects.get(id=port_id)
        router = Router.objects.get(id=router_id)
    except (Port.DoesNotExist, Router.DoesNotExist):
        return "Port or Router record not found."

    lock_key      = f'device_lock_{router.loopback_ip}'
    lock_acquired = redis_client.set(lock_key, f"port_res_{port_id}", nx=True, ex=300)

    if not lock_acquired:
        port.admin_status = 'inactive'
        port.save()
        logger.warning(f"Device {router.loopback_ip} is busy. Reservation queued.")
        raise self.retry(countdown=60, max_retries=3)

    try:
        if router.vendor.lower() == 'huawei':
            output_lines    = []
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

        port.admin_status     = 'active'
        port.oper_status      = 'up'
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

    payload  = build_provisioning_payload(port.port_full_name, task.parameters, router=router)
    lock_key = f'device_lock_{router.loopback_ip}'

    if not redis_client.set(lock_key, f"internet_task_{task_id}", nx=True, ex=300):
        raise self.retry(countdown=60, max_retries=3)

    try:
        task.status = 'running'
        task.save()

        script_parts = []

        if payload.get('has_switch') and payload.get('switch_ip'):
            sw_ip     = payload['switch_ip']
            sw_vendor = payload.get('switch_vendor', 'juniper')

            try:
                from devices.models import Switch
                sw = Switch.objects.get(loopback_ip=sw_ip)
                if 'cisco' in getattr(sw, 'model', '').lower():
                    sw_vendor = 'cisco'
            except Exception:
                logger.warning(f"Switch {sw_ip} not found in Switch model.")

            sw_username = router.ssh_username
            sw_password = router.ssh_password

            if sw_vendor == 'cisco':
                sw_driver = CiscoSwitchDriver(sw_ip, sw_username, sw_password)
            else:
                sw_driver = JuniperSwitchDriver(sw_ip, sw_username, sw_password)

            sw_config = sw_driver.generate_config(payload)
            sw_output = sw_driver.execute(sw_config)
            logger.info(f"Switch {sw_ip} provisioned: {sw_output}")

            script_parts.append(
                f"==> port sur sw sans NAT avec CPE\n\n"
                f"<{sw_ip}>\n"
                f"{sw_driver.render_script(payload)}"
            )

        pe_driver = get_vendor_driver(
            router.vendor,
            router.loopback_ip,
            router.ssh_username,
            router.ssh_password,
        )

        config_data      = pe_driver.generate_config(payload)
        execution_output = pe_driver.execute(config_data)

        if payload.get('has_switch'):
            script_parts.append(
                f"\n<{router.loopback_ip}>\n"
                f"{pe_driver.render_script(payload, router_ip=router.loopback_ip).split(chr(10), 4)[-1]}"
            )
        else:
            script_parts.append(
                pe_driver.render_script(payload, router_ip=router.loopback_ip)
            )

        task.status        = 'completed'
        task.result        = execution_output
        task.script_output = '\n'.join(script_parts)
        task.save()

        port.admin_status     = 'active'
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


# ============================================================
# TASK 5 — LIBERATE PORT (undo internet provisioning)
# ============================================================

@shared_task(bind=True)
def execute_port_liberation(self, task_id, router_id, port_id):
    from devices.models import Router, Port
    from provisioning.models import ProvisioningTask

    try:
        task   = ProvisioningTask.objects.get(id=task_id)
        router = Router.objects.get(id=router_id)
        port   = Port.objects.get(id=port_id)
    except Exception as e:
        return f"DB record not found: {e}"

    lock_key = f'device_lock_{router.loopback_ip}'
    if not redis_client.set(lock_key, f"liberation_{task_id}", nx=True, ex=300):
        raise self.retry(countdown=60, max_retries=3)

    try:
        params    = task.parameters
        vlan      = params.get('vlan')
        vendor    = router.vendor.lower()
        port_name = _normalize_huawei_port(port.port_full_name)

        if vlan and '.' not in port_name:
            sub_iface = f"{port_name}.{vlan}"
        else:
            sub_iface = port_name

        vrf_name = params.get('vrf_name', 'Internet_vpn')

        if vendor == 'huawei':
            output_lines = []

            with ConnectHandler(**_huawei_device_params(
                router.loopback_ip, router.ssh_username, router.ssh_password
            )) as conn:

                conn.write_channel('system-view\n')
                time.sleep(3)
                conn.read_channel()

                _hw_send(conn, f'undo interface {sub_iface}', output_lines, sleep=3)

                _hw_send(conn, f'interface {port_name}', output_lines, sleep=2)
                _hw_send(conn, 'undo description', output_lines)
                _hw_send(conn, 'shutdown', output_lines)

                conn.write_channel('quit\n')
                time.sleep(2)
                conn.read_channel()

                ce_ip           = params.get('ce_ip_address') or params.get('ce_ip', '')
                cust_lan_prefix = params.get('customer_lan_prefix', '')
                nat_mode        = params.get('nat_mode', '')

                if (
                    nat_mode in ('sans_nat_avec_cpe', 'avec_nat')
                    and ce_ip
                    and cust_lan_prefix
                ):
                    _hw_send(
                        conn,
                        f'undo ip route-static vpn-instance {vrf_name} {cust_lan_prefix} {ce_ip}',
                        output_lines,
                        check_error=False
                    )

                _hw_commit_save(conn, output_lines)

            execution_output = "\n".join(output_lines)

        elif vendor == 'juniper':
            junos_port = port_name[:-2] if port_name.endswith('.0') else port_name
            unit       = str(vlan) if vlan else '0'

            lines = [
                f'delete interfaces {junos_port} unit {unit}',
                f'delete routing-instances INTERNET interface {junos_port}.{unit}',
            ]

            nat_mode        = params.get('nat_mode', '')
            ce_ip           = params.get('ce_ip_address') or params.get('ce_ip', '')
            cust_lan_cidr   = params.get('customer_lan_cidr', '')

            if (
                nat_mode in ('sans_nat_avec_cpe', 'avec_nat')
                and ce_ip
                and cust_lan_cidr
            ):
                lines.append(
                    f'delete routing-instances INTERNET routing-options static route {cust_lan_cidr}'
                )

            config_string    = '\n'.join(lines)
            execution_output = _juniper_push(
                router.loopback_ip, router.ssh_username, router.ssh_password, config_string
            )

        else:
            raise ValueError(f"Unsupported vendor: {router.vendor}")

        task.status        = 'liberated'
        task.result        = execution_output
        task.save()

        port.admin_status     = 'available'
        port.oper_status      = 'down'
        port.port_description = ''
        port.save()

        return f"Port {port.port_full_name} liberated successfully."

    except Exception as e:
        logger.error(f"Liberation failed on {router.loopback_ip}: {str(e)}")
        task.status = 'failed'
        task.result = f"Liberation error: {str(e)}"
        task.save()
        raise e

    finally:
        redis_client.delete(lock_key)
