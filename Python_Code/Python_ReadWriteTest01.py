#!/usr/bin/env python
# -*- coding: utf-8 -*-

import threading
import os, time, sys, platform, glob

# ----------------------------
# Cross-platform getch() 구현
# ----------------------------
try:
    import msvcrt  # Windows
    def getch():
        return msvcrt.getch().decode(errors="ignore")
    _IS_WINDOWS = True
except Exception:
    import termios, tty  # POSIX
    def getch():
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        return ch
    _IS_WINDOWS = False

from dynamixel_sdk import *  # Uses Dynamixel SDK library (includes GroupSyncWrite)

# -------------------------------------------------
# Helper functions for Dynamixel SyncWrite encoding
# -------------------------------------------------
def DXL_LOBYTE(w):  return w & 0xFF
def DXL_HIBYTE(w):  return (w >> 8) & 0xFF
def DXL_LOWORD(l):  return l & 0xFFFF
def DXL_HIWORD(l):  return (l >> 16) & 0xFFFF


#--------------------------------------
# 키 입력 감지 함수 (Windows 전용)
#--------------------------------------


def key_listener():
    global KeyISR_running, KeyCommand
    while KeyISR_running:
        key = getch()
        if key == chr(0x1b):  # ESC
            print("ESC pressed. Exiting...")
            KeyISR_running = False
        elif key == 's':
            print("Key 's' pressed. Performing action...")
        elif key == 'o':
            print("Key 'o' pressed. Enabling torque...")
        elif key == 'f':
            print("Key 'f' pressed. Disabling torque...")
        elif key == chr(0x0d) or key == '\r' or key == '\n':  # Enter (크로스플랫폼)
            print(f"Command entered: {KeyCommand}")
            KeyCommand = ''
        else:
            print(f"Key '{key}' pressed.")
            KeyCommand += key

#******************************************************
# Key input thread processing
KeyISR_running = True
listener_thread = threading.Thread(target=key_listener, daemon=True)
# listener_thread.start()
KeyCommand = ''

#********* DYNAMIXEL Model definition *********
MY_DXL = 'X_SERIES'       # X330 (5.0 V recommended), X430, X540, 2X430
# MY_DXL = 'MX_SERIES'
# MY_DXL = 'PRO_SERIES'
# MY_DXL = 'PRO_A_SERIES'
# MY_DXL = 'P_SERIES'
# MY_DXL = 'XL320'

# Control table address
ADDR_TORQUE_ENABLE          = 64
ADDR_GOAL_POSITION          = 116
ADDR_PRESENT_POSITION       = 132
ADDR_LED_RED                = 65
DXL_MINIMUM_POSITION_VALUE  = 0
DXL_MAXIMUM_POSITION_VALUE  = 4095
BAUDRATE                    = 57600
ADDR_MAX_POSITION           = 48
ADDR_MIN_POSITION           = 52
ADDR_PROFILE_ACC            = 108
ADDR_PROFILE_VEL            = 112
DXL_MOVING                  = 122

PROTOCOL_VERSION            = 2.0
DXL_ID                      = 1

def _auto_detect_port():
    # 우선 환경변수
    env = os.environ.get("DXL_PORT")
    if env:
        return env

    if _IS_WINDOWS:
        # COM5~COM20 후보 스캔
        for i in range(20, 0, -1):
            yield f"COM{i}"
    else:
        # 리눅스 계열: ttyUSB*, ttyACM* 후보 스캔 (번호 낮은 순 우선)
        for pat in ("/dev/ttyUSB*", "/dev/ttyACM*"):
            for path in sorted(glob.glob(pat)):
                yield path

def _pick_port_or_default():
    # 명시 환경변수 우선
    if os.environ.get("DXL_PORT"):
        return os.environ["DXL_PORT"]
    # 플랫폼 기본값
    return "COM9" if _IS_WINDOWS else "/dev/ttyACM0"

# 최종 포트 후보 리스트
_PORT_CANDIDATES = list(_auto_detect_port()) or [_pick_port_or_default()]

# 기본값 (첫번째 후보)
DEVICENAME = _PORT_CANDIDATES[0]

TORQUE_ENABLE               = 1
TORQUE_DISABLE              = 0
DXL_MOVING_STATUS_THRESHOLD = 3
DXL_MAX_ID                  = 6

index = 0
dxl_goal_position = [420, 1640]

#============================
# DXL Profile
#=============================
DXL_Profile = [ [1400,2680],   # Link 1
                [590,2400],    # Link 2
                [1500,3500],   # Link 3
                [1560,3600],   # Link 4
                [2400,3770],   # Link 5
                [500,3550] ]   # Link 6

# ===== 기본 속도/가속도 프로파일(ACC, VEL) =====
V_Profile = [ [20 , 10],
              [20, 30],
              [20 , 20],
              [20, 35],
              [20, 15],
              [20, 30] ]

# ===== 보조(다른 동작에서 사용할) 속도/가속도 프로파일 =====
V_Profile2 = [ [20 , 10],
              [20, 30],
              [20 , 20],
              [20, 35],
              [20, 15],
              [20, 30] ]

TargetPos = []

# Initialize PortHandler instance
portHandler = PortHandler(DEVICENAME)
packetHandler = PacketHandler(PROTOCOL_VERSION)

# Open port
if portHandler.openPort():
    if portHandler.setBaudRate(BAUDRATE):
        print("Succeeded to open the port and change the baudrate")
    else:
        print("Failed to change the baudrate")
else:
    print("Failed to open the port")
    print("Hints: check cable, permissions (dialout), and DXL_PORT env.")
    sys.exit(1)


def init_joint_POS(_Profile):
    for _ID in range(1, DXL_MAX_ID+1):
        packetHandler.write4ByteTxRx(portHandler, _ID, ADDR_MIN_POSITION, _Profile[_ID-1][0])
        packetHandler.write4ByteTxRx(portHandler, _ID, ADDR_MAX_POSITION, _Profile[_ID-1][1])

def init_joint_VELACC(_Profile):
    for _ID in range(1, DXL_MAX_ID+1):
        packetHandler.write4ByteTxRx(portHandler, _ID, ADDR_PROFILE_ACC, _Profile[_ID-1][0])
        packetHandler.write4ByteTxRx(portHandler, _ID, ADDR_PROFILE_VEL, _Profile[_ID-1][1])

def set_joint_VELACC(joints, profile):
    if joints and profile and len(joints) == len(profile):
        for i, _ID in enumerate(joints):
            packetHandler.write4ByteTxRx(portHandler, _ID, ADDR_PROFILE_ACC, profile[i][0])
            packetHandler.write4ByteTxRx(portHandler, _ID, ADDR_PROFILE_VEL, profile[i][1])

def get_current_joint_positions(print_flag=True):
    positions = []
    for _ID in range(1, DXL_MAX_ID+1):
        present_position, comm_result, error = packetHandler.read4ByteTxRx(
            portHandler, _ID, ADDR_PRESENT_POSITION
        )
        positions.append(present_position)
    if print_flag:
        print(" [CJP] Current Joint Positions: ", positions)
    return positions

def set_joint_torque(On_Off=True):
    for _ID in range(1, DXL_MAX_ID+1):
        if On_Off:
            # 토크 ON
            packetHandler.write1ByteTxRx(portHandler, _ID, ADDR_TORQUE_ENABLE, TORQUE_ENABLE)
        else:
            # 토크 OFF
            packetHandler.write1ByteTxRx(portHandler, _ID, ADDR_TORQUE_ENABLE, TORQUE_DISABLE)

# -------------------------------------------------
# UPDATED: simultaneous start for ALL joints
# -------------------------------------------------
def set_joint_positions(goal_position):
    """
    모든 관절(1~6)의 목표 위치를 GroupSyncWrite로 한 번에 전송해서
    동시에 출발하게 만든다.
    """
    global TargetPos
    TargetPos = goal_position[:]  # copy for safety

    groupSyncWrite = GroupSyncWrite(portHandler, packetHandler, ADDR_GOAL_POSITION, 4)

    for _ID in range(1, DXL_MAX_ID+1):
        pos = int(goal_position[_ID-1])
        param_goal_position = [
            DXL_LOBYTE(DXL_LOWORD(pos)),
            DXL_HIBYTE(DXL_LOWORD(pos)),
            DXL_LOBYTE(DXL_HIWORD(pos)),
            DXL_HIBYTE(DXL_HIWORD(pos))
        ]
        groupSyncWrite.addParam(_ID, param_goal_position)

    groupSyncWrite.txPacket()
    groupSyncWrite.clearParam()

# -------------------------------------------------
# UPDATED: simultaneous start for a SUBSET of joints
# -------------------------------------------------
def set_joint_positions2(joints, positions):
    """
    joints:    [id1, id2, ...]
    positions: [pos1, pos2, ...]
    선택된 관절들만 동시에 이동시키는 버전.
    SNAP, 수동조정(move_one_motor_by_delta), move_rel 등에서 사용.
    """
    if not (joints and positions and len(joints) == len(positions)):
        return

    groupSyncWrite = GroupSyncWrite(portHandler, packetHandler, ADDR_GOAL_POSITION, 4)

    for i, _ID in enumerate(joints):
        pos = int(positions[i])
        param_goal_position = [
            DXL_LOBYTE(DXL_LOWORD(pos)),
            DXL_HIBYTE(DXL_LOWORD(pos)),
            DXL_LOBYTE(DXL_HIWORD(pos)),
            DXL_HIBYTE(DXL_HIWORD(pos))
        ]
        groupSyncWrite.addParam(_ID, param_goal_position)

    groupSyncWrite.txPacket()
    groupSyncWrite.clearParam()

def joint_moving_status2():
    for _ID in range(1, DXL_MAX_ID+1):
        moving, comm_result, error = packetHandler.read1ByteTxRx(portHandler, _ID, DXL_MOVING)
        if moving != 0:
            return True
    return False

def joint_moving_status():
    for _ID in range(1, DXL_MAX_ID+1):
        present_position, comm_result, error = packetHandler.read4ByteTxRx(
            portHandler, _ID, ADDR_PRESENT_POSITION
        )
        if abs(present_position - TargetPos[_ID-1]) > DXL_MOVING_STATUS_THRESHOLD:
            return True
    return False

# ---- 기본 프로파일(비-SNAP 동작이 따르는 값) ----
def _profile_map_from_list(plist):
    # plist: [[acc, vel], ...] 길이 6
    return {i+1: (plist[i][0], plist[i][1]) for i in range(6)}

DEFAULT_VELACC = _profile_map_from_list(V_Profile)

def restore_default_velacc(joints=(1,2,3,4,5,6)):
    prof = [[DEFAULT_VELACC[j][0], DEFAULT_VELACC[j][1]] for j in joints]
    set_joint_VELACC(list(joints), prof)

# ---------------- 공통 유틸 ----------------
def clamp(v, lo, hi):
    return max(lo, min(hi, v))

def get_joint_limits(joint_id):
    """DXL_Profile의 (min,max)를 반환"""
    mn, mx = DXL_Profile[joint_id-1]
    return mn, mx

def get_default_velacc(joint_id):
    """기본 VEL/ACC를 반환. DEFAULT_VELACC에 없으면 V_Profile 기준"""
    return DEFAULT_VELACC.get(joint_id, (V_Profile[joint_id-1][0], V_Profile[joint_id-1][1]))

# ============================================================
#                 SNAP 전용 최적화 유틸
#        - SNAP 내부는 항상 50/50로 동작
#        - 끝나면 기본 프로파일(V_Profile)로 복구
# ============================================================
def _clamp_pos(jid: int, val: int) -> int:
    jmin, jmax = DXL_Profile[jid-1]
    return max(jmin, min(val, jmax))

def _apply_50(joints):
    """SNAP에서만 사용: 지정 관절을 ACC/VEL=50,50으로 강제"""
    set_joint_VELACC(joints, [[50,50] for _ in joints])

def move_rel(deltas: dict, dwell: float = 0.5, print_after: bool = True):
    """
    SNAP 전용 상대이동
    deltas: {joint_id:int(delta), ...}
    """
    if not deltas:
        return
    joints = list(deltas.keys())
    _apply_50(joints)  # SNAP은 50/50 고정

    cur = get_current_joint_positions(print_flag=False)
    tgt = []
    for j in joints:
        newv = _clamp_pos(j, cur[j-1] + deltas[j])
        tgt.append(newv)
    set_joint_positions2(joints, tgt)

    time.sleep(dwell)
    while joint_moving_status2():
        time.sleep(0.05)
    if print_after:
        get_current_joint_positions(print_flag=True)

def move_abs(targets: dict, dwell: float = 0.5, print_after: bool = True):
    """
    SNAP 전용 절대이동
    targets: {joint_id:int(target_abs), ...}
    """
    if not targets:
        return
    joints = list(targets.keys())
    _apply_50(joints)

    tgt = []
    for j in joints:
        tgt.append(_clamp_pos(j, targets[j]))
    set_joint_positions2(joints, tgt)

    time.sleep(dwell)
    while joint_moving_status2():
        time.sleep(0.05)
    if print_after:
        get_current_joint_positions(print_flag=True)

# ------------------------------------------------------------
#                      SNAP 들 (개편/최적화)
#   - 기존 시퀀스 유지하되 move_rel()로 단순화
#   - SNAP 내부는 50/50 고정, 종료 시 restore_default_velacc()
# ------------------------------------------------------------

def snap1_1():
    print("[SNAP] snap1_1 start")
    move_rel({5: -900}, dwell=0.5)
    move_rel({6: -1300},  dwell=0.5)
    move_rel({4: -300}, dwell=0.5)
    move_rel({3: +150},  dwell=0.5)
    move_rel({5: +450}, dwell=0.5)
    move_rel({1: +20}, dwell=0.5)
    move_rel({2: +75}, dwell=0.5)    #75
    move_rel({6: +900, 1: +50}, dwell=0.5)
    restore_default_velacc()
    print("[SNAP] snap1_1 done.")

def snap1_2():
    print("[SNAP] snap1_2 start")
    move_rel({5: -900}, dwell=0.5)
    move_rel({6: -1300},  dwell=0.5)
    move_rel({4: -300}, dwell=0.5)
    move_rel({3: +150},  dwell=0.5)
    move_rel({5: +450}, dwell=0.5)
    move_rel({1: +20}, dwell=0.5)
    move_rel({2: +75}, dwell=0.5)
    move_rel({6: +900, 1: +50}, dwell=0.5)
    restore_default_velacc()
    print("[SNAP] snap1_2 done.")



# SNAP 2_1 (2번 왼쪽))
def snap2_1():
    print("[SNAP] snap2_1 start")
    move_rel({4: -300}, dwell=0.5)
    move_rel({1: +35},  dwell=1.0)
    move_rel({2: -90}, dwell=1.0)
    move_rel({3: +200},  dwell=0.5)
    move_rel({5: -400}, dwell=0.5)
    move_rel({6: -900}, dwell=1.0)
    move_rel({5: +500}, dwell=0.5)
    move_rel({6: +900}, dwell=1.0)
    restore_default_velacc()
    print("[SNAP] snap2_1 done.")
    
    
# SNAP 2_2 (2번 오른쪽)
def snap2_2():
    print("[SNAP] snap2_1 start")
    move_rel({2:-60}, dwell=0.5)
    move_rel({3:+150}, dwell=0.5)
    move_rel({4:-220}, dwell=0.5)
    move_rel({5:-400}, dwell=0.5)
    move_rel({6:-900}, dwell=0.5)
    move_rel({5:+500}, dwell=0.5)
    move_rel({6:+900}, dwell=0.5)
    restore_default_velacc()
    print("[SNAP] snap2_2 done.")

# SNAP 3_1 (3번 왼쪽)
def snap3_1():
    print("[SNAP] snap3_1 start")
    move_rel({5: -500}, dwell=0.5)   # Step 1
    move_rel({4: -200},  dwell=0.5)   # Step 2
    move_rel({3: +100}, dwell=0.5)   # Step 3
    move_rel({6: -800}, dwell=0.5)   # Step 4
    move_rel({2: +50}, dwell=0.5)   # Step 5
    move_rel({5: +250}, dwell=0.5)   # Step 6
    move_rel({6: +900}, dwell=0.5)  # Step 7
    restore_default_velacc()
    print("[SNAP] snap3_1 done.")

# SNAP 3_2 (3번 오른쪽)
def snap3_2():
    print("[SNAP] snap3_2 start")
    move_rel({1: -40}, dwell=0.5)  # Step 1
    move_rel({5: -500}, dwell=0.5)  # Step 2
    move_rel({6: -900}, dwell=0.5)  # Step 3
    move_rel({3: +100},  dwell=0.5)  # Step 4
    move_rel({2: -50}, dwell=0.5)  # Step 5
    move_rel({5: +300}, dwell=0.5)  # Step 6
    move_rel({6: +1100}, dwell=0.5)  # Step 5
    restore_default_velacc()
    print("[SNAP] snap3_2 done.")

# SNAP 4_1 (4번 음식 왼쪽 전용)
def snap4_1():
    print("[SNAP] snap4_1 start")
    move_rel({1: +80}, dwell=1)
    move_rel({5: -500}, dwell=1)
    move_rel({6: -1100}, dwell=1)
    move_rel({4: -400},  dwell=1)
    move_rel({2: -100}, dwell=1)
    move_rel({3: +200}, dwell=0.5)
    move_rel({5: +400}, dwell=0.5)
    move_rel({6: +900, 1: +100}, dwell=0.5)
    restore_default_velacc()
    print("[SNAP] snap4_1 done.")

# SNAP 4_2 (4번 음식 중간(완료) 전용)
def snap4_2():
    print("[SNAP] snap4_2 start")
    move_rel({2:-150}, dwell=1)
    move_rel({5:-500}, dwell=1)
    move_rel({6:-1200}, dwell=1)
    move_rel({4:-340},  dwell=1)
    move_rel({1:+110}, dwell=1)
    move_rel({3:+250}, dwell=0.5)
    move_rel({5:+350}, dwell=0.5)
    move_rel({6:+1000}, dwell=0.5)
    restore_default_velacc()
    print("[SNAP] snap4_2 done.")

# SNAP 4_3 (4번 오른쪽 전용)
def snap4_3():
    print("[SNAP] Snap4_3 start")
    move_rel({1: -13}, dwell=0.5)
    move_rel({5: -500},  dwell=0.5)
    move_rel({6: -1100}, dwell=0.5)
    move_rel({2: -260}, dwell=0.5)
    move_rel({3: +360}, dwell=0.5)
    move_rel({4: -200}, dwell=0.5)
    move_rel({5:+400}, dwell=0.5)
    move_rel({6:+1100}, dwell=0.5)
    restore_default_velacc()
    print("[SNAP] Snap4_3 done.")




# SNAP 5_1(음식 5_왼쪽)
def snap5_1():
    print("[SNAP] snap5_1 start")
    move_rel({5:-900}, dwell=1.0)
    move_rel({2:-210}, dwell=0.5)
    move_rel({3:+331}, dwell=0.5)
    move_rel({4:-225}, dwell=0.5)
    move_rel({6:-900}, dwell=0.5)
    move_rel({5:+400}, dwell=0.5)
    move_rel({6:+900}, dwell=0.5)
    restore_default_velacc()
    print("[SNAP] snap5_1 done.")

# SNAP 5_1(음식 5_왼쪽)
def snap5_2():
    print("[SNAP] snap5_2 start")
    move_rel({5:-900}, dwell=1.0)
    move_rel({2:-210}, dwell=0.5)
    move_rel({3:+331}, dwell=0.5)
    move_rel({4:-225}, dwell=0.5)
    move_rel({6:-900}, dwell=0.5)
    move_rel({5:+400}, dwell=0.5)
    move_rel({6:+900}, dwell=0.5)
    restore_default_velacc()
    print("[SNAP] snap5_2 done.")
    
# SNAP 5_1(음식 5_왼쪽)
def snap5_3():
    print("[SNAP] snap5_3 start")
    move_rel({5:-900}, dwell=1.0)
    move_rel({2:-210}, dwell=0.5)
    move_rel({3:+331}, dwell=0.5)
    move_rel({4:-225}, dwell=0.5)
    move_rel({6:-900}, dwell=0.5)
    move_rel({5:+400}, dwell=0.5)
    move_rel({6:+900}, dwell=0.5)
    restore_default_velacc()
    print("[SNAP] snap5_3 done.")
    
# ====== 테스트용 단일 모터 상대 이동 (수동) ======
def move_one_motor_by_delta(joint_id: int, delta: int):
    """
    현재 위치에서 delta만큼 이동 (한 개 모터만)
    비-SNAP 수동테스트는 기본 프로파일 사용
    """
    pos_all = get_current_joint_positions(print_flag=False)
    cur = pos_all[joint_id-1]

    # 리미트 클램프
    mn, mx = get_joint_limits(joint_id)
    goal = clamp(cur + int(delta), mn, mx)

    # 이 모터의 VEL/ACC (기본 프로파일)
    acc, vel = get_default_velacc(joint_id)
    set_joint_VELACC([joint_id], [[acc, vel]])

    # 목표치 전송 (subset sync)
    set_joint_positions2([joint_id], [goal])

    # 완료 대기
    time.sleep(0.05)
    while joint_moving_status2():
        get_current_joint_positions(0)

    print(f"[OK] Motor {joint_id} → {goal}")

def test():
    """
    수동 스냅 모드:
    - '모터ID 이동량'을 입력하면 해당 모터를 바로 그만큼 이동
      예) '4 -600'  (4번 모터를 -600)
    - 종료: 빈 줄 엔터 또는 'q'/'quit'
    """
    print("\n=== SNAP1_2: Manual Motor Move Mode ===")
    print("형식: '모터ID 이동량'  예)  '4 -600'  또는  '6 200'")
    print("종료: 빈 줄로 엔터  또는  q / quit")
    print("---------------------------------------")

    # 안전: 토크 ON 보장
    set_joint_torque(True)

    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[SNAP] 입력 종료.")
            break

        if line == "" or line.lower() in ("q", "quit"):
            print("[SNAP] Manual mode 종료.")
            break

        parts = line.split()
        if len(parts) != 2:
            print("잘못된 입력. 예:  '4 -600'")
            continue

        try:
            jid = int(parts[0])
            delta = int(parts[1])
        except ValueError:
            print("숫자로 입력하세요. 예:  '4 -600'")
            continue

        if not (1 <= jid <= DXL_MAX_ID):
            print(f"모터ID는 1~{DXL_MAX_ID} 사이여야 합니다.")
            continue

        # 실제 이동 (비SNAP: 기본 프로파일 사용)
        move_one_motor_by_delta(jid, delta)

    # 끝나면 속도/가속도 기본치로 복구
    restore_default_velacc(joints=(1,2,3,4,5,6))
    print("[SNAP] Done.\n")

# ====== 초기화 ======
set_joint_torque(TORQUE_DISABLE)
init_joint_POS(DXL_Profile)
init_joint_VELACC(V_Profile)  # 비SNAP 기본 프로파일 적용
time.sleep(0.3)
set_joint_torque(TORQUE_ENABLE)

TargetPos = get_current_joint_positions(0)

Home_profile  = [1510, 735, 3382, 2578, 3636, 2569]
NPOS          = [2024, 1400, 2817, 2402, 3310, 2281]
POS1          = [1974, 2000, 2250, 2591, 3000, 2312]
POS2          = [1870, 1950, 2580, 2400, 2830, 1750]
POS3          = [1697, 1850, 2700, 2470, 2725, 1721]
POS4          = [2100, 2050, 2530, 2250, 3040, 1815]
POS5          = [1858, 1725, 2900, 2404, 2836, 1726]
MPOS          = [2279, 1164, 2843, 3295, 3353, 3200]


# 1  =[1974, 2000, 2250, 2591, 3000, 2312]
# 1_1=[2011, 2091, 2451, 2310, 3010, 1918]


# 2 = [1870, 1950, 2580, 2400, 2830, 1750]
# 2_1=[1895, 1961, 2781, 2150, 2760, 1635]
# 2_2=[1910, 1893, 2811, 2184, 2425, 1750]


# 3  =[1697, 1850, 2700, 2470, 2725, 1721]
# 3_1=[1693, 1871, 2913, 2272, 2710, 1753]
# 3_2=[1675, 1788, 2891, 2496, 2811, 1768]


# 4=       [2100, 2050, 2530, 2250, 3040, 1815])
# 4_1 [2192, 1947, 2781, 1940, 2514, 707]
# 4_2 [2218, 1993, 2781, 1912, 2407, 568]
# 4_3 [2087, 1877, 2893, 2059, 2554, 721]

# 5  =[1858, 1725, 2900, 2404, 2836, 1726]
# 5_1=[1964, 1517, 3231, 2145, 2896, 1647]


command = ''

time.sleep(0.3)
set_joint_positions(Home_profile)
time.sleep(1)

while joint_moving_status2():
    get_current_joint_positions(0)
    continue

# ====== 메인 루프 ======
while True:
    get_current_joint_positions()
    print(" [TJP] Target Joint Positions : ", TargetPos)
    print("Press any key to continue! (or press ESC to quit!)")
    command = getch()
    if command == chr(0x1b):  # ESC key
        print("Exiting the program. Homing.............")
        print("  => Home Joint Positions : ", Home_profile)
        break

    if command == 'l':
        set_joint_torque(TORQUE_ENABLE)
        print('[Torque Enabled for all joints]')
        get_current_joint_positions(0)
    if command == 'p':
        set_joint_torque(TORQUE_DISABLE)
        print('[Torque disabled for all joints]')

    # SNAP들 (내부 50/50 고정)
  
    if command == 'q':
        snap1_1()
    if command == 'w':
        snap1_2()
#음식 1             
        
    if command == 'e':
        snap2_1()
    if command == 'r':
        snap2_2()
#음식 2        
        
        
    if command == 't':
        snap3_1()
    if command == 'y':
        snap3_2()
#음식 3
        
        
    if command == 'a':
        snap4_1()
    if command == 's':
        snap4_2()
    if command == 'd':
        snap4_3()
#음식 4       
        
    if command == 'f':
        snap5_1()
    if command == 'g':
        snap5_2()
    if command == 'h':
        snap5_3()
#음식 5              
        
        
        
        


    # 수동 테스트
    if command == 'o':
        test()

    # 비SNAP 이동: V_Profile2 적용 후 목표 포즈로 이동
    if command == '1':
        set_joint_VELACC([1,2,3,4,5,6], V_Profile2)
        set_joint_positions(POS1)
    if command == '2':
        set_joint_VELACC([1,2,3,4,5,6], V_Profile2)
        set_joint_positions(POS2)
    if command == '3':
        set_joint_VELACC([1,2,3,4,5,6], V_Profile2)
        set_joint_positions(POS3)
    if command == '4':
        set_joint_VELACC([1,2,3,4,5,6], V_Profile2)
        set_joint_positions(POS4)
    if command == '5':
        set_joint_VELACC([1,2,3,4,5,6], V_Profile2)
        set_joint_positions(POS5)

    if command == 'n':
        set_joint_VELACC([1,2,3,4,5,6], V_Profile)
        set_joint_positions(NPOS)

    if command == 'm':
        set_joint_VELACC([1,2,3,4,5,6], V_Profile)
        set_joint_positions(MPOS)

    time.sleep(0.3)
    while joint_moving_status2():
        get_current_joint_positions(0)
        continue

    # 루프 말미에 기본 프로파일로 복구(비SNAP 기본)
    init_joint_VELACC(V_Profile)
    index += 1
    if index > 3:
        index = 0

# ====== 종료 처리 ======
time.sleep(0.3)
set_joint_positions(Home_profile)
time.sleep(1)

while joint_moving_status2():
    get_current_joint_positions(0)
    continue

time.sleep(3)
set_joint_torque(TORQUE_DISABLE)
portHandler.closePort()
