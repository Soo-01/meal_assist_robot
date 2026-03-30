#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Dynamixel Debug Menus (NO Vosk / NO Audio)
- Keyboard only: a (next/confirm), s (select/start), q (cancel/quit)
- Main Menu
  1) Angle Teach Mode
     - Press 's' to start (Torque OFF for manual teaching)
     - Step 1: LOWER angle → move arm by hand → press 'a' to confirm, 'q' to cancel
     - Step 2: RAISE angle  → move arm by hand → press 'a' to confirm, 'q' to cancel
     - On finish/cancel: set goal to present (anti-jump), Torque ON
     - Persist angles to motor_angles.json
  2) Repeat Mode
     - Uses persisted LOWER/RAISE angles
     - Loop: LOWER → (10s) → RAISE → (10s) → LOWER → ...
     - Press 'q' to stop; returns at LOWER
"""

import sys, time, json, tty, termios, select
from pathlib import Path
from dynamixel_sdk import PortHandler, PacketHandler

# ====================== User Configuration ======================
BASE_DIR = Path(__file__).resolve().parent

# Dynamixel
DEVICENAME       = '/dev/ttyACM0'
PROTOCOL_VERSION = 2.0
BAUDRATE         = 57600
DXL_IDS          = (1, 2)  # (RIGHT, LEFT)

ADDR_TORQUE_ENABLE        = 64
ADDR_GOAL_POSITION        = 116
ADDR_PRESENT_POSITION     = 132
ADDR_PROFILE_ACCELERATION = 108
ADDR_PROFILE_VELOCITY     = 112
TORQUE_ENABLE             = 1
TORQUE_DISABLE            = 0

PROFILE_ACCEL_VALUE       = 10
PROFILE_VEL_VALUE         = 20

# Angle mapping (empirical calibration)
ANGLE_MIN = 0
RIGHT_AT_ANGLE_MIN = 4076
LEFT_AT_ANGLE_MIN  = 2551
ANGLE_MAX = 75
RIGHT_AT_ANGLE_MAX = 3431
LEFT_AT_ANGLE_MAX  = 3197

# Defaults (overridden by persisted config)
DEFAULT_LOWER = 20
DEFAULT_RAISE = 70
REPEAT_WAIT_SEC = 10  # seconds

# Persistent config path
CONFIG_PATH = BASE_DIR / "motor_angles.json"
# ===============================================================

# =============== Globals ===============
portHandler = None
packetHandler = None
ANGLE_LOWER = DEFAULT_LOWER
ANGLE_RAISE = DEFAULT_RAISE
# =======================================


# ---------------------- Config (Persist) ----------------------
def load_angles():
    global ANGLE_LOWER, ANGLE_RAISE
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            ANGLE_LOWER = int(data.get("angle_lower", DEFAULT_LOWER))
            ANGLE_RAISE = int(data.get("angle_raise", DEFAULT_RAISE))
            print(f"[CONFIG] Loaded: LOWER={ANGLE_LOWER} deg, RAISE={ANGLE_RAISE} deg")
        except Exception as e:
            print(f"[CONFIG] Load failed, using defaults. Error: {e}")
            ANGLE_LOWER = DEFAULT_LOWER
            ANGLE_RAISE = DEFAULT_RAISE
    else:
        print(f"[CONFIG] No file, using defaults: LOWER={ANGLE_LOWER} deg, RAISE={ANGLE_RAISE} deg")

def save_angles():
    try:
        data = {"angle_lower": ANGLE_LOWER, "angle_raise": ANGLE_RAISE}
        CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[CONFIG] Saved: LOWER={ANGLE_LOWER} deg, RAISE={ANGLE_RAISE} deg → {CONFIG_PATH}")
    except Exception as e:
        print(f"[CONFIG] Save failed: {e}")


# ---------------------- TTY utils ----------------------
def get_key():
    """Read a single key (no Enter)."""
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    return ch

def _enter_cbreak():
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    tty.setcbreak(fd)
    return fd, old

def _restore_term(fd, old):
    termios.tcsetattr(fd, termios.TCSADRAIN, old)

def kbhit(timeout_sec=0.0):
    r, _, _ = select.select([sys.stdin], [], [], timeout_sec)
    return bool(r)

def read_char_nonblock():
    if kbhit(0.0):
        return sys.stdin.read(1)
    return None


# ---------------------- Math helpers ----------------------
def clamp(v, lo, hi):
    return max(lo, min(hi, v))

def calculate_positions(angle_deg: int):
    angle_deg = clamp(angle_deg, ANGLE_MIN, ANGLE_MAX)
    ratio = (angle_deg - ANGLE_MIN) / (ANGLE_MAX - ANGLE_MIN)
    right_goal = RIGHT_AT_ANGLE_MIN + (RIGHT_AT_ANGLE_MAX - RIGHT_AT_ANGLE_MIN) * ratio
    left_goal  = LEFT_AT_ANGLE_MIN  + (LEFT_AT_ANGLE_MAX  - LEFT_AT_ANGLE_MIN) * ratio
    return int(round(right_goal)), int(round(left_goal))

def invert_angle_from_pos(right_pos: int, left_pos: int):
    """Estimate angle (deg) from current positions of both motors (average of inverted mappings)."""
    r_ratio = (right_pos - RIGHT_AT_ANGLE_MIN) / (RIGHT_AT_ANGLE_MAX - RIGHT_AT_ANGLE_MIN)
    r_angle = ANGLE_MIN + r_ratio * (ANGLE_MAX - ANGLE_MIN)
    l_ratio = (left_pos - LEFT_AT_ANGLE_MIN) / (LEFT_AT_ANGLE_MAX - LEFT_AT_ANGLE_MIN)
    l_angle = ANGLE_MIN + l_ratio * (ANGLE_MAX - ANGLE_MIN)
    est = (r_angle + l_angle) / 2.0
    return clamp(int(round(est)), ANGLE_MIN, ANGLE_MAX)


# ---------------------- Dynamixel ----------------------
def setup_dynamixel():
    ph = PortHandler(DEVICENAME)
    pk = PacketHandler(PROTOCOL_VERSION)
    if not ph.openPort():
        sys.exit(f"[DXL] Failed to open port: {DEVICENAME}")
    if not ph.setBaudRate(BAUDRATE):
        sys.exit(f"[DXL] Failed to set baudrate: {BAUDRATE}")
    return ph, pk

def set_motor_profiles(ph, pk):
    for dxl_id in DXL_IDS:
        pk.write4ByteTxRx(ph, dxl_id, ADDR_PROFILE_ACCELERATION, PROFILE_ACCEL_VALUE)
        pk.write4ByteTxRx(ph, dxl_id, ADDR_PROFILE_VELOCITY,    PROFILE_VEL_VALUE)
    print(f"[DXL] Profiles set → Accel={PROFILE_ACCEL_VALUE}, Vel={PROFILE_VEL_VALUE}")

def set_torque(enable: bool):
    for dxl_id in DXL_IDS:
        packetHandler.write1ByteTxRx(portHandler, dxl_id, ADDR_TORQUE_ENABLE,
                                     TORQUE_ENABLE if enable else TORQUE_DISABLE)
    print(f"[DXL] Torque {'ENABLED' if enable else 'DISABLED'}")

def move_to_angle(angle: int):
    r_goal, l_goal = calculate_positions(angle)
    packetHandler.write4ByteTxRx(portHandler, DXL_IDS[0], ADDR_GOAL_POSITION, r_goal)
    packetHandler.write4ByteTxRx(portHandler, DXL_IDS[1], ADDR_GOAL_POSITION, l_goal)
    print(f"[DXL] Goal sent (Angle {angle} deg) → RIGHT:{r_goal}, LEFT:{l_goal}")

def read_positions():
    pos = {}
    for dxl_id in DXL_IDS:
        val, _, _ = packetHandler.read4ByteTxRx(portHandler, dxl_id, ADDR_PRESENT_POSITION)
        pos[dxl_id] = val
    return pos

def estimate_current_angle():
    pos = read_positions()
    r = pos.get(DXL_IDS[0], 0)
    l = pos.get(DXL_IDS[1], 0)
    ang = invert_angle_from_pos(r, l)
    return ang, pos

def set_goal_to_present():
    """Write current positions to goal to prevent jump when enabling torque."""
    pos = read_positions()
    for dxl_id in DXL_IDS:
        packetHandler.write4ByteTxRx(portHandler, dxl_id, ADDR_GOAL_POSITION, pos[dxl_id])
    print("[DXL] Goal set to present positions (anti-jump).")


# ---------------------- Menus ----------------------
def submenu_teach():
    """
    Angle Teach Mode
    - Press 's' to start (Torque OFF). Move the arm by hand.
      1) LOWER angle: move → press 'a' to confirm / 'q' to cancel
      2) RAISE  angle: move → press 'a' to confirm / 'q' to cancel
    - On finish/cancel: set goal to present, then Torque ON
    - Persist the confirmed angles and update runtime variables
    """
    print("[SUBMENU: Angle Teach] s=start (Torque OFF), q=cancel")
    while True:
        key = get_key()
        if key == "s":
            print("[TEACH] Torque DISABLED for manual movement. (CAUTION: Support the arm to avoid falling.)")
            set_torque(False)
            try:
                # 1) LOWER
                print("\n[TEACH] LOWER: Move arm to your desired LOWER angle, then press 'a' to confirm ('q' to cancel).")
                taught_lower = teach_one_angle()
                if taught_lower is None:
                    print("[TEACH] Canceled.")
                    return

                # 2) RAISE
                print("\n[TEACH] RAISE: Move arm to your desired RAISE angle, then press 'a' to confirm ('q' to cancel).")
                taught_raise = teach_one_angle()
                if taught_raise is None:
                    print("[TEACH] Canceled.")
                    return

                # Make sure LOWER <= RAISE
                lower = min(taught_lower, taught_raise)
                raise_ = max(taught_lower, taught_raise)

                # Update + persist
                global ANGLE_LOWER, ANGLE_RAISE
                ANGLE_LOWER = lower
                ANGLE_RAISE = raise_
                save_angles()
                print(f"[TEACH] Done: LOWER={ANGLE_LOWER} deg, RAISE={ANGLE_RAISE} deg\n")

            finally:
                # Anti-jump → then Torque ON
                try:
                    set_goal_to_present()
                except Exception as e:
                    print(f"[DXL] set_goal_to_present failed: {e}")
                set_torque(True)
                print("[TEACH] Torque ENABLED (holding current pose)")

            return

        elif key == "q":
            print("[INFO] Teach canceled.")
            return
        else:
            print("[INFO] Press 's' to start or 'q' to cancel.")

def teach_one_angle():
    """
    With torque OFF, show live estimated angle.
    'a' → return current angle; 'q' → return None.
    """
    fd, old = _enter_cbreak()
    try:
        last_print = 0.0
        while True:
            now = time.monotonic()
            if now - last_print >= 0.3:
                ang, pos = estimate_current_angle()
                sys.stdout.write(
                    f"\r  Live angle ≈ {ang:>3} deg   (R={pos.get(DXL_IDS[0],0)}, L={pos.get(DXL_IDS[1],0)})   a=confirm / q=cancel   "
                )
                sys.stdout.flush()
                last_print = now

            ch = read_char_nonblock()
            if ch == 'a':
                ang, _ = estimate_current_angle()
                print(f"\n  → Confirmed angle: {ang} deg")
                return ang
            elif ch == 'q':
                print("\n  → Canceled")
                return None

            time.sleep(0.03)
    finally:
        _restore_term(fd, old)

def submenu_repeat():
    """
    Repeat Mode:
    - Uses current ANGLE_LOWER / ANGLE_RAISE
    - Loop: LOWER → (10s) → RAISE → (10s) → LOWER → ...
    - Press 'q' to stop and return to main menu (stop at LOWER).
    """
    print("[SUBMENU: Repeat] Press 'q' to stop and return to menu.")
    move_to_angle(ANGLE_LOWER)
    time.sleep(0.5)

    fd, old = _enter_cbreak()
    try:
        state_high = False  # False: LOWER, True: RAISE
        while True:
            # Wait while checking for 'q'
            t_end = time.monotonic() + REPEAT_WAIT_SEC
            while time.monotonic() < t_end:
                ch = read_char_nonblock()
                if ch == 'q':
                    print("[Repeat] Stop requested → going to LOWER and returning to menu.")
                    move_to_angle(ANGLE_LOWER)
                    time.sleep(0.5)
                    current = read_positions()
                    print(f"[DXL] Actual (exit) → {current}")
                    return
                time.sleep(0.05)

            # Toggle
            if not state_high:
                move_to_angle(ANGLE_RAISE)
                state_high = True
            else:
                move_to_angle(ANGLE_LOWER)
                state_high = False

            time.sleep(0.8)
            current = read_positions()
            print(f"[DXL] Actual → {current}")
    finally:
        _restore_term(fd, old)

def handle_menu():
    options = [
        ("Angle Teach Mode", 1),
        ("Repeat Mode", 2),
    ]
    cursor = 0
    print("[MAIN MENU] a=next, s=select, q=quit")
    while True:
        print(f"Current selection → {options[cursor][0]}")
        key = get_key()
        if key == "a":
            cursor = (cursor + 1) % len(options)
        elif key == "s":
            text, idx = options[cursor]
            print(f"[MENU] Executing: {text}")
            if idx == 1:
                submenu_teach()
            elif idx == 2:
                submenu_repeat()
            print("[INFO] Back to main menu.")
        elif key == "q":
            print("[INFO] Quit pressed from main menu.")
            return


# ---------------------- Main ----------------------
def main():
    global portHandler, packetHandler

    # Load persisted angles
    load_angles()

    # Setup Dynamixel
    portHandler, packetHandler = setup_dynamixel()
    try:
        set_motor_profiles(portHandler, packetHandler)
        set_torque(True)
        print("[DXL] Torque enabled.")

        # Move to safe initial angle (persisted LOWER)
        move_to_angle(ANGLE_LOWER)
        time.sleep(0.5)

        # Keyboard-only menus
        handle_menu()

    finally:
        try:
            set_torque(False)
        except Exception:
            pass
        try:
            portHandler.closePort()
        except Exception:
            pass
        print("[DXL] Torque disabled, port closed.")


if __name__ == "__main__":
    main()
