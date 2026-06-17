#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import cv2
import numpy as np
import glob
import os

# ========================= 설정 =========================
CAM_INDEX = 0
FRAME_W, FRAME_H = 1920, 1080
CHESSBOARD_SIZE = (8, 5)  # 내부 코너 수
CALIB_OUTPUT = "camera_mtx.npz"  # 저장 파일명

# ======================== 수집 모드 ======================
cap = cv2.VideoCapture(CAM_INDEX)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_W)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_H)

criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)

# 체커보드 실제 3D 좌표 준비 (z=0 평면)
objp = np.zeros((CHESSBOARD_SIZE[0]*CHESSBOARD_SIZE[1], 3), np.float32)
objp[:, :2] = np.mgrid[0:CHESSBOARD_SIZE[0], 0:CHESSBOARD_SIZE[1]].T.reshape(-1, 2)

objpoints = []  # 3D points
imgpoints = []  # 2D points
capture_count = 0

print("[INFO] 's' 눌러 샘플 캡처, 'q' 눌러 종료")

while True:
    ret, frame = cap.read()
    if not ret:
        continue

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    found, corners = cv2.findChessboardCorners(gray, CHESSBOARD_SIZE, None)

    if found:
        corners2 = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), criteria)
        cv2.drawChessboardCorners(frame, CHESSBOARD_SIZE, corners2, found)

    cv2.putText(frame, f"Captured: {capture_count}", (20, 50),
                cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 255), 2)
    cv2.imshow("Calibration Capture", frame)
    key = cv2.waitKey(1) & 0xFF

    if key == ord('s') and found:
        objpoints.append(objp)
        imgpoints.append(corners2)
        capture_count += 1
        print(f"[INFO] 캡처 {capture_count}개 저장 완료")

    elif key == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()

# ======================== 보정 단계 ======================
if len(objpoints) < 5:
    print("[WARN] 샘플이 너무 적습니다 (최소 5장 이상 필요)")
    exit()

ret, mtx, dist, rvecs, tvecs = cv2.calibrateCamera(
    objpoints, imgpoints, gray.shape[::-1], None, None
)

print("\n[RESULT] Calibration complete")
print("Camera matrix (mtx):\n", mtx)
print("Distortion coeffs (dist):\n", dist.ravel())

# 저장
np.savez(CALIB_OUTPUT, mtx=mtx, dist=dist)
print(f"[INFO] 보정 행렬 저장 완료 → {CALIB_OUTPUT}")
