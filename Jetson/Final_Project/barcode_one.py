import cv2
from pyzbar import pyzbar
import time

def draw_box_and_text(frame, barcode, text, color=(0, 255, 0)):
    (x, y, w, h) = barcode.rect
    cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
    text_y = max(25, y - 10)
    cv2.putText(frame, text, (x, text_y),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2, cv2.LINE_AA)

def main():
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("❌ 카메라를 열 수 없습니다.")
        return

    print("📸 스캔을 시작합니다. (10초 동안 스캔)")
    start_time = time.time()
    duration = 15  # 10초 동안 스캔
    detected_data = set()

    while True:
        ret, frame = cap.read()
        if not ret:
            print("❌ 프레임을 가져올 수 없습니다.")
            break

        barcodes = pyzbar.decode(frame)
        for bc in barcodes:
            data = bc.data.decode("utf-8").strip()

            # ✅ 5자리 숫자만 허용
            if len(data) == 5 and data.isdigit():
                if data not in detected_data:
                    detected_data.add(data)
                    print(f"✅ NEW: {data}")
                    draw_box_and_text(frame, bc, f"{data}", color=(0, 255, 0))
                else:
                    draw_box_and_text(frame, bc, f"{data}", color=(0, 180, 180))
            else:
                # 5자리가 아니면 무시
                draw_box_and_text(frame, bc, "ignored", color=(0, 0, 255))

        cv2.imshow("Barcode Scanner (5-digit only)", frame)
        if cv2.waitKey(1) & 0xFF == 27:
            break
        if time.time() - start_time > duration:
            break

    cap.release()
    cv2.destroyAllWindows()

    # 결과 출력
    if detected_data:
        result_str = ", ".join([f"{d}" for d in detected_data])
        print(f"{result_str}")

    else:
        print("\n⛔ 10초 동안 5자리 숫자 바코드가 인식되지 않았습니다.\n")

if __name__ == "__main__":
    main()

# 콩자반: 12345, 소세지: 12355, 메추리알: 12365, 어묵: 12375, 밥: 12385
# 