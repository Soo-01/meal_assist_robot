#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import cv2
import time
import numpy as np
import mediapipe as mp
from collections import deque
import pygame
import os
import subprocess

# ========================== 설정 ==========================
# 카메라
CAM_INDEX = 0
FRAME_W, FRAME_H, FPS = 1280, 720, 30

# 입 벌림 판정
MOUTH_OPEN_THR = 0.15
MOUTH_CLOSE_THR = 0.05
NO_CHEW_WAIT = 3.0  # 입 닫힘 유지 시간

# 음성 출력
PROMPT_MP3 = "/home/bimsrl/Final_Project/prompt_chew.mp3"   # "음식을 먹어주세요"
WARN_MP3 = "/home/bimsrl/Final_Project/warn_too_close.mp3"
PROMPT_COOLDOWN = 5.0
WARN_COOLDOWN = 3.0

# 안전 원
USE_SAFETY_CIRCLE = True
SAFETY_CIRCLE_CENTER = [FRAME_W // 2, FRAME_H // 2]
SAFETY_CIRCLE_RADIUS = 180

SHOW_WINDOW = True

# ======================== MediaPipe =======================
mp_face = mp.solutions.face_mesh
mp_draw = mp.solutions.drawing_utils
mp_style = mp.solutions.drawing_styles

face_mesh = mp_face.FaceMesh(
    static_image_mode=False,
    max_num_faces=1,
    refine_landmarks=True,
    min_detection_confidence=0.4,
    min_tracking_confidence=0.4
)

UPPER_LIP = 13; LOWER_LIP = 14; NOSE_TIP = 1
LEFT_EAR = 234; RIGHT_EAR = 454

# ======================== 유틸 함수 =======================
def init_camera():
    cap = cv2.VideoCapture(CAM_INDEX, cv2.CAP_V4L2)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_W)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_H)
    cap.set(cv2.CAP_PROP_FPS, FPS)
    if not cap.isOpened():
        print("[ERROR] 카메라 열기 실패!")
    return cap

def mouth_open_norm(lm, w, h):
    uy = lm[UPPER_LIP].y * h
    ly = lm[LOWER_LIP].y * h
    le = np.array([lm[LEFT_EAR].x * w, lm[LEFT_EAR].y * h])
    re = np.array([lm[RIGHT_EAR].x * w, lm[RIGHT_EAR].y * h])
    face_w = np.linalg.norm(re - le) + 1e-6
    return abs(ly - uy) / face_w

def init_audio():
    usb_dev = None
    try:
        result = subprocess.run(["aplay", "-l"], capture_output=True, text=True)
        for line in result.stdout.strip().splitlines():
            if any(k in line for k in ["USB", "PnP", "Audio", "Sound"]):
                card = line.split(":")[0].split()[1]
                dev = line.split("device")[1].split(":")[0].strip()
                usb_dev = f"plughw:{card},{dev}"
                print(f"[INFO] USB 오디오 장치 감지됨: {usb_dev}")
                break
        if usb_dev:
            os.environ["SDL_AUDIODRIVER"] = "alsa"
            os.environ["AUDIODEV"] = usb_dev
        else:
            os.environ["SDL_AUDIODRIVER"] = "pulse"
    except Exception as e:
        print(f"[WARN] 오디오 장치 검색 실패: {e}")
        os.environ["SDL_AUDIODRIVER"] = "pulse"
    try:
        pygame.mixer.init()
        print("[DEBUG] 오디오 초기화 완료:", pygame.mixer.get_init())
    except Exception as e:
        print(f"[ERROR] pygame mixer 초기화 실패({e}) → dummy로 전환")
        os.environ["SDL_AUDIODRIVER"] = "dummy"
        pygame.mixer.init()

def play_mp3(path):
    try:
        if os.path.exists(path):
            if not pygame.mixer.music.get_busy():
                pygame.mixer.music.load(path)
                pygame.mixer.music.play()
                print(f"[DEBUG] MP3 재생: {path}")
        else:
            print(f"[WARN] 파일 없음: {path}")
    except Exception as e:
        print(f"[WARN] MP3 재생 실패({path}): {e}")

# ========================== 메인 ==========================
def main():
    print("[INFO] Start (Ctrl+C to exit)")
    cap = init_camera()
    init_audio()

    # --- 카메라 보정 로드 ---
    CALIB_PATH = "/home/bimsrl/Final_Project/camera_mtx.npz"
    mtx = dist = None
    if os.path.exists(CALIB_PATH):
        data = np.load(CALIB_PATH)
        mtx, dist = data["mtx"], data["dist"]
        print(f"[INFO] 카메라 보정 행렬 로드 완료: {CALIB_PATH}")
    else:
        print(f"[WARN] 보정 행렬 파일 없음 → 원본 사용")

    # --- 상태 변수 ---
    last_prompt_t = 0.0
    last_warn_t = 0.0
    mouth_state = "closed"
    last_open_time = 0.0
    last_close_time = time.time()

    # --- 마우스 이벤트 (원 이동) ---
    def mouse_event(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            SAFETY_CIRCLE_CENTER[0] = x
            SAFETY_CIRCLE_CENTER[1] = y
            print(f"[INFO] 안전 원 이동: {SAFETY_CIRCLE_CENTER}")

    cv2.namedWindow("Gaze+Chew+Safety (Calibrated)")
    cv2.setMouseCallback("Gaze+Chew+Safety (Calibrated)", mouse_event)

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                continue

            # 🔧 보정 적용
            if mtx is not None and dist is not None:
                frame = cv2.undistort(frame, mtx, dist, None, mtx)

            h, w = frame.shape[:2]
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            res = face_mesh.process(rgb)

            too_close = False
            face_center = None
            mval = 0.0

            if res.multi_face_landmarks:
                lm = res.multi_face_landmarks[0].landmark
                mval = mouth_open_norm(lm, w, h)
                face_center = (int(lm[NOSE_TIP].x * w), int(lm[NOSE_TIP].y * h))

                # 입 상태 추적
                now = time.time()
                if mval > MOUTH_OPEN_THR and mouth_state == "closed":
                    mouth_state = "open"
                    last_open_time = now
                    print("[DEBUG] 입 열림 감지")
                elif mval < MOUTH_CLOSE_THR and mouth_state == "open":
                    mouth_state = "closed"
                    last_close_time = now
                    print("[DEBUG] 입 닫힘 감지")

                # 안전 원 판정
                if USE_SAFETY_CIRCLE and face_center is not None:
                    dx = face_center[0] - SAFETY_CIRCLE_CENTER[0]
                    dy = face_center[1] - SAFETY_CIRCLE_CENTER[1]
                    dist2 = dx * dx + dy * dy
                    too_close = dist2 <= (SAFETY_CIRCLE_RADIUS ** 2)

                # 메쉬 표시
                mp_draw.draw_landmarks(
                    frame, res.multi_face_landmarks[0],
                    mp_face.FACEMESH_TESSELATION,
                    landmark_drawing_spec=None,
                    connection_drawing_spec=mp_style.get_default_face_mesh_tesselation_style()
                )

            # 판정
            now = time.time()
            no_chew = mouth_state == "closed" and (now - last_close_time) >= NO_CHEW_WAIT

            if res.multi_face_landmarks and (not too_close) and no_chew and (now - last_prompt_t >= PROMPT_COOLDOWN):
                print("[DEBUG] 음식 먹기 안내 재생 (입 닫힘 3초 유지)")
                play_mp3(PROMPT_MP3)
                last_prompt_t = now

            if too_close and (now - last_warn_t >= WARN_COOLDOWN):
                print("[DEBUG] TOO CLOSE! 경고음 재생")
                play_mp3(WARN_MP3)
                last_warn_t = now

            # 디스플레이
            if SHOW_WINDOW:
                if USE_SAFETY_CIRCLE:
                    cv2.circle(frame, tuple(SAFETY_CIRCLE_CENTER), SAFETY_CIRCLE_RADIUS, (0, 0, 255), 2)
                    if face_center is not None:
                        cv2.circle(frame, face_center, 6, (0, 255, 0) if not too_close else (0, 0, 255), -1)
                cv2.putText(frame, f"mouth_norm:{mval:.3f}", (20, 40),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 255), 2)
                state_txt = f"state:{mouth_state} | no_chew:{no_chew}"
                cv2.putText(frame, state_txt, (20, 75),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 200, 0), 2)
                remain = max(0.0, NO_CHEW_WAIT - (now - last_close_time))
                cv2.putText(frame, f"no_chew_timer:{remain:.1f}s", (20, 110),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.9, (200, 200, 0), 2)
                if too_close:
                    cv2.putText(frame, "TOO CLOSE", (20, 145),
                                cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 2)
                cv2.imshow("Gaze+Chew+Safety (Calibrated)", frame)
                cv2.waitKey(1)

    except KeyboardInterrupt:
        print("\n[INFO] Ctrl+C로 종료됨.")
    finally:
        cap.release()
        cv2.destroyAllWindows()
        pygame.mixer.quit()

# ==========================================================
if __name__ == "__main__":
    main()
