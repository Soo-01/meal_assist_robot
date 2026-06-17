#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import cv2
import json
import time
import atexit
import signal
import socket
import threading
import requests
import subprocess
from urllib.parse import urlparse
from pyzbar import pyzbar
import subprocess


# ───────────────────────────────────────────────────────────────────
# (0) 환경/장치 설정
# ───────────────────────────────────────────────────────────────────
def detect_audio_devices():
    """
    aplay -l, arecord -l 결과를 보고 'USB' 단어가 들어간 장치를 자동으로 찾아냄.
    없으면 default를 반환.
    """
    aplay_dev = "default"
    arecord_dev = "default"

    try:
        # aplay 쪽 검색 (재생)
        out = subprocess.check_output(["aplay", "-l"], text=True, stderr=subprocess.DEVNULL)
        for line in out.splitlines():
            if "USB" in line or "PnP" in line:
                parts = line.strip().split()
                # card N: …, device M:
                card_idx = line.split("card ")[1].split(":")[0]
                dev_idx = line.split("device ")[1].split(":")[0]
                aplay_dev = f"plughw:{card_idx},{dev_idx}"
                break

        # arecord 쪽 검색 (녹음)
        out = subprocess.check_output(["arecord", "-l"], text=True, stderr=subprocess.DEVNULL)
        for line in out.splitlines():
            if "USB" in line or "PnP" in line:
                card_idx = line.split("card ")[1].split(":")[0]
                dev_idx = line.split("device ")[1].split(":")[0]
                arecord_dev = f"plughw:{card_idx},{dev_idx}"
                break

    except Exception:
        pass

    return arecord_dev, aplay_dev


MIC_DEVICE, APLAY_DEVICE = detect_audio_devices()
print(f"[AUTO-DETECT] MIC_DEVICE={MIC_DEVICE}, APLAY_DEVICE={APLAY_DEVICE}")
SHOW_WINDOW = True

# Ollama
OLLAMA_URL_BASE = os.environ.get("OLLAMA_URL", "http://localhost:11434/api")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "CLOVA")
AUTOSTART_OLLAMA = int(os.environ.get("AUTOSTART_OLLAMA", "1"))

# 바코드 → 메뉴 매핑
MENU_MAP = {
    "12345": "콩자반",
    "12355": "소세지",
    "12365": "메추리알",
    "12375": "어묵",
    "12385": "밥",
}

# ───────────────────────────────────────────────────────────────────
# (1) 전역 상태/패턴
# ───────────────────────────────────────────────────────────────────
STOP_EVENT    = threading.Event()  # 재생 중단(“그만” 등)
RUN_EVENT     = threading.Event()  # 리스너 루프 on/off
PROCEED_EVENT = threading.Event()  # “스캔” 계열 감지 시 세트

CURRENT_PROCS = {"mimic": None, "aplay": None}
AREC_PROC = None
OLLAMA_PROC = None

STOP_PAT    = re.compile(r"(그만|멈춰|스탑|stop)", re.IGNORECASE)
# “스캔”만 말해도 진행, “스캔 완료/스캔이 다 끝났습니다” 등도 포함
PROCEED_PAT = re.compile(r"(스캔(\s*끝| 완료)?|스캔이\s*다\s*끝났[습니다다]?)", re.IGNORECASE)

# ───────────────────────────────────────────────────────────────────
# (2) 유틸: 바운딩 박스/텍스트
# ───────────────────────────────────────────────────────────────────
def draw_box_and_text(frame, barcode, text, color=(0, 255, 0)):
    (x, y, w, h) = barcode.rect
    cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
    cv2.putText(frame, text, (x, max(25, y - 10)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2, cv2.LINE_AA)

# ───────────────────────────────────────────────────────────────────
# (3) 프로세스 종료 유틸
# ───────────────────────────────────────────────────────────────────
def stop_speaking():
    STOP_EVENT.set()
    for key in ("aplay", "mimic"):
        p = CURRENT_PROCS.get(key)
        if p and p.poll() is None:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGTERM)
            except Exception:
                pass
    time.sleep(0.2)
    for key in ("aplay", "mimic"):
        p = CURRENT_PROCS.get(key)
        if p and p.poll() is None:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            except Exception:
                pass

def stop_arecord():
    global AREC_PROC
    try:
        if AREC_PROC and AREC_PROC.poll() is None:
            os.killpg(os.getpgid(AREC_PROC.pid), signal.SIGTERM)
            time.sleep(0.2)
            if AREC_PROC.poll() is None:
                os.killpg(os.getpgid(AREC_PROC.pid), signal.SIGKILL)
    except Exception:
        pass
    AREC_PROC = None

def stop_ollama_if_spawned():
    global OLLAMA_PROC
    try:
        if OLLAMA_PROC and OLLAMA_PROC.poll() is None:
            os.killpg(os.getpgid(OLLAMA_PROC.pid), signal.SIGTERM)
            time.sleep(0.5)
            if OLLAMA_PROC.poll() is None:
                os.killpg(os.getpgid(OLLAMA_PROC.pid), signal.SIGKILL)
    except Exception:
        pass
    OLLAMA_PROC = None

# ───────────────────────────────────────────────────────────────────
# (4) Mimic3 → aplay TTS (중간 '그만' 정지)
# ───────────────────────────────────────────────────────────────────
def speak(text: str):
    stop_speaking()
    STOP_EVENT.clear()
    try:
        mimic = subprocess.Popen(
            ["mimic3", "--voice", "ko_KO/kss_low", "--stdout"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            preexec_fn=os.setsid
        )
        CURRENT_PROCS["mimic"] = mimic

        aplay_cmd = ["aplay"]
        if APLAY_DEVICE:
            aplay_cmd += ["-D", APLAY_DEVICE]
        aplay = subprocess.Popen(
            aplay_cmd,
            stdin=mimic.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            preexec_fn=os.setsid
        )
        CURRENT_PROCS["aplay"] = aplay

        mimic.stdin.write(text.encode("utf-8"))
        mimic.stdin.close()

        while True:
            if STOP_EVENT.is_set(): break
            if aplay.poll() is not None: break
            time.sleep(0.05)
    except Exception as e:
        print(f"⚠️ 음성 출력 실패: {e}")
    finally:
        stop_speaking()
        CURRENT_PROCS["mimic"] = None
        CURRENT_PROCS["aplay"] = None
        STOP_EVENT.clear()

# ───────────────────────────────────────────────────────────────────
# (5) arecord + Vosk 키워드 리스너
#     - '그만/멈춰/스탑/stop' → STOP_EVENT, TTS 중지
#     - '스캔' 계열 → PROCEED_EVENT.set() → 스캔 루프 즉시 종료
# ───────────────────────────────────────────────────────────────────
def keyword_listener(model_path: str, rate: int = 16000, device_hw: str | None = None):
    from vosk import Model, KaldiRecognizer
    global AREC_PROC

    try:
        model = Model(model_path)
        rec = KaldiRecognizer(model, rate)
        rec.SetWords(False)

        cmd = ["arecord", "-q", "-f", "S16_LE", "-r", str(rate), "-c", "1"]
        if device_hw:
            cmd += ["-D", device_hw]
        AREC_PROC = subprocess.Popen(cmd, stdout=subprocess.PIPE, preexec_fn=os.setsid)

        with AREC_PROC as proc:
            while RUN_EVENT.is_set():
                chunk = proc.stdout.read(3000)  # 반응 빠르게
                if not chunk:
                    time.sleep(0.01); continue

                if rec.AcceptWaveform(chunk):
                    try:
                        j = json.loads(rec.Result()); text = j.get("text", "")
                        if text:
                            if STOP_PAT.search(text):
                                print("🛑 (final) '그만' 계열:", text)
                                STOP_EVENT.set(); stop_speaking()
                            if PROCEED_PAT.search(text):
                                print("➡️ (final) '스캔' 계열:", text)
                                PROCEED_EVENT.set()
                    except Exception:
                        pass
                else:
                    j = json.loads(rec.PartialResult()); part = j.get("partial", "")
                    if part:
                        if STOP_PAT.search(part):
                            print("🛑 (partial) '그만' 계열:", part)
                            STOP_EVENT.set(); stop_speaking()
                        if PROCEED_PAT.search(part):
                            print("➡️ (partial) '스캔' 계열:", part)
                            PROCEED_EVENT.set()

    except Exception as e:
        print(f"⚠️ 키워드 리스너 오류: {e}")
    finally:
        stop_arecord()

# ───────────────────────────────────────────────────────────────────
# (6) Ollama 데몬 보장 + 웜업
# ───────────────────────────────────────────────────────────────────
def _tcp_ready(host: str, port: int, timeout_s: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except Exception:
        return False

def ensure_ollama_ready_and_warm(model: str, url_base: str, start_timeout: int = 60) -> None:
    parsed = urlparse(url_base)
    host = parsed.hostname or "localhost"
    port = parsed.port or 11434
    global OLLAMA_PROC

    if _tcp_ready(host, port):
        try:
            requests.get(f"{url_base}/tags", timeout=3)
            return
        except Exception:
            pass

    if not AUTOSTART_OLLAMA:
        print("⚠️ AUTOSTART_OLLAMA=0 → 자동 기동 생략")
        return

    try:
        print("▶ ollama serve 시작…")
        OLLAMA_PROC = subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            preexec_fn=os.setsid
        )
    except FileNotFoundError:
        print("❌ 'ollama' 명령을 찾지 못했습니다."); return
    except Exception as e:
        print(f"❌ ollama serve 실행 실패: {e}"); return

    start = time.time()
    while time.time() - start < start_timeout:
        if _tcp_ready(host, port): break
        time.sleep(0.5)
    else:
        print("❌ ollama 포트가 열리지 않음"); return

    try:
        print(f"▶ '{model}' 웜업…")
        payload = {"model": OLLAMA_MODEL, "prompt": prompt, "stream": True}
        with requests.post(f"{url_base}/generate", json=payload, stream=True, timeout=180) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line: continue
                obj = json.loads(line.decode("utf-8"))
                if obj.get("done"): break
        print("✅ Ollama 웜업 완료")
    except Exception as e:
        print(f"⚠️ 웜업 경고(무시 가능): {e}")

# ───────────────────────────────────────────────────────────────────
# (7) Ollama 호출 (안정화 옵션+재시도)
# ───────────────────────────────────────────────────────────────────
def llm_explain(items: list[str]) -> str:
    if not items:
        return "스캔된 항목이 없어 설명할 내용이 없습니다."
    prompt = f"""
    ### 지시사항
    1. 모든 음식 항목은 하나의 문단으로 끝나도록 해줘.
    2. 절대로 특수문자나 LATEX 문법(예: **, $, \\frac 등)을 쓰지마.
    3. 반드시 7줄 이내로 간결하게 정리해줘.

    ### 항목 목록
    {', '.join(items)}
    """

    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": True,
        "options": {"num_ctx": 256, "num_gpu": 8, "num_batch": 16, "num_thread": 4, "temperature": 0.6}
    }
    backoff = 1.5
    last_error = None
    for attempt in range(3):
        try:
            with requests.post(f"{OLLAMA_URL_BASE}/generate", json=payload, stream=True, timeout=180) as r:
                r.raise_for_status()
                chunks = []
                for line in r.iter_lines():
                    if not line: continue
                    obj = json.loads(line.decode("utf-8"))
                    if "response" in obj: chunks.append(obj["response"])
                    if obj.get("done"): break
                text = "".join(chunks).strip()
                return text or "설명을 생성하지 못했습니다."
        except Exception as e:
            last_error = e
            time.sleep(backoff ** attempt)
    return f"Ollama 요청 중 오류가 발생했습니다: {last_error}"

# ───────────────────────────────────────────────────────────────────
# (8) 종료 훅
# ───────────────────────────────────────────────────────────────────
def cleanup_all():
    try: stop_speaking()
    except Exception: pass
    RUN_EVENT.clear()
    stop_arecord()
    try: cv2.destroyAllWindows()
    except Exception: pass
    stop_ollama_if_spawned()

def _handle_signal(signum, frame):
    cleanup_all()

atexit.register(cleanup_all)
signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)

# ───────────────────────────────────────────────────────────────────
# (9) 메인
# ───────────────────────────────────────────────────────────────────
def main():
    print(f"[USE] MIC_DEVICE   = {MIC_DEVICE}")
    print(f"[USE] APLAY_DEVICE = {APLAY_DEVICE}")

    # 0) Ollama 보장 + 웜업
    ensure_ollama_ready_and_warm(OLLAMA_MODEL, OLLAMA_URL_BASE)

    # 1) 키워드 리스너 시작
    RUN_EVENT.set()
    model_path = os.path.expanduser("~/models/vosk-model-small-ko-0.22")
    threading.Thread(target=keyword_listener, args=(model_path, 16000, MIC_DEVICE), daemon=True).start()

    cap = None
    try:
        # 2) 바코드 스캔 루프 (시간 제한 없음! '스캔' 음성 명령이 반드시 필요)
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            print("❌ 카메라를 열 수 없습니다."); return

        print("📸 스캔을 시작합니다. (음성으로 '스캔'이라고 말하면 종료됩니다)")
        detected_codes, known_items, unknown_codes = [], [], []

        while True:
            if PROCEED_EVENT.is_set():   # 🔴 음성으로 '스캔' 계열 감지되어야만 종료
                print("➡️ 음성 명령에 의해 스캔 종료 후 설명 단계로 진행합니다.")
                break

            ret, frame = cap.read()
            if not ret:
                print("❌ 프레임을 가져올 수 없습니다."); break

            for bc in pyzbar.decode(frame):
                data = bc.data.decode("utf-8").strip()
                if len(data) == 5 and data.isdigit():
                    if data not in detected_codes:
                        detected_codes.append(data)
                        label = MENU_MAP.get(data)
                        if label:
                            known_items.append(label)
                            print(f"✅ NEW: {data} ({label})")
                            draw_box_and_text(frame, bc, f"{label}({data})", color=(0, 255, 0))
                        else:
                            unknown_codes.append(data)
                            print(f"✅ NEW: {data} (미등록)")
                            draw_box_and_text(frame, bc, f"미등록({data})", color=(0, 165, 255))
                    else:
                        label = MENU_MAP.get(data, data)
                        draw_box_and_text(frame, bc, f"(dup) {label}", color=(0, 180, 180))
                else:
                    draw_box_and_text(frame, bc, "ignored", color=(0, 0, 255))

            if SHOW_WINDOW:
                cv2.imshow("Barcode Scanner (Ollama+TTS)", frame)
                # 디버깅용 ESC는 유지(완전 금지하고 싶으면 이 블록 제거)
                if cv2.waitKey(1) & 0xFF == 27:
                    print("ESC로 사용자 강제 종료"); break

        # 3) 결과
        print("\n==============================")
        if detected_codes:
            print("인식된 코드:", ", ".join(detected_codes))
            if known_items:   print("인식된 항목:", ", ".join(known_items))
            if unknown_codes: print("미등록 코드:", ", ".join(unknown_codes))
        else:
            print("스캔된 바코드가 없습니다.")
        print("스캔이 끝났습니다.")
        print("==============================\n")

        # 4) LLM 설명 + TTS
        if known_items:
            explanation = llm_explain(known_items)
            print("\n[LLM 설명]\n", explanation, "\n")
            speak(f"스캔이 끝났습니다. {explanation}")
        elif detected_codes:
            speak("스캔이 끝났습니다. 미등록 코드만 인식되었습니다.")
        else:
            speak("스캔이 끝났습니다. 인식된 바코드가 없습니다.")

    finally:
        try:
            if cap: cap.release()
        except Exception:
            pass
        cleanup_all()

# ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    main()
