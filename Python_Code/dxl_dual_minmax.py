#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys, time
from dynamixel_sdk import *  # Dynamixel SDK
from dualmotor import angle_to_both, angles_to_soft_limits

# ---------------- User Config ----------------
DEVICENAME = '/dev/ttyACM0'
PROTOCOL_VERSION = 2.0
BAUDRATE = 57600

# Two motors: ID1=RIGHT, ID2=LEFT
DXL_IDS = (1, 2)
DXL_MAX_ID = len(DXL_IDS)

# Desired working angle range [deg]
ANGLE_MIN_CMD = 0.0
ANGLE_MAX_CMD = 75.0

# Apply software limits to match the above range
APPLY_SOFT_LIMITS = True

# Profile settings
PROFILE_ACC = 10
PROFILE_VEL = 20

# Position reach threshold
POS_EPS = 3

# ------------- Control Table (X/MX Series) -------------
ADDR_TORQUE_ENABLE    = 64
ADDR_LED_RED          = 65
ADDR_PROFILE_ACC      = 108
ADDR_PROFILE_VEL      = 112
ADDR_GOAL_POSITION    = 116  # 4 bytes
ADDR_PRESENT_POSITION = 132  # 4 bytes
ADDR_MAX_POSITION     = 48   # 4 bytes (soft limit)
ADDR_MIN_POSITION     = 52   # 4 bytes (soft limit)

TORQUE_ENABLE  = 1
TORQUE_DISABLE = 0

# -------------------- Helpers --------------------
def open_port():
    ph = PortHandler(DEVICENAME)
    if not ph.openPort():
        print("Failed to open the port")
        sys.exit(1)
    if not ph.setBaudRate(BAUDRATE):
        print("Failed to set baudrate")
        sys.exit(1)
    return ph

def set_torque(ph, pk, enable=True):
    for dxl_id in DXL_IDS:
        comm_result, error = pk.write1ByteTxRx(ph, dxl_id, ADDR_TORQUE_ENABLE, TORQUE_ENABLE if enable else TORQUE_DISABLE)
        if comm_result != COMM_SUCCESS or error != 0:
            print(f"[Torque][ID:{dxl_id}] {pk.getTxRxResult(comm_result)} {pk.getRxPacketError(error)}")

def set_profile(ph, pk, acc, vel):
    for dxl_id in DXL_IDS:
        pk.write4ByteTxRx(ph, dxl_id, ADDR_PROFILE_ACC, int(acc))
        pk.write4ByteTxRx(ph, dxl_id, ADDR_PROFILE_VEL, int(vel))

def set_soft_limits(ph, pk, right_minmax, left_minmax):
    # right_minmax = (min_num, max_num) for RIGHT(ID1)
    # left_minmax  = (min_num, max_num) for LEFT(ID2)
    pair = {DXL_IDS[0]: right_minmax, DXL_IDS[1]: left_minmax}
    for dxl_id in DXL_IDS:
        mn, mx = pair[dxl_id]
        pk.write4ByteTxRx(ph, dxl_id, ADDR_MIN_POSITION, int(mn))
        pk.write4ByteTxRx(ph, dxl_id, ADDR_MAX_POSITION, int(mx))

def get_present_positions(ph, pk):
    pos = []
    for dxl_id in DXL_IDS:
        p, comm_result, error = pk.read4ByteTxRx(ph, dxl_id, ADDR_PRESENT_POSITION)
        if comm_result != COMM_SUCCESS or error != 0:
            print(f"[Present][ID:{dxl_id}] {pk.getTxRxResult(comm_result)} {pk.getRxPacketError(error)}")
        pos.append(p)
    return pos

def set_goal_positions(ph, pk, goal_right, goal_left):
    targets = {DXL_IDS[0]: goal_right, DXL_IDS[1]: goal_left}
    for dxl_id in DXL_IDS:
        g = int(targets[dxl_id])
        comm_result, error = pk.write4ByteTxRx(ph, dxl_id, ADDR_GOAL_POSITION, g)
        if comm_result != COMM_SUCCESS or error != 0:
            print(f"[Goal][ID:{dxl_id}] {pk.getTxRxResult(comm_result)} {pk.getRxPacketError(error)}")

def wait_until_reached(ph, pk, goal_right, goal_left, eps=POS_EPS, timeout=5.0):
    t0 = time.time()
    targets = {DXL_IDS[0]: int(goal_right), DXL_IDS[1]: int(goal_left)}
    while True:
        cur = get_present_positions(ph, pk)
        if all(abs(cur[i] - targets[DXL_IDS[i]]) <= eps for i in range(DXL_MAX_ID)):
            return True
        if (time.time() - t0) > timeout:
            print("Timeout while waiting to reach target")
            return False
        time.sleep(0.02)

def move_to_angle(ph, pk, angle_deg):
    r, l = angle_to_both(angle_deg)
    set_goal_positions(ph, pk, r, l)
    ok = wait_until_reached(ph, pk, r, l)
    print(f"Move angle={angle_deg:.2f} deg -> RIGHT={r}, LEFT={l} {'(OK)' if ok else '(TIMEOUT)'}")

# -------------------- Main --------------------
def main():
    pk = PacketHandler(PROTOCOL_VERSION)
    ph = open_port()
    try:
        set_torque(ph, pk, False)          # torque off before config
        set_profile(ph, pk, PROFILE_ACC, PROFILE_VEL)

        # Apply software limits from desired angle range
        r_lim, l_lim = angles_to_soft_limits(ANGLE_MIN_CMD, ANGLE_MAX_CMD)
        if APPLY_SOFT_LIMITS:
            set_soft_limits(ph, pk, r_lim, l_lim)
            print(f"Applied soft limits: RIGHT{r_lim}, LEFT{l_lim}")

        set_torque(ph, pk, True)

        # Simple REPL
        print("Commands: m=go MIN, M=go MAX, g=go angle, p=print pos, q=quit")
        print(f"Configured angle range: [{ANGLE_MIN_CMD}, {ANGLE_MAX_CMD}] deg")

        while True:
            cmd = input("> ").strip()
            if cmd == "q":
                break
            elif cmd == "m":
                move_to_angle(ph, pk, ANGLE_MIN_CMD)
            elif cmd == "M":
                move_to_angle(ph, pk, ANGLE_MAX_CMD)
            elif cmd == "g":
                try:
                    val = float(input("angle(deg): ").strip())
                except ValueError:
                    print("Invalid angle")
                    continue
                move_to_angle(ph, pk, val)
            elif cmd == "p":
                pos = get_present_positions(ph, pk)
                print(f"Present positions: RIGHT={pos[0]}, LEFT={pos[1]}")
            else:
                print("Unknown command")

    finally:
        try:
            set_torque(ph, pk, False)
        except Exception:
            pass
        ph.closePort()
        print("Torque disabled, port closed.")

if __name__ == "__main__":
    main()
