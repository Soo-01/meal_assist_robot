#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Auto_Robot.py - pygame audio, Dynamixel control, full pipeline restored
- Immediate sound playback via pygame.mixer.Sound preload
- Snap motion fully restored (uses set_joint_VELACC, set_joint_positions2, wait_until_stop)
- Pipeline: choice -> snap -> NPOS -> MPOS -> NPOS
- Menu: press '1' to cycle choice, if no input for 3s after last sound finished -> execute
- '2' = Water menu (no robot motion, only voice)
- ESC/CTRL+C homing preserved
"""

import sys, tty, termios, time, select, shutil, subprocess
from pathlib import Path
import pygame
from dynamixel_sdk import *

# -------------------------
# Config / Paths
# -------------------------
BASE_DIR  = Path(__file__).resolve().parent
AUDIO_DIR = BASE_DIR / "audio_file"   # place your mp3/wav files here

# -------------------------
# Helper: non-blocking keyboard read
# -------------------------
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

# -------------------------
# System volume helper (attempt pactl -> amixer)
# -------------------------
def set_system_volume_percent(percent: int = 65):
    import subprocess
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
            for ctl in ("Master", "PCM", "Headphone", "Speaker", "DAC"):
                rc = subprocess.run(["amixer", "sset", ctl, f"{percent}%"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
                if rc == 0:
                    print(f"[VOLUME] {percent}% via amixer:{ctl}")
                    return
    except Exception:
        pass
    print("[VOLUME] System volume not changed (no pactl/amixer)")

# -------------------------
# Pygame audio: init + preload
# -------------------------
pygame.mixer.init(frequency=16000)  # sampling rate (works with many files)
pygame.mixer.set_num_channels(8)
# keep internal mixer volume at 1.0; we control system volume externally
pygame.mixer.music.set_volume(1.0)

# Sound cache: Path -> pygame.mixer.Sound
SOUND_CACHE = {}

def preload_sound(path: Path):
    """Load a sound into SOUND_CACHE if not loaded. Returns Sound or None."""
    if not path:
        return None
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
        print(f"[AUDIO] Preload error {p}: {e}")
        return None

def play_sound_obj(snd, block: bool = True):
    """Play a pygame Sound object. If block True, wait until finished."""
    if snd is None:
        return
    ch = snd.play()
    if block:
        while ch.get_busy():
            time.sleep(0.01)

def play_sound_path(path: Path, block: bool = True):
    snd = preload_sound(path)
    play_sound_obj(snd, block=block)

# Backwards-compatible helpers
def play_audio(path: Path):
    play_sound_path(path, block=True)

def start_audio(path: Path):
    play_sound_path(path, block=False)

def stop_audio():
    pygame.mixer.stop()

def speak_then_wait(path: Path, delay_after: float = 0.0):
    """Play the sound fully (block) then wait delay_after seconds."""
    play_audio(path)
    if delay_after > 0:
        time.sleep(delay_after)

# -------------------------
# Sound file variables (use consistent extensions)
# -------------------------
SND_MENU_SELECT     = AUDIO_DIR / "menu_select.mp3"
SND_MOVE_SELECTED   = AUDIO_DIR / "move_selected.mp3"
SND_MOVE_NEUTRAL    = AUDIO_DIR / "move_neutral.mp3"
SND_MOVE_SPECIFIC   = AUDIO_DIR / "move_specific.mp3"
SND_PIPELINE_DONE   = AUDIO_DIR / "pipeline_done.mp3"
SND_WATER_MENU      = AUDIO_DIR / "water_menu.mp3"
SND_WATER_ACTION    = AUDIO_DIR / "water_action.mp3"

START = AUDIO_DIR / "Start.wav"

SND_FOODS = {
    1: AUDIO_DIR / "food1.wav",
    2: AUDIO_DIR / "food2.wav",
    3: AUDIO_DIR / "food3.wav",
    4: AUDIO_DIR / "food4.wav",
    5: AUDIO_DIR / "food5.wav",
}

# Preload all known sounds to minimize latency
_PRELOAD_LIST = [
    SND_MENU_SELECT, SND_MOVE_SELECTED, SND_MOVE_NEUTRAL, SND_MOVE_SPECIFIC,
    SND_PIPELINE_DONE, SND_WATER_MENU, SND_WATER_ACTION,
    *SND_FOODS.values()
]
for p in _PRELOAD_LIST:
    preload_sound(p)

# -------------------------
# Dynamixel config (unchanged)
# -------------------------
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
V_Profile = [
    [5, 10], [20, 30], [8, 20],
    [10, 35], [8, 15], [17, 30],
]
V_Profile2 = [
    [8, 15], [20, 15], [10, 10],
    [12, 17], [10, 7], [15, 25],
]

Home_profile = [1510, 735, 3382, 2578, 3636, 2569]
NPOS         = [2024, 1400, 2817, 2402, 3310, 2281]
POS1         = [2021, 2264, 2333, 1979, 3122, 1606]
POS2         = [1876, 2149, 2557, 2171, 2983, 1739]
POS3         = [1697, 2132, 2634, 2254, 2820, 1721]
POS4         = [2190, 2079, 2623, 1826, 3113, 1424]
POS5         = [1937, 1856, 3070, 1933, 2887, 1591]
MPOS         = [2279, 1164, 2843, 3295, 3353, 3200]

POSE_MAP   = {1: POS1, 2: POS2, 3: POS3, 4: POS4, 5: POS5}
FOOD_ITEMS = {1: "Rice", 2: "Soup", 3: "Kimchi", 4: "Meat", 5: "Water"}

portHandler = PortHandler(DEVICENAME)
packetHandler = PacketHandler(PROTOCOL_VERSION)

# -------------------------
# Low-level helpers (Dynamixel comm)
# -------------------------
def init_joint_POS(_Profile):
    for _ID in range(1, DXL_MAX_ID + 1):
        r,e = packetHandler.write4ByteTxRx(portHandler, _ID, ADDR_MIN_POSITION, _Profile[_ID-1][0])
        if r != COMM_SUCCESS: print("[POS INIT01]", packetHandler.getTxRxResult(r))
        elif e != 0:          print("[POS INIT01]", packetHandler.getRxPacketError(e))
        r,e = packetHandler.write4ByteTxRx(portHandler, _ID, ADDR_MAX_POSITION, _Profile[_ID-1][1])
        if r != COMM_SUCCESS: print("[POS INIT02]", packetHandler.getTxRxResult(r))
        elif e != 0:          print("[POS INIT02]", packetHandler.getRxPacketError(e))

def init_joint_VELACC(_Profile):
    for _ID in range(1, DXL_MAX_ID + 1):
        r,e = packetHandler.write4ByteTxRx(portHandler, _ID, ADDR_PROFILE_ACC, _Profile[_ID-1][0])
        if r != COMM_SUCCESS: print("[VEL INIT01]", packetHandler.getTxRxResult(r))
        elif e != 0:          print("[VEL INIT01]", packetHandler.getRxPacketError(e))
        r,e = packetHandler.write4ByteTxRx(portHandler, _ID, ADDR_PROFILE_VEL, _Profile[_ID-1][1])
        if r != COMM_SUCCESS: print("[VEL INIT02]", packetHandler.getTxRxResult(r))
        elif e != 0:          print("[VEL INIT02]", packetHandler.getRxPacketError(e))

def set_joint_VELACC(joints, profile):
    if not (joints and profile and len(joints) == len(profile)):
        print("[Error] 'joints' and 'profile' mismatch or empty."); return
    for i, _ID in enumerate(joints):
        r,e = packetHandler.write4ByteTxRx(portHandler, _ID, ADDR_PROFILE_ACC, profile[i][0])
        if r != COMM_SUCCESS: print(f"[SET_ACC] ID {_ID}:", packetHandler.getTxRxResult(r))
        elif e != 0:          print(f"[SET_ACC] ID {_ID}:", packetHandler.getRxPacketError(e))
        r,e = packetHandler.write4ByteTxRx(portHandler, _ID, ADDR_PROFILE_VEL, profile[i][1])
        if r != COMM_SUCCESS: print(f"[SET_VEL] ID {_ID}:", packetHandler.getTxRxResult(r))
        elif e != 0:          print(f"[SET_VEL] ID {_ID}:", packetHandler.getRxPacketError(e))

def get_current_joint_positions(print_flag=True):
    positions = []
    for _ID in range(1, DXL_MAX_ID + 1):
        pos, r, e = packetHandler.read4ByteTxRx(portHandler, _ID, ADDR_PRESENT_POSITION)
        if r != COMM_SUCCESS: print(packetHandler.getTxRxResult(r))
        elif e != 0:          print(packetHandler.getRxPacketError(e))
        else:                 positions.append(pos)
    if print_flag:
        print(" [CJP] Current Joint Positions:", positions)
    return positions

def set_joint_torque(on=True):
    val = TORQUE_ENABLE if on else TORQUE_DISABLE
    for _ID in range(1, DXL_MAX_ID + 1):
        r,e = packetHandler.write1ByteTxRx(portHandler, _ID, ADDR_TORQUE_ENABLE, val)
        if r != COMM_SUCCESS: print("[Torque]", packetHandler.getTxRxResult(r))
        elif e != 0:          print("[Torque]", packetHandler.getRxPacketError(e))

def set_joint_positions(goal_position):
    global TargetPos
    TargetPos = goal_position[:]
    for _ID in [6, 5, 4, 2, 3, 1]:
        r,e = packetHandler.write4ByteTxRx(portHandler, _ID, ADDR_GOAL_POSITION, goal_position[_ID-1])
        if r != COMM_SUCCESS: print("[SJP] %2d:" % _ID, packetHandler.getTxRxResult(r))
        elif e != 0:          print("[SJP] %2d:" % _ID, packetHandler.getRxPacketError(e))

def set_joint_positions2(joints, positions):
    if not (joints and positions and len(joints) == len(positions)):
        print("[Error] 'joints' and 'positions' mismatch or empty."); return
    for i, _ID in enumerate(joints):
        r,e = packetHandler.write4ByteTxRx(portHandler, _ID, ADDR_GOAL_POSITION, positions[i])
        if r != COMM_SUCCESS: print(f"[SJP2] ID {_ID}:", packetHandler.getTxRxResult(r))
        elif e != 0:          print(f"[SJP2] ID {_ID}:", packetHandler.getRxPacketError(e))

def joint_moving_status2():
    for _ID in range(1, DXL_MAX_ID + 1):
        moving, r, e = packetHandler.read1ByteTxRx(portHandler, _ID, DXL_MOVING)
        if r != COMM_SUCCESS: print("[JMS]", packetHandler.getTxRxResult(r))
        elif e != 0:          print("[JMS]", packetHandler.getRxPacketError(e))
        if moving != 0:
            return True
    return False

def wait_until_stop(poll_delay=0.05):
    while joint_moving_status2():
        time.sleep(poll_delay)

# -------------------------
# Motion helpers (restore Snap)
# -------------------------
def snap():
    print("[SNAP] Fast move")
    set_joint_torque(True)
    time.sleep(0.10)
    set_joint_VELACC([5, 6], [[70, 90], [200, 150]])

    tempPos = get_current_joint_positions(print_flag=False)
    # tempPos indexing: joint ids 1..6 => index 0..5; joint5 index=4, joint6 index=5
    tempPos[4] -= 350   # joint 5
    tempPos[5] -= 700   # joint 6
    set_joint_positions2([5, 6], [tempPos[4], tempPos[5]])
    time.sleep(0.30)
    wait_until_stop()
    time.sleep(0.10)

    print("[SNAP] Return move")
    tempPos[4] += 350
    tempPos[5] += 700
    set_joint_VELACC([5, 6], [[50, 50], [100, 80]])
    set_joint_positions2([5, 6], [tempPos[4], tempPos[5]])
    time.sleep(0.30)
    wait_until_stop()
    time.sleep(0.10)

    set_joint_VELACC([5, 6], [[10, 15], [10, 50]])

# -------------------------
# Motion sequence / pipeline
# -------------------------
def move_and_wait(pose, voice: Path = None, use_profile2=False, restore_profile=True, label=None):
    if label:
        print(f"[MOVE] -> {label}")
    if voice:
        # speak then wait until sound finished (blocking). After that, the main loop resets last_input_ts
        speak_then_wait(voice, delay_after=0.0)
    set_joint_torque(True)
    time.sleep(0.10)
    if use_profile2:
        set_joint_VELACC([1,2,3,4,5,6], V_Profile2)
    set_joint_positions(pose)
    time.sleep(0.30)
    wait_until_stop()
    time.sleep(0.10)
    if restore_profile:
        init_joint_VELACC(V_Profile)

STAGE_PAUSE_SELECTED       = 0.40
STAGE_PAUSE_BEFORE_SNAP    = 0.30
STAGE_PAUSE_AFTER_SNAP     = 0.40
STAGE_PAUSE_AFTER_NEUTRAL1 = 0.40
STAGE_PAUSE_AFTER_SPECIFIC = 0.40
STAGE_PAUSE_AFTER_NEUTRAL2 = 0.40

def execute_pipeline_for_choice(choice: int):
    food = FOOD_ITEMS[choice]
    pose = POSE_MAP[choice]
    print(f"[AUTO/EXEC] POS{choice} ({food}) pipeline start.")

    move_and_wait(pose, voice=SND_MOVE_SELECTED, use_profile2=True, restore_profile=True, label=f"POS{choice} ({food})")
    time.sleep(STAGE_PAUSE_SELECTED)

    time.sleep(STAGE_PAUSE_BEFORE_SNAP)
    print("[EXEC] Snap")
    snap()
    time.sleep(STAGE_PAUSE_AFTER_SNAP)

    move_and_wait(NPOS, voice=SND_MOVE_NEUTRAL, label="Neutral (NPOS)")
    time.sleep(STAGE_PAUSE_AFTER_NEUTRAL1)

    move_and_wait(MPOS, voice=SND_MOVE_SPECIFIC, label="Specific (MPOS)")
    time.sleep(STAGE_PAUSE_AFTER_SPECIFIC)

    move_and_wait(NPOS, voice=SND_MOVE_NEUTRAL, label="Neutral (NPOS)")
    time.sleep(STAGE_PAUSE_AFTER_NEUTRAL2)

    play_audio(SND_PIPELINE_DONE)
    print("[EXEC] Pipeline done. Back to menu.")

# -------------------------
# Menu / main loop
# -------------------------
IDLE_AUTO_SECONDS = 3.0  # after last sound finished, wait 3s to auto-run

def print_menu(current_choice):
    print("\n================ MENU ================")
    print("키 '1'로 음식 선택(순환: 1→2→3→4→5→1…). 3초간 입력 없으면 자동 실행.")
    print("현재 선택:", current_choice, f"(POS{current_choice}) → {FOOD_ITEMS[current_choice]}")
    print("키 '2'는 물 메뉴(로봇 동작 없음).")
    print("-------------------------------------")
    print("[o] Torque ON / [p] Torque OFF / [h] HOME / [ESC] Exit")
    print("======================================\n")

def main():
    # set system volume
    set_system_volume_percent(65)

    # open port
    if not portHandler.openPort():
        print("Failed to open the port"); sys.exit(1)
    if not portHandler.setBaudRate(BAUDRATE):
        print("Failed to change the baudrate"); sys.exit(1)
    print("Succeeded to open the port and change the baudrate")

    # init
    set_joint_torque(False)
    init_joint_POS(DXL_Profile)
    init_joint_VELACC(V_Profile)
    time.sleep(0.10)
    set_joint_torque(True)
    time.sleep(0.10)

    # boot neutral
    print("[BOOT] Moving to Neutral (NPOS) ...")
    move_and_wait(NPOS, voice=SND_MOVE_NEUTRAL, label="NPOS")
    speak_then_wait(START, delay_after=0.0)


    choice = 5
    print_menu(choice)

    # play menu prompt fully then start the idle timer (we treat "sound finished" as reset point)
    speak_then_wait(SND_MENU_SELECT, delay_after=0.0)
    last_input_ts = time.time()

    while True:
        now = time.time()
        if now - last_input_ts >= IDLE_AUTO_SECONDS:
            execute_pipeline_for_choice(choice)
            print_menu(choice)
            speak_then_wait(SND_MENU_SELECT, delay_after=0.0)
            last_input_ts = time.time()
            continue

        key = get_key(timeout=0.1)
        if key is None:
            continue

        # on any keypress, we reset timer after handling the key (but we keep last_input_ts set now so rapid presses don't auto-run)
        last_input_ts = time.time()

        if key == chr(0x1b):  # ESC
            print("[EXIT] Homing...")
            # maintain the original homing routine
            set_joint_torque(True); time.sleep(0.10)
            set_joint_positions(Home_profile)
            time.sleep(1)
            while joint_moving_status2():
                get_current_joint_positions(False)
            time.sleep(3)
            set_joint_torque(False); time.sleep(0.05)
            portHandler.closePort()
            stop_audio()
            break

        elif key == 'h':
            move_and_wait(Home_profile, voice=None, label="HOME")
            print_menu(choice)
            speak_then_wait(SND_MENU_SELECT, delay_after=0.0)
            last_input_ts = time.time()

        elif key == 'o':
            set_joint_torque(True)
            print("[Torque Enabled]")

        elif key == 'p':
            set_joint_torque(False)
            print("[Torque Disabled]")

        elif key == '1':
            choice = 1 + (choice % 5)
            food = FOOD_ITEMS[choice]
            print_menu(choice)
            print(f"[SELECT] POS{choice} → {food}")
            snd = SND_FOODS.get(choice)
            if snd:
                # block until sound finished; after that last_input_ts marks the time from which 3s counts
                speak_then_wait(snd, delay_after=0.0)
            last_input_ts = time.time()

        elif key == '2':
            print("[WATER] 물 동작 (로봇 비동작).")
            speak_then_wait(SND_WATER_MENU, delay_after=0.0)
            speak_then_wait(SND_WATER_ACTION, delay_after=0.0)
            print_menu(choice)
            speak_then_wait(SND_MENU_SELECT, delay_after=0.0)
            last_input_ts = time.time()

        else:
            print(f"[INFO] Unknown key: {repr(key)}")
            last_input_ts = time.time()

# -------------------------
# Entrypoint (with ctrl-c homing)
# -------------------------
if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        try:
            print("\n[CTRL+C] Homing then shutdown...")
            set_joint_torque(True); time.sleep(0.10)
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
            pygame.mixer.quit()
        except Exception:
            pass
