#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NT로봇 식사보조 제어 스크립트
요구사항: SNAP 동작(빠른 왕복)은 내부에서 50/50을 사용.
그 외 모든 동작(초기화, 메뉴 이동, NPOS/MPOS 등)은 아래 V_Profile / V_Profile2를 따름.
"""

import sys, tty, termios, time, select, shutil, subprocess
from pathlib import Path
import pygame
from dynamixel_sdk import *

# ----------------- GPIO 안전 임포트 -----------------
try:
    import RPi.GPIO as GPIO
    HAS_GPIO = True
except Exception:
    HAS_GPIO = False
    class _DummyGPIO:
        BCM=IN=PUD_UP=PUD_DOWN=None
        def setmode(self,*a,**k): pass
        def setup(self,*a,**k): pass
        def input(self,*a,**k): return 0
        def cleanup(self,*a,**k): pass
    GPIO = _DummyGPIO()
    print("[GPIO] RPi.GPIO not available. GPIO features disabled.")

# ----------------- 키보드(논블로킹) -----------------
def get_key(timeout: float = 0.1):
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        r, _, _ = select.select([sys.stdin], [], [], timeout)
        if r:
            return sys.stdin.read(1)
        return None
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)

# ----------------- 오디오 -----------------
BASE_DIR  = Path(__file__).resolve().parent
AUDIO_DIR = BASE_DIR / "audio_file"

def set_system_volume_percent(percent: int = 90):
    try:
        if shutil.which("pactl"):
            subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{percent}%"],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(f"[VOLUME] {percent}% via pactl")
            return
    except Exception:
        pass
    try:
        if shutil.which("amixer"):
            for ctl in ("DAC", "Master", "Speaker", "Headphone", "PCM"):
                rc = subprocess.run(["amixer", "sset", ctl, f"{percent}%"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
                if rc == 0:
                    print(f"[VOLUME] {percent}% via amixer:{ctl}")
                    return
    except Exception:
        pass
    print("[VOLUME] Skip (no pactl/amixer)")

pygame.mixer.init(frequency=16000)
pygame.mixer.set_num_channels(8)
pygame.mixer.music.set_volume(1.0)

SOUND_CACHE = {}
def preload_sound(path: Path):
    p = Path(path)
    if not p.exists():
        print(f"[AUDIO] Missing: {p}")
        return None
    if p in SOUND_CACHE:
        return SOUND_CACHE[p]
    try:
        snd = pygame.mixer.Sound(str(p))
        SOUND_CACHE[p] = snd
        return snd
    except Exception as e:
        print(f"[AUDIO] Preload error: {p} -> {e}")
        return None

def play_audio(path: Path):
    snd = preload_sound(path)
    if snd:
        ch = snd.play()
        while ch.get_busy():
            time.sleep(0.01)

def speak_then_wait(path: Path, delay_after: float = 0.0):
    play_audio(path)
    if delay_after > 0:
        time.sleep(delay_after)

def stop_audio():
    pygame.mixer.stop()

SND_START          = AUDIO_DIR / "start.mp3"
SND_PRESS_BLUE     = AUDIO_DIR / "press_blue.mp3"
SND_SETTING_GUIDE  = AUDIO_DIR / "setting_guide.mp3"
SND_SETTING_DONE   = AUDIO_DIR / "setting_done.mp3"
SND_SETTING_UP     = AUDIO_DIR / "setting_up.mp3"
SND_MENU_SELECT    = AUDIO_DIR / "menu_select.mp3"
SND_MOVE_SELECTED  = AUDIO_DIR / "move_selected.mp3"
SND_MOVE_NEUTRAL   = AUDIO_DIR / "move_neutral.mp3"
SND_MOVE_SPECIFIC  = AUDIO_DIR / "move_specific.mp3"
SND_PIPELINE_DONE  = AUDIO_DIR / "pipeline_done.mp3"
SND_WATER_MENU     = AUDIO_DIR / "water_menu.mp3"
SND_WATER_ACTION   = AUDIO_DIR / "water_action.mp3"
SND_CHANGE_FOOD   = AUDIO_DIR / "change_food.mp3"
SND_FOODS = {
    1: AUDIO_DIR / "food1.mp3",
    2: AUDIO_DIR / "food2.mp3",
    3: AUDIO_DIR / "food3.mp3",
    4: AUDIO_DIR / "food4.mp3",
    5: AUDIO_DIR / "food5.mp3",
}
for p in [SND_START,SND_PRESS_BLUE,SND_SETTING_GUIDE,SND_SETTING_DONE,SND_MENU_SELECT,
          SND_MOVE_SELECTED,SND_MOVE_NEUTRAL,SND_MOVE_SPECIFIC,SND_PIPELINE_DONE,
          SND_WATER_MENU,SND_WATER_ACTION,*SND_FOODS.values(), SND_CHANGE_FOOD]:
    preload_sound(p)

# ----------------- Dynamixel -----------------
PROTOCOL_VERSION = 2.0
DEVICENAME = '/dev/ttyACM0'
BAUDRATE = 57600
DXL_MAX_ID = 6

ADDR_TORQUE_ENABLE     = 64
ADDR_MAX_POSITION      = 48
ADDR_MIN_POSITION      = 52
ADDR_PROFILE_ACC       = 108
ADDR_PROFILE_VEL       = 112
ADDR_GOAL_POSITION     = 116
ADDR_PRESENT_POSITION  = 132
DXL_MOVING             = 122

TORQUE_ENABLE  = 1
TORQUE_DISABLE = 0

DXL_Profile = [
    [1400, 2680], [590, 2400], [1500, 3500],
    [1560, 3600], [2400, 3770], [500, 3550],
]

# ===== 기본 속도/가속도 프로파일(ACC, VEL) =====
V_Profile = [
    [5, 10], [20, 30], [8, 20],
    [10, 35], [8, 15], [17, 30],
]
# ===== 보조(다른 동작에서 사용할) 속도/가속도 프로파일 =====
V_Profile2 = [
    [8, 15], [20, 15], [10, 10],
    [12, 17], [10, 7], [15, 25],
]

Home_profile  = [1510, 735, 3382, 2578, 3636, 2569]
NPOS          = [2024, 1400, 2817, 2402, 3310, 2281]
POS1          = [1974, 2000, 2250, 2591, 3000, 2312]
POS2          = [1870, 1950, 2580, 2400, 2830, 1750]
POS3          = [1697, 1850, 2700, 2470, 2725, 1721]
POS4          = [2100, 2050, 2530, 2250, 3040, 1815]
POS5          = [1858, 1725, 2900, 2404, 2836, 1726]
MPOS          = [2279, 1164, 2843, 3295, 3353, 3200]


POSE_MAP   = {1: POS1, 2: POS2, 3: POS3, 4: POS4, 5: POS5}
FOOD_ITEMS = {1: "Rice", 2: "Soup", 3: "Kimchi", 4: "Meat", 5: "Water"}

portHandler = PortHandler(DEVICENAME)
packetHandler = PacketHandler(PROTOCOL_VERSION)

# ----------------- 모터 제어 유틸 -----------------
def init_joint_POS(_Profile):
    for _ID in range(1, DXL_MAX_ID + 1):
        packetHandler.write4ByteTxRx(portHandler, _ID, ADDR_MIN_POSITION, _Profile[_ID-1][0])
        packetHandler.write4ByteTxRx(portHandler, _ID, ADDR_MAX_POSITION, _Profile[_ID-1][1])

def init_joint_VELACC(_Profile):
    for _ID in range(1, DXL_MAX_ID + 1):
        packetHandler.write4ByteTxRx(portHandler, _ID, ADDR_PROFILE_ACC, _Profile[_ID-1][0])
        packetHandler.write4ByteTxRx(portHandler, _ID, ADDR_PROFILE_VEL, _Profile[_ID-1][1])

def set_joint_VELACC(joints, profile):
    for i, _ID in enumerate(joints):
        packetHandler.write4ByteTxRx(portHandler, _ID, ADDR_PROFILE_ACC, profile[i][0])
        packetHandler.write4ByteTxRx(portHandler, _ID, ADDR_PROFILE_VEL, profile[i][1])

def get_current_joint_positions(print_flag=False):
    pos = []
    for _ID in range(1, DXL_MAX_ID+1):
        v, r, e = packetHandler.read4ByteTxRx(portHandler, _ID, ADDR_PRESENT_POSITION)
        if r==COMM_SUCCESS and e==0: pos.append(v)
    if print_flag: print("[CJP]", pos)
    return pos

def set_joint_torque(on=True):
    val = TORQUE_ENABLE if on else TORQUE_DISABLE
    for _ID in range(1, DXL_MAX_ID+1):
        packetHandler.write1ByteTxRx(portHandler, _ID, ADDR_TORQUE_ENABLE, val)

def set_joint_positions(goal):
    for _ID in [6,5,4,2,3,1]:
        packetHandler.write4ByteTxRx(portHandler, _ID, ADDR_GOAL_POSITION, goal[_ID-1])

def set_joint_positions2(joints, positions):
    for i, _ID in enumerate(joints):
        packetHandler.write4ByteTxRx(portHandler, _ID, ADDR_GOAL_POSITION, positions[i])

def joint_moving_status2():
    for _ID in range(1, DXL_MAX_ID+1):
        moving, r, e = packetHandler.read1ByteTxRx(portHandler, _ID, DXL_MOVING)
        if moving != 0: return True
    return False

def wait_until_stop():
    while joint_moving_status2(): time.sleep(0.05)

# ---- 프로파일 매핑(기본 프로파일 = V_Profile) ----
def _profile_map_from_list(plist):
    # plist: [[acc, vel], ...] 길이 6
    return { i+1: (plist[i][0], plist[i][1]) for i in range(6) }

DEFAULT_VELACC = _profile_map_from_list(V_Profile)

def restore_default_velacc(joints=(1,2,3,4,5,6)):
    """기본 프로파일(V_Profile)로 복구"""
    prof = [[DEFAULT_VELACC[j][0], DEFAULT_VELACC[j][1]] for j in joints]
    set_joint_VELACC(list(joints), prof)

# ============================================================
#                 SNAP 공통 유틸(스냅 전용 50/50)
# ============================================================
def _clamp_pos(jid: int, val: int) -> int:
    jmin, jmax = DXL_Profile[jid-1]
    return max(jmin, min(val, jmax))

def _apply_50(joints):
    set_joint_VELACC(joints, [[50,50] for _ in joints])  # ← SNAP은 항상 50/50

def move_rel(deltas: dict, dwell: float = 0.5, print_after: bool = True):
    if not deltas: return
    joints = list(deltas.keys())
    _apply_50(joints)  # SNAP 전용

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

# ----------------- 모션 프리미티브 -----------------
def move_and_wait(pose, voice=None, use_profile2=False, restore_profile=True, label=None):
    """
    - SNAP 이외의 모든 동작은 여기로 이동하며 V_Profile / V_Profile2를 따른다.
    - use_profile2=True이면 이동 전에 V_Profile2를 적용, 끝나고 restore_profile=True면 V_Profile로 복구.
    """
    if label: print(f"[MOVE] -> {label}")
    if voice: speak_then_wait(voice, 0.0)
    set_joint_torque(True); time.sleep(0.10)
    if use_profile2:
        set_joint_VELACC([1,2,3,4,5,6], V_Profile2)  # 보조 프로파일
    else:
        set_joint_VELACC([1,2,3,4,5,6], V_Profile)   # 기본 프로파일(명시 적용)
    set_joint_positions(pose)
    time.sleep(0.30); wait_until_stop(); time.sleep(0.10)
    if restore_profile:
        init_joint_VELACC(V_Profile)  # 기본 프로파일 유지


# ============================================================
#                         SNAP들 (스냅은 50/50 고정)
# ============================================================
def snap1_1():
    print("[SNAP] snap1_1 start")
    move_rel({5: -900}, dwell=0.5)
    move_rel({6: -1300},  dwell=0.5)
    move_rel({4: -300}, dwell=0.5)
    move_rel({3: +150},  dwell=0.5)
    move_rel({5: +450}, dwell=0.5)
    move_rel({1: +20}, dwell=0.5)
    move_rel({2: +75}, dwell=0.5)
    move_rel({6: +900}, dwell=0.5)
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
    move_rel({6: +900}, dwell=0.5)
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
    move_rel({1: -50}, dwell=0.5)   # Step 5
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
    move_rel({1: +80}, dwell=0.5)
    move_rel({5: -500}, dwell=0.5)
    move_rel({6: -1100}, dwell=0.5)
    move_rel({4: -400},  dwell=0.5)
    move_rel({2: -100}, dwell=0.5)
    move_rel({3: +200}, dwell=0.5)
    move_rel({5: +400}, dwell=0.5)
    move_rel({6: +900}, dwell=0.5)
    restore_default_velacc()
    print("[SNAP] snap4_1 done.")

# SNAP 4_2 (4번 음식 중간(완료) 전용)
def snap4_2():
    print("[SNAP] snap4_2 start")
    move_rel({2:-150}, dwell=0.5)
    move_rel({5:-500}, dwell=0.5)
    move_rel({6:-1200}, dwell=0.5)
    move_rel({4:-340},  dwell=0.5)
    move_rel({1:+110}, dwell=0.5)
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
    move_rel({5:-900}, dwell=0.5)
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
    move_rel({5:-900}, dwell=0.5)
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
    move_rel({5:-900}, dwell=0.5)
    move_rel({2:-210}, dwell=0.5)
    move_rel({3:+331}, dwell=0.5)
    move_rel({4:-225}, dwell=0.5)
    move_rel({6:-900}, dwell=0.5)
    move_rel({5:+400}, dwell=0.5)
    move_rel({6:+900}, dwell=0.5)
    restore_default_velacc()
    print("[SNAP] snap5_3 done.")
# ----------------- GPIO 헬퍼 -----------------
PIN_BLUE   = 5
PIN_TORQUE = 27

def gpio_setup():
    if not HAS_GPIO: return
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(PIN_BLUE,   GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
    GPIO.setup(PIN_TORQUE, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    print(f"[GPIO] Configured: BLUE={PIN_BLUE}(PUD_DOWN), TORQUE={PIN_TORQUE}(PUD_UP)")

def blue_pressed():
    return GPIO.input(PIN_BLUE) == 1 if HAS_GPIO else False

def torque_btn_pressed():
    return GPIO.input(PIN_TORQUE) == 0 if HAS_GPIO else False

# ----------------- 메뉴 & 파이프라인 -----------------
IDLE_AUTO_SECONDS = 3.0

def print_menu(choice):
    print("\n========= MENU =========")
    print("키 '1' 음식 순환 / 3초 무입력 자동실행 / '2' 물(로봇 비동작)")
    print(f"현재 선택: POS{choice} → {FOOD_ITEMS[choice]}")
    print("[o] Torque ON / [p] Torque OFF / [h] HOME / [ESC] Exit")
    print("========================\n")

# ===================== SNAP 매핑/순환 =====================
# - Food 1,2,3: L/R 2개 스냅을 번갈아 실행 (L -> R -> L -> ...)
# - Food 4,5  : L/M/R 3개 스냅을 순환 실행 (L -> M -> R -> ...)

SNAP_BY_FOOD = {
    1: { 'L': snap1_1, 'R': snap1_2 },
    2: { 'L': snap2_1, 'R': snap2_2 },
    3: { 'L': snap3_1, 'R': snap3_2 },   # ← 여기 L을 None -> snap3 로 변경
    4: { 'L': snap4_1, 'M': snap4_2, 'R': snap4_3 },
    5: { 'L': snap5_1, 'M': snap5_2, 'R': snap5_3 },
}


# 좌/우 또는 좌/중/우 순환 순서
_snap_order_map = {
    1: ['L','R'],
    2: ['L','R'],
    3: ['L','R'],
    4: ['L','M','R'],
    5: ['L','M','R'],
}

# 순환 인덱스 (초기 0)
snap_sequence_index = {1:0, 2:0, 3:0, 4:0, 5:0}


def execute_pipeline_for_choice(choice):
    """
    선택된 음식(choice)에 대한 자동 실행 파이프라인:
      선택 포즈 -> SNAP(사이드 순환) -> NPOS -> MPOS -> NPOS
    - SNAP: 1,2,3번은 L/R 번갈아 실행, 4,5번은 L→M→R 순환
    - SNAP 내부는 ACC/VEL=50/50 고정(각 snap 함수/유틸에서 처리)
    - 그 외 이동은 V_Profile / V_Profile2 규칙을 따름
    """
    def interrupted():
        return blue_pressed()

    food = FOOD_ITEMS[choice]
    pose = POSE_MAP[choice]
    print(f"[AUTO/EXEC] POS{choice} ({food}) pipeline start.")

    # 1) 선택 포즈로 진입 (보조 프로파일 사용 가능)
    if interrupted():
        return "INTERRUPT"
    move_and_wait(
        pose,
        voice=SND_MOVE_SELECTED,
        use_profile2=True,          # 필요 시 보조 프로파일로 진입
        restore_profile=True,
        label=f"POS{choice}"
    )
    time.sleep(0.3)
    if interrupted():
        return "INTERRUPT"

    # 2) SNAP 실행(사이드 순환: 1,2,3은 L/R, 4,5는 L/M/R)
    global snap_sequence_index
    order = _snap_order_map.get(choice, [])
    if not order:
        print(f"[SNAP] Skip - no snap order for Food{choice}")
    else:
        idx = snap_sequence_index[choice]
        side = order[idx]                                   # 이번에 실행할 사이드
        snap_sequence_index[choice] = (idx + 1) % len(order)

        snap_fn = SNAP_BY_FOOD[choice].get(side)            # ★ 예외분기 없이 매핑만 사용
        if snap_fn is None:
            valid = "/".join(SNAP_BY_FOOD[choice].keys())
            print(f"[SNAP] No function for Food{choice} side {side}. Valid: {valid}")
        else:
            print(f"[SNAP SEQ] Food{choice} {side} -> {snap_fn.__name__}")
            snap_fn()                                       # 내부에서 50/50 적용 및 복구 처리

    # 3) 속도/가속 기본값 복구(안전)
    restore_default_velacc()
    time.sleep(0.2)

    # 4) NPOS → MPOS → NPOS (비-SNAP: V_Profile/V_Profile2 규칙 적용)
    if interrupted():
        return "INTERRUPT"
    move_and_wait(NPOS, voice=SND_MOVE_NEUTRAL, label="Neutral (NPOS)", restore_profile=True)
    time.sleep(0.2)
    if interrupted():
        return "INTERRUPT"
    move_and_wait(MPOS, voice=SND_MOVE_SPECIFIC, label="Specific (MPOS)", restore_profile=True)
    time.sleep(8.0)
    if interrupted():
        return "INTERRUPT"
    move_and_wait(NPOS, voice=SND_MOVE_NEUTRAL, label="Neutral (NPOS)", restore_profile=True)
    time.sleep(0.2)

    play_audio(SND_PIPELINE_DONE)
    print("[EXEC] Pipeline done.")
    return "DONE"


# ----------------- 설정 모드 -----------------
def enter_setting_mode():
    global MPOS
    print("[SETTING] Entering setting mode...")

    # 설정 진입/복귀도 V_Profile 사용
    move_and_wait(NPOS, voice=SND_MOVE_NEUTRAL, label="Neutral (NPOS)")
    speak_then_wait(SND_SETTING_GUIDE, 0.0)

    print("[SETTING] You can press TORQUE(27) to set MPOS. Keep BLUE pressed.")
    prev_torque_pressed = torque_btn_pressed()
    set_joint_torque(False if prev_torque_pressed else True)

    while True:
        torque_now = torque_btn_pressed()

        if torque_now and not prev_torque_pressed:
            set_joint_torque(False)
            print("[SETTING] TORQUE27 pressed → Torque OFF")
            speak_then_wait(SND_SETTING_UP, 0.0)

        if prev_torque_pressed and not torque_now:
            set_joint_torque(True)
            MPOS = get_current_joint_positions(False)
            print(f"[SETTING] TORQUE27 released → Torque ON, MPOS saved: {MPOS}")
            speak_then_wait(SND_SETTING_DONE, 0.0)

        prev_torque_pressed = torque_now

        if not blue_pressed():
            time.sleep(0.05)
            print("[SETTING] BLUE released → Go to Neutral and return to menu.")
            move_and_wait(NPOS, voice=SND_MOVE_NEUTRAL, label="Neutral (NPOS)")
            time.sleep(0.1)
            break

        time.sleep(0.02)

# ----------------- MAIN -----------------
def main():
    set_system_volume_percent(90)
    gpio_setup()

    if not portHandler.openPort():
        print("Failed to open port"); sys.exit(1)
    if not portHandler.setBaudRate(BAUDRATE):
        print("Failed to set baudrate"); sys.exit(1)
    print("Port open OK")

    set_joint_torque(False)
    init_joint_POS(DXL_Profile)
    init_joint_VELACC(V_Profile)  # ★ 기본 프로파일로 초기화
    time.sleep(0.10)
    set_joint_torque(True)
    time.sleep(0.10)

    speak_then_wait(SND_START, 0.0)
    speak_then_wait(SND_PRESS_BLUE, 0.0)

    print("[WAIT] Press BLUE (GPIO5=1) to enter setting mode.")
    last = blue_pressed()
    while True:
        cur = blue_pressed()
        if (not last) and cur:
            enter_setting_mode()
            break
        last = cur
        time.sleep(0.05)

    choice = 5
    print_menu(choice)
    speak_then_wait(SND_MENU_SELECT, 0.0)

    ever_selected = False
    timer_active  = False
    last_input_ts = None
    from_setting  = False

    blue_prev = blue_pressed()

    # ★ 추가: 같은 음식 연속 선택 감지용 상태
    last_run_choice = None
    same_choice_count = 0

    while True:
        now = time.time()

        # 설정 모드 재진입
        cur = blue_pressed()
        if (not blue_prev) and cur:
            enter_setting_mode()
            print_menu(choice)
            speak_then_wait(SND_MENU_SELECT, 0.0)
            from_setting  = True
            timer_active  = False
            last_input_ts = None
            last_run_choice = None
            same_choice_count = 0
            while blue_pressed():
                time.sleep(0.03)
            blue_prev = False
            continue
        else:
            blue_prev = cur
        # 자동 실행
        if timer_active and (last_input_ts is not None) and (now - last_input_ts >= IDLE_AUTO_SECONDS):
            # ★ 추가: 같은 음식 연속 선택 감지
            if last_run_choice == choice:
                same_choice_count += 1
            else:
                same_choice_count = 1
                last_run_choice = choice

            # ★ 추가: 2회째 이상이면 경고음 먼저 재생 (이후 로봇은 그대로 작동)
            if same_choice_count >= 2:
                speak_then_wait(SND_CHANGE_FOOD, 0.0)

            execute_pipeline_for_choice(choice)
            print_menu(choice)
            speak_then_wait(SND_MENU_SELECT, 0.0)
            ever_selected = True
            timer_active  = True
            last_input_ts = time.time()
            continue


        key = get_key(timeout=0.1)
        if key is None:
            continue

        if key == chr(0x1b):
            print("[EXIT] Homing...")
            set_joint_torque(True); time.sleep(0.10)
            # 홈 이동도 기본 프로파일로
            init_joint_VELACC(V_Profile)
            set_joint_positions(Home_profile)
            time.sleep(1)
            while joint_moving_status2():
                get_current_joint_positions(False)
                continue
            time.sleep(3)
            set_joint_torque(False); time.sleep(0.05)
            portHandler.closePort()
            stop_audio()
            print("Bye.")
            break

        elif key == 'h':
            move_and_wait(Home_profile, voice=None, label="HOME")
            print_menu(choice)
            speak_then_wait(SND_MENU_SELECT, 0.0)
            if from_setting:
                timer_active = False
                last_input_ts = None
            elif ever_selected:
                timer_active = True
                last_input_ts = time.time()

        elif key == '1':
            choice = 1 + (choice % 5)
            # ★ 변경 감지 시 연속카운터 리셋
            last_run_choice = None
            same_choice_count = 0
            food = FOOD_ITEMS[choice]
            print_menu(choice)
            print(f"[SELECT] POS{choice} → {food}")
            snd = SND_FOODS.get(choice)
            if snd: speak_then_wait(snd, 0.0)
            ever_selected = True
            timer_active  = True
            last_input_ts = time.time()
            from_setting  = False

        elif key == '2':
            print("[WATER] 물 동작 (로봇 비동작).")
            last_run_choice = None
            same_choice_count = 0
            speak_then_wait(SND_WATER_MENU, 0.0)
            speak_then_wait(SND_WATER_ACTION, 0.0)
            print_menu(choice)
            speak_then_wait(SND_MENU_SELECT, 0.0)
            if from_setting:
                timer_active = False
                last_input_ts = None
            elif ever_selected:
                timer_active  = True
                last_input_ts = time.time()

        elif key == 'o':
            set_joint_torque(True)
            print("[Torque Enabled]")
            if from_setting:
                timer_active = False
                last_input_ts = None
            elif ever_selected:
                timer_active  = True
                last_input_ts = time.time()

        elif key == 'p':
            set_joint_torque(False)
            print("[Torque Disabled]")
            if from_setting:
                timer_active = False
                last_input_ts = None
            elif ever_selected:
                timer_active  = True
                last_input_ts = time.time()

        else:
            print(f"[INFO] Unknown key: {repr(key)}")
            if from_setting:
                timer_active = False
                last_input_ts = None
            elif ever_selected:
                timer_active  = True
                last_input_ts = time.time()

# ----------------- Entry -----------------
if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        try:
            print("\n[CTRL+C] Homing then shutdown...")
            set_joint_torque(True); time.sleep(0.10)
            init_joint_VELACC(V_Profile)
            set_joint_positions(Home_profile)
            time.sleep(1)
            while joint_moving_status2():
                get_current_joint_positions(False)
            time.sleep(3)
            set_joint_torque(False); time.sleep(0.05)
            portHandler.closePort()
            stop_audio()
        except Exception:
            pass
    finally:
        try:
            GPIO.cleanup()
        except Exception:
            pass
        try:
            pygame.mixer.quit()
        except Exception:
            pass
