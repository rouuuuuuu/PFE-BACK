from netmiko import ConnectHandler

# 1. Define the router credentials
huawei_router = {
    'device_type': 'huawei',
    'host': '192.168.65.130',         # Replace with your router's actual IP
    'username': 'django_admin',
    'password': 'NocTestPassword123!',
    'port': 22,                      
}

try:
    print(f"Attempting to connect to {huawei_router['host']}...")
    
    # 2. Open the connection
    net_connect = ConnectHandler(**huawei_router)
    print("Success! Logged into the router.")
    
    # 3. Send a command to the router and print the result
    output = net_connect.send_command('display ip interface brief')
    print("\n--- Router Output ---")
    print(output)
    
    # 4. Close the connection
    net_connect.disconnect()

except Exception as e:
    print(f"\nFailed to connect! Error: {e}")
