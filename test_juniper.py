from netmiko import ConnectHandler

# 1. Define the Juniper credentials
juniper_router = {
    'device_type': 'juniper_junos',
    'host': '192.168.65.131',        # The IP you just found!
    'username': 'root',
    'password': 'roua2002',  # <-- Replace with the password you set!
    'port': 22,                      
}

try:
    print(f"Attempting to connect to Juniper at {juniper_router['host']}...")
    
    # 2. Open the connection
    net_connect = ConnectHandler(**juniper_router)
    print("Success! Logged into the Juniper router.")
    
    # 3. Send the interface command and print the result
    output = net_connect.send_command('show interfaces terse')
    print("\n--- Juniper Interface Table ---")
    print(output)
    
    # 4. Close the connection
    net_connect.disconnect()

except Exception as e:
    print(f"\nFailed to connect! Error: {e}")
