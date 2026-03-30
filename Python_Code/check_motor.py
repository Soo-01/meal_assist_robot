#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Real-time Motor Position Monitor

This script disables motor torque and continuously reads and displays the
current position of the RIGHT (ID 1) and LEFT (ID 2) motors.
This allows you to move the motors by hand and see the position values change in real-time.

Press Ctrl+C to exit.
"""

import sys
import time
from dynamixel_sdk import *

# ---- User Configuration ----
DEVICENAME = '/dev/ttyACM0'
PROTOCOL_VERSION = 2.0
BAUDRATE = 57600

# Motor ID Settings
DXL_IDS = (1, 2)  # (RIGHT, LEFT)

# Dynamixel Control Table Addresses
ADDR_TORQUE_ENABLE = 64
ADDR_PRESENT_POSITION = 132
TORQUE_DISABLE = 0

# ---- Dynamixel Control Functions ----
def setup_dynamixel() -> tuple[PortHandler, PacketHandler]:
    """Opens the port and sets up communication."""
    portHandler = PortHandler(DEVICENAME)
    packetHandler = PacketHandler(PROTOCOL_VERSION)
    if not portHandler.openPort():
        print(f"Failed to open the port {DEVICENAME}")
        sys.exit(1)
    if not portHandler.setBaudRate(BAUDRATE):
        print(f"Failed to set the baudrate to {BAUDRATE}")
        sys.exit(1)
    return portHandler, packetHandler

def disable_torque(ph: PortHandler, pk: PacketHandler):
    """Disables torque for all motors so they can be moved by hand."""
    for dxl_id in DXL_IDS:
        pk.write1ByteTxRx(ph, dxl_id, ADDR_TORQUE_ENABLE, TORQUE_DISABLE)
    print("Torque has been disabled for all motors. You can now move them by hand.")

def read_current_positions(ph: PortHandler, pk: PacketHandler) -> dict[int, int]:
    """Reads and returns the current position of all motors."""
    positions = {}
    for dxl_id in DXL_IDS:
        present_pos, dxl_comm_result, dxl_error = pk.read4ByteTxRx(ph, dxl_id, ADDR_PRESENT_POSITION)
        if dxl_comm_result == COMM_SUCCESS and dxl_error == 0:
            positions[dxl_id] = present_pos
        else:
            positions[dxl_id] = -1  # Indicate a reading error
    return positions

# ---- Main Execution ----
def main():
    portHandler, packetHandler = setup_dynamixel()
    
    # Ensure torque is disabled
    disable_torque(portHandler, packetHandler)
    
    print("\nStarting real-time position monitoring...")
    print("Press Ctrl+C to exit.")
    
    try:
        while True:
            # Read the current positions
            current_pos = read_current_positions(portHandler, packetHandler)
            r_id, l_id = DXL_IDS
            
            # Display the values on a single line, continuously updating
            # The '\r' at the end moves the cursor to the beginning of the line
            print(f"\rCurrent Position -> RIGHT: {current_pos[r_id]:<5} | LEFT: {current_pos[l_id]:<5}", end="")
            
            # Wait for a short period before the next read
            time.sleep(0.1)

    except KeyboardInterrupt:
        print("\n\nMonitoring stopped.")
    finally:
        # Close the port when the program ends
        portHandler.closePort()
        print("Port closed.")

if __name__ == "__main__":
    main()