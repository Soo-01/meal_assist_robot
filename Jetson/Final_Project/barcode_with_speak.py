import cv2
from pyzbar import pyzbar
import time
import subprocess

# ── 코드→메뉴 매핑 (네가 준 예시)
MENU_MAP = {
    "12345": "콩자반",
    "12355": "소세지",
    "12365": "메추리알",
    "12375": "어묵",
    "12385": "밥",
}

def draw_box_and_text(frame, barcode, text, color=(0, 255, 0)):
    (x, y, w, h) = barcode.rect
    cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
    text_y = max(25, y - 10)
    cv2.putText(frame, text, (x, text_y),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2, cv2.LINE_AA)

def speak(text: str):
    try:
        subprocess.run(
            ["bash", "-c", f"echo '{text}' | mimic3 --voice ko_KO/kss_low | aplay"],
            check=True
        )
    except Exception as e:
        print(f"⚠️ 음성 출력 실패: {e}")

def main():
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("❌ 카메라를 열 수 없습니다.")
        return

    print("📸 스캔을 시작합니다. (10초 동안 스캔, 5자리 숫자만)")
    start_time = time.time()
    duration = 20  # seconds

    # 순서 보존을 위해 list 사용 + 중복 방지
    detected_codes = []       # 예: ["12345", "12375", ...]
    known_items = []          # 예: ["콩자반", "어묵", ...]
    unknown_codes = []        # 매핑에 없는 코드

    while True:
        ret, frame = cap.read()
        if not ret:
            print("❌ 프레임을 가져올 수 없습니다.")
            break

        for bc in pyzbar.decode(frame):
            data = bc.data.decode("utf-8").strip()

            # ✅ 5자리 숫자만 허용
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
                        print(f"✅ NEW: {data} (미등록 코드)")
                        draw_box_and_text(frame, bc, f"미등록({data})", color=(0, 165, 255))
                else:
                    # 중복
                    label = MENU_MAP.get(data, "dup")
                    draw_box_and_text(frame, bc, f"(dup) {label if label!='dup' else data}", color=(0, 180, 180))
            else:
                # 무시(표시도 원하면 숨겨도 됨)
                draw_box_and_text(frame, bc, "ignored", color=(0, 0, 255))

        cv2.imshow("Barcode Scanner (5-digit only + labels)", frame)
        if cv2.waitKey(1) & 0xFF == 27:
            break
        if time.time() - start_time > duration:
            break

    cap.release()
    cv2.destroyAllWindows()

    # ── 결과 출력(콘솔)
    print("\n==============================")
    if detected_codes:
        codes_line = ", ".join(detected_codes)
        print(f"인식된 코드: {codes_line}")
        if known_items:
            items_line = ", ".join(known_items)
            print(f"인식된 항목: {items_line}")
        if unknown_codes:
            unk_line = ", ".join(unknown_codes)
            print(f"미등록 코드: {unk_line}")
    else:
        print("⛔ 10초 동안 5자리 숫자 바코드가 인식되지 않았습니다.")
    print("스캔이 끝났습니다.")
    print("==============================\n")

    # ── TTS 안내
    if known_items:
        items_line = ", ".join(known_items)
        speak(f"스캔이 다 끝났습니다. 인식된 항목은 {items_line} 입니다.")
    elif detected_codes:
        codes_line = ", ".join(detected_codes)
        speak(f"스캔이 다 끝났습니다. 미등록 코드 {codes_line} 가 인식되었습니다.")
    else:
        speak("스캔이 다 끝났습니다. 인식된 바코드가 없습니다.")

if __name__ == "__main__":
    main()
