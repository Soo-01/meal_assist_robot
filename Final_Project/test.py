import os
import subprocess
import pygame

def init_audio():
    """
    USB 오디오 장치를 자동으로 탐색 후 pygame 오디오 출력 초기화
    """
    # 1. ALSA 장치 목록에서 USB 오디오 장치 탐색
    try:
        result = subprocess.run(["aplay", "-l"], capture_output=True, text=True)
        lines = result.stdout.strip().splitlines()
        usb_dev = None
        for line in lines:
            if "USB" in line or "Audio" in line or "PnP" in line:
                # 예: 'card 1: Device [USB PnP Sound Device], device 0: USB Audio [USB Audio]'
                parts = line.split(":")
                if len(parts) >= 3 and "card" in parts[0]:
                    card_part = parts[0].split()
                    card_num = card_part[1]
                    # device 번호 추출
                    dev_num = line.split("device")[1].split(",")[0].strip()
                    usb_dev = f"plughw:{card_num},{dev_num}"
                    print(f"[INFO] USB 오디오 장치 자동 탐색됨: {usb_dev}")
                    break

        if usb_dev:
            os.environ["SDL_AUDIODRIVER"] = "alsa"
            os.environ["AUDIODEV"] = usb_dev
        else:
            # USB 오디오 장치 없음 → PulseAudio 시도
            print("[WARN] USB 오디오 장치 미탐색 → PulseAudio로 시도")
            os.environ["SDL_AUDIODRIVER"] = "pulse"

    except Exception as e:
        print(f"[WARN] ALSA 장치 탐색 실패: {e}")
        os.environ["SDL_AUDIODRIVER"] = "pulse"

    # 2. pygame mixer 초기화 시도
    try:
        pygame.mixer.init()
        print("[DEBUG] pygame mixer 초기화 완료:", pygame.mixer.get_init())
    except Exception as e:
        print(f"[ERROR] pygame.mixer.init() 실패 → fallback to dummy driver ({e})")
        os.environ["SDL_AUDIODRIVER"] = "dummy"
        pygame.mixer.init()
