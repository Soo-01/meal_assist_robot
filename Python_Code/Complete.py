#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, sys, json, time, queue, threading, signal, tty, termios, wave, select
from pathlib import Path

# ---- Audio / STT ----
import alsaaudio as aa
from vosk import Model, KaldiRecognizer

# ---- Dynamixel ----
from dynamixel_sdk import PortHandler, PacketHandler

# ====================== User Configuration ======================
BASE_DIR = Path(__file__).resolve().parent

# Audio I/O
CAPTURE_CARD  = "plughw:0,0"
PLAYBACK_CARD = "plughw:0,0"
SAMPLE_RATE   = 16000
PERIOD_SIZE   = 4096

# ALSA mixer control candidates
MIXER_CANDIDATES = ["Master", "PCM", "Headphone", "Speaker", "Digital"]
VOLUME_STEP  = 20
INIT_VOLUME  = 100

# <<< 복구: Vosk wake words >>>
MODEL_DIR    = BASE_DIR.parent / "vosk-model-small-ko-0.22"
WAKE_WORDS   = ["헤이 인디", "헤이", "인디"]

# Audio files
AUDIO_DIR    = BASE_DIR / "audio_file"
HELLO_WAV    = AUDIO_DIR / "Start.wav"
READY_WAV    = AUDIO_DIR / "Back_to_Menu.wav"
END_WAV      = AUDIO_DIR / "End.wav"
MENU_WAVS = {
    1: AUDIO_DIR / "menu1_raise_arm.wav",
    2: AUDIO_DIR / "menu2_location_set.wav",
    3: AUDIO_DIR / "menu3_sound_set.wav",
}
SUB_WAV = {
    "vol_up": AUDIO_DIR / "submenu3_action1.wav",
    "vol_dn": AUDIO_DIR / "submenu3_action2.wav",
}
BAP_SET_WAV   = AUDIO_DIR / "Bap_set.wav"
MOUSE_SET_WAV = AUDIO_DIR / "mouse_set.wav"
BAP_WAV       = AUDIO_DIR / "bap.wav"
MOUSE_WAV     = AUDIO_DIR / "mouse.wav"

# Dynamixel
DEVICENAME           = '/dev/ttyACM0'
PROTOCOL_VERSION = 2.0
BAUDRATE             = 57600
DXL_IDS              = (1, 2)

ADDR_TORQUE_ENABLE        = 64
ADDR_GOAL_POSITION        = 116
ADDR_PRESENT_POSITION     = 132
ADDR_PROFILE_ACCELERATION = 108
ADDR_PROFILE_VELOCITY     = 112
TORQUE_ENABLE             = 1
TORQUE_DISABLE            = 0

PROFILE_ACCEL_VALUE       = 10
PROFILE_VEL_VALUE         = 20

# Angle mapping
ANGLE_MIN = 0
RIGHT_AT_ANGLE_MIN = 4076
LEFT_AT_ANGLE_MIN  = 2551
ANGLE_MAX = 75
RIGHT_AT_ANGLE_MAX = 3431
LEFT_AT_ANGLE_MAX  = 3197

# Defaults
DEFAULT_LOWER = 20
DEFAULT_RAISE = 70
REPEAT_WAIT_SEC = 10

# One-key timings
LONG_PRESS_CONFIRM_SEC = 1.5  # 메뉴 안에서 항목 확정 시간
TEACH_PRINT_DT  = 0.3

# Persistent config path
CONFIG_PATH = BASE_DIR / "motor_angles.json"
# ===============================================================

# =============== Globals ===============
stop_flag = threading.Event()
mixer = None
portHandler = None
packetHandler = None
ANGLE_LOWER = DEFAULT_LOWER
ANGLE_RAISE = DEFAULT_RAISE
# <<< 복구: 음성인식용 큐 및 오디오 재생용 큐 >>>
audio_capture_queue = queue.Queue()
audio_playback_queue = queue.Queue()
pcm_capture = None
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

# ---------------------- Console / keys ----------------------
def _enter_cbreak():
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    tty.setcbreak(fd)
    return fd, old

def _restore_term(fd, old):
    termios.tcsetattr(fd, termios.TCSADRAIN, old)

def read_one_key_nonblock():
    return sys.stdin.read(1) if select.select([sys.stdin], [], [], 0)[0] else None

# ---------------------- ONE-KEY core (short press = next, long press = confirm) ----------------------
# <<< 유지: 새로운 버튼 UI (짧게:이동, 길게:확정) >>>
def one_key_select(title, options, wav_files=None, live_line_fn=None):
    idx = 0
    press_time = 0
    is_pressed = False
    long_press_triggered = False
    
    fd, old = _enter_cbreak()
    try:
        print(f"[{title}] ONE-KEY: short=next, long({LONG_PRESS_CONFIRM_SEC:.1f}s)=confirm")
        _print_menu_line(options, idx)
        
        if wav_files and idx < len(wav_files):
            request_audio_playback(wav_files[idx])

        while not stop_flag.is_set():
            now = time.monotonic()
            ch = read_one_key_nonblock()

            if live_line_fn: live_line_fn()

            if ch is not None:
                if not is_pressed:
                    is_pressed = True
                    long_press_triggered = False
                    press_time = now
            else: # 키에서 손을 뗐을 때
                if is_pressed:
                    is_pressed = False
                    duration = now - press_time
                    if duration < LONG_PRESS_CONFIRM_SEC: # 짧게 누름
                        idx = (idx + 1) % len(options)
                        _print_menu_line(options, idx)
                        if wav_files and idx < len(wav_files):
                            request_audio_playback(wav_files[idx])
            
            if is_pressed and not long_press_triggered:
                duration = now - press_time
                if duration >= LONG_PRESS_CONFIRM_SEC: # 길게 누름
                    long_press_triggered = True
                    print(f"\r> Confirmed: {options[idx]}" + " " * 40)
                    return idx
            
            time.sleep(0.02)
    finally:
        _restore_term(fd, old)


def _print_menu_line(options, idx):
    line = " | ".join([f"[{o}]" if i == idx else o for i, o in enumerate(options)])
    print(f"\r     {line}     ", end="", flush=True)

# ---------------------- Audio I/O ----------------------
# <<< 유지: 안정적인 오디오 재생 큐 시스템 >>>
def audio_player_thread():
    while not stop_flag.is_set():
        try:
            path = audio_playback_queue.get(timeout=0.1)
            if path and path.exists():
                try:
                    wf = wave.open(str(path), 'rb')
                    out_pcm = aa.PCM(type=aa.PCM_PLAYBACK, device=PLAYBACK_CARD, channels=wf.getnchannels(), rate=wf.getframerate(), format=aa.PCM_FORMAT_S16_LE, periodsize=1024)
                    data = wf.readframes(1024)
                    while data:
                        out_pcm.write(data)
                        data = wf.readframes(1024)
                    wf.close()
                    out_pcm.close()
                except Exception as e:
                    print(f"\n[AUDIO] Playback error: {e}")
            audio_playback_queue.task_done()
        except queue.Empty:
            continue

def request_audio_playback(path: Path):
    audio_playback_queue.put(path)

# <<< 복구: 음성인식 관련 함수들 >>>
def alsa_reader():
    """마이크 입력을 받아 오디오 캡처 큐에 넣는 스레드"""
    global pcm_capture
    while not stop_flag.is_set():
        length, data = pcm_capture.read()
        if length:
            audio_capture_queue.put(data)

def init_vosk():
    print("Loading Vosk model …")
    vosk_model = Model(str(MODEL_DIR))
    grammar = json.dumps(WAKE_WORDS, ensure_ascii=False)
    rec = KaldiRecognizer(vosk_model, SAMPLE_RATE, grammar)
    rec.SetWords(False)
    return rec

# ---------------------- Dynamixel & Other helpers ----------------------
def get_mixer():
    for name in MIXER_CANDIDATES:
        try: return aa.Mixer(control=name)
        except Exception: continue
    raise RuntimeError(f"No suitable ALSA mixer found (tried: {MIXER_CANDIDATES})")
def set_volume(percent: int):
    global mixer
    percent = max(0, min(100, percent))
    try:
        mixer.setvolume(percent)
        print(f"[VOLUME] {percent}%")
    except Exception as e: print(f"[VOLUME] Failed: {e}")
def change_volume(delta: int):
    try: cur = mixer.getvolume()[0]
    except Exception: cur = INIT_VOLUME
    newv = max(0, min(100, cur + delta))
    set_volume(newv)
    return newv
def calculate_positions(angle_deg: int):
    angle_deg = max(ANGLE_MIN, min(ANGLE_MAX, angle_deg))
    ratio = (angle_deg - ANGLE_MIN) / (ANGLE_MAX - ANGLE_MIN)
    right_goal = RIGHT_AT_ANGLE_MIN + (RIGHT_AT_ANGLE_MAX - RIGHT_AT_ANGLE_MIN) * ratio
    left_goal  = LEFT_AT_ANGLE_MIN  + (LEFT_AT_ANGLE_MAX  - LEFT_AT_ANGLE_MIN) * ratio
    return int(round(right_goal)), int(round(left_goal))
def setup_dynamixel():
    ph = PortHandler(DEVICENAME)
    pk = PacketHandler(PROTOCOL_VERSION)
    if not ph.openPort(): sys.exit(f"[DXL] Failed to open port: {DEVICENAME}")
    if not ph.setBaudRate(BAUDRATE): sys.exit(f"[DXL] Failed to set baudrate: {BAUDRATE}")
    return ph, pk
def set_motor_profiles(ph, pk):
    for dxl_id in DXL_IDS:
        pk.write4ByteTxRx(ph, dxl_id, ADDR_PROFILE_ACCELERATION, PROFILE_ACCEL_VALUE)
        pk.write4ByteTxRx(ph, dxl_id, ADDR_PROFILE_VELOCITY,     PROFILE_VEL_VALUE)
    print(f"[DXL] Profiles → Accel={PROFILE_ACCEL_VALUE}, Vel={PROFILE_VEL_VALUE}")
def set_torque(enable: bool):
    for dxl_id in DXL_IDS:
        packetHandler.write1ByteTxRx(portHandler, dxl_id, ADDR_TORQUE_ENABLE, TORQUE_ENABLE if enable else TORQUE_DISABLE)
    print(f"[DXL] Torque {'ENABLED' if enable else 'DISABLED'}")
def move_to_angle(angle: int):
    r_goal, l_goal = calculate_positions(angle)
    packetHandler.write4ByteTxRx(portHandler, DXL_IDS[0], ADDR_GOAL_POSITION, r_goal)
    packetHandler.write4ByteTxRx(portHandler, DXL_IDS[1], ADDR_GOAL_POSITION, l_goal)
    print(f"[DXL] Goal → angle {angle} deg | R:{r_goal} L:{l_goal}")
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
def invert_angle_from_pos(right_pos: int, left_pos: int):
    r_ratio = (right_pos - RIGHT_AT_ANGLE_MIN) / (RIGHT_AT_ANGLE_MAX - RIGHT_AT_ANGLE_MIN)
    r_angle = ANGLE_MIN + r_ratio * (ANGLE_MAX - ANGLE_MIN)
    l_ratio = (left_pos - LEFT_AT_ANGLE_MIN) / (LEFT_AT_ANGLE_MAX - LEFT_AT_ANGLE_MIN)
    l_angle = ANGLE_MIN + l_ratio * (ANGLE_MAX - ANGLE_MIN)
    est = (r_angle + l_angle) / 2.0
    return int(round(max(ANGLE_MIN, min(ANGLE_MAX, est))))
def set_goal_to_present():
    pos = read_positions()
    for dxl_id in DXL_IDS:
        packetHandler.write4ByteTxRx(portHandler, dxl_id, ADDR_GOAL_POSITION, pos[dxl_id])
    print("[DXL] Goal set = present (anti-jump).")
def teach_one_angle_confirm_loop(label):
    def live_line():
        ang, pos = estimate_current_angle()
        sys.stdout.write(f"\r   {label} | live angle ≈ {ang:>3} deg  (R={pos.get(DXL_IDS[0],0)}, L={pos.get(DXL_IDS[1],0)})   ")
        sys.stdout.flush()
    idx = one_key_select(title=f"{label}: Choose", options=["Confirm now", "Cancel"], live_line_fn=live_line)
    return idx == 0

# ---------------------- Submenus ----------------------
def submenu_teach():
    start_idx = one_key_select("Angle Teach Mode", ["Start (Torque OFF)", "Back"])
    if start_idx == 1: print("\n[Teach] Back."); return
    print("\n[Teach] Torque DISABLED for manual movement. (CAUTION: Support the arm.)")
    set_torque(False)
    try:
        print("\n[Teach] LOWER: Move arm to your desired LOWER angle.")
        if not teach_one_angle_confirm_loop("LOWER"): print("\n[Teach] Canceled."); return
        request_audio_playback(BAP_SET_WAV)
        lower_ang, _ = estimate_current_angle()
        print("\n[Teach] RAISE: Move arm to your desired RAISE angle.")
        if not teach_one_angle_confirm_loop("RAISE"): print("\n[Teach] Canceled."); return
        request_audio_playback(MOUSE_SET_WAV)
        raise_ang, _ = estimate_current_angle()
        lower, raise_ = min(lower_ang, raise_ang), max(lower_ang, raise_ang)
        global ANGLE_LOWER, ANGLE_RAISE
        ANGLE_LOWER, ANGLE_RAISE = lower, raise_
        save_angles()
        print(f"\n[Teach] Done: LOWER={ANGLE_LOWER} deg, RAISE={ANGLE_RAISE} deg")
    finally:
        try: set_goal_to_present()
        except Exception as e: print(f"[DXL] set_goal_to_present failed: {e}")
        set_torque(True)
        print("[Teach] Torque ENABLED (holding current pose)")

def submenu_repeat():
    print("[Repeat] Starting. Long-press to STOP and return.")
    move_to_angle(ANGLE_LOWER)
    request_audio_playback(BAP_WAV)
    time.sleep(0.5)
    
    fd, old = _enter_cbreak()
    try:
        state_high = False
        press_time, is_pressed, long_press_triggered = 0, False, False
        while not stop_flag.is_set():
            t_end = time.monotonic() + REPEAT_WAIT_SEC
            while time.monotonic() < t_end:
                now = time.monotonic()
                ch = read_one_key_nonblock()
                if ch is not None:
                    if not is_pressed: is_pressed, long_press_triggered, press_time = True, False, now
                else:
                    if is_pressed: is_pressed = False
                if is_pressed and not long_press_triggered and (now - press_time) >= LONG_PRESS_CONFIRM_SEC:
                    print("\n[Repeat] Stop requested → going to LOWER and returning.")
                    move_to_angle(ANGLE_LOWER)
                    request_audio_playback(BAP_WAV)
                    time.sleep(0.5); return
                time.sleep(0.02)
            if not state_high:
                move_to_angle(ANGLE_RAISE); request_audio_playback(MOUSE_WAV)
            else:
                move_to_angle(ANGLE_LOWER); request_audio_playback(BAP_WAV)
            state_high = not state_high
            time.sleep(0.5)
    finally:
        _restore_term(fd, old)

def submenu_sound():
    if mixer is None: print("[Sound] Mixer not available."); return
    sound_menu_options = ["Volume Up (+20%)", "Volume Down (-20%)", "Back"]
    sound_menu_wavs = [SUB_WAV.get("vol_up"), SUB_WAV.get("vol_dn"), READY_WAV]
    idx = one_key_select("Sound Mode", sound_menu_options, wav_files=sound_menu_wavs)
    if idx == 0: change_volume(+VOLUME_STEP)
    elif idx == 1: change_volume(-VOLUME_STEP)
    else: print("[Sound] Back.")

def handle_menu():
    request_audio_playback(HELLO_WAV)
    main_menu_options = ["Angle Teach Mode", "Repeat Mode", "Sound Mode", "Exit"]
    main_menu_wavs = [MENU_WAVS.get(1), MENU_WAVS.get(2), MENU_WAVS.get(3), END_WAV]
    while not stop_flag.is_set():
        idx = one_key_select("Main Menu", main_menu_options, wav_files=main_menu_wavs)
        if idx is None: break # stop_flag is set
        print("")
        if (idx + 1) in MENU_WAVS: request_audio_playback(MENU_WAVS[idx + 1])
        if idx == 0: submenu_teach()
        elif idx == 1: submenu_repeat()
        elif idx == 2: submenu_sound()
        elif idx == 3:
            print("[INFO] Exit requested.")
            request_audio_playback(END_WAV)
            return
        request_audio_playback(READY_WAV)
        print("[INFO] Back to main menu.")

# <<< 복구: Wake Worker 스레드 >>>
def wake_worker(rec: KaldiRecognizer):
    last_trigger = 0.0
    DETECTION_GAP = 3.0
    print(f"Listening for wake words {WAKE_WORDS}… (Ctrl-C to quit)")
    while not stop_flag.is_set():
        try:
            data = audio_capture_queue.get(timeout=0.1)
            if rec.AcceptWaveform(data):
                res = json.loads(rec.Result())
                txt = res.get("text", "").strip()
                if any(w.replace(" ", "") in txt.replace(" ", "") for w in WAKE_WORDS):
                    now = time.time()
                    if now - last_trigger >= DETECTION_GAP:
                        print(f"[WAKE] '{txt}' detected → Entering menu")
                        handle_menu()
                        last_trigger = now
                    rec.Reset()
        except queue.Empty:
            continue

# ---------------------- Graceful Shutdown ----------------------
def _graceful_shutdown(*_):
    stop_flag.set()
signal.signal(signal.SIGINT, _graceful_shutdown)
signal.signal(signal.SIGTERM, _graceful_shutdown)

# ---------------------- Main ----------------------
def main():
    global pcm_capture, mixer, portHandler, packetHandler, ANGLE_LOWER, ANGLE_RAISE
    load_angles()
    portHandler, packetHandler = setup_dynamixel()
    try:
        set_motor_profiles(portHandler, packetHandler)
        r_goal, l_goal = calculate_positions(ANGLE_LOWER)
        packetHandler.write4ByteTxRx(portHandler, DXL_IDS[0], ADDR_GOAL_POSITION, r_goal)
        packetHandler.write4ByteTxRx(portHandler, DXL_IDS[1], ADDR_GOAL_POSITION, l_goal)
        set_torque(True)
        print("[DXL] Torque enabled (goal preloaded to LOWER).")
        time.sleep(0.1)
        try:
            mixer = get_mixer()
            set_volume(INIT_VOLUME)
        except Exception as e: print(f"[VOLUME] Mixer init failed: {e}")

        # <<< 복구: 음성인식 및 오디오 스레드 설정 및 시작 >>>
        pcm_capture = aa.PCM(type=aa.PCM_CAPTURE, device=CAPTURE_CARD, channels=1, rate=SAMPLE_RATE, format=aa.PCM_FORMAT_S16_LE, periodsize=PERIOD_SIZE)
        rec = init_vosk()
        
        # 스레드 시작
        threading.Thread(target=audio_player_thread, daemon=True).start()
        threading.Thread(target=alsa_reader, daemon=True).start()
        threading.Thread(target=wake_worker, args=(rec,), daemon=True).start()
        
        # 메인 스레드는 대기
        while not stop_flag.is_set():
            time.sleep(0.2)

    finally:
        stop_flag.set()
        try: set_torque(False)
        except Exception: pass
        try: portHandler.closePort()
        except Exception: pass
        if pcm_capture: pcm_capture.close()
        print("[DXL] Torque disabled, port closed.")
        print("[INFO] Stopped.")

if __name__ == "__main__":
    main()