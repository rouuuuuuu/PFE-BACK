from celery import shared_task
from netmiko import ConnectHandler

@shared_task
def check_device_connection(ip_address, username, password):
    """
    A Celery background task that connects to a Huawei router via SSH
    and retrieves its version information.
    """
    # 1. Define the connection parameters for Huawei VRP
    huawei_router = {
        'device_type': 'huawei', # Netmiko's specific driver for Huawei VRP [cite: 85]
        'host': ip_address,
        'username': username,
        'password': password,
        'port': 22,
        'global_delay_factor': 2, # Gives the telecom equipment a little extra time to reply
    }

    try:
        # 2. Establish the SSH connection
        with ConnectHandler(**huawei_router) as net_connect:
            
            # 3. Send a simple, safe command
            output = net_connect.send_command('display version')
            
            # Return a success message with a snippet of the output
            return f"✅ Success! Connected to {ip_address}. Output: {output[:100]}..."
            
    except Exception as e:
        return f"❌ Connection failed for {ip_address}: {str(e)}"
