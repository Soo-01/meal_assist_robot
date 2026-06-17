import cv2
from pyzbar import pyzbar
import time
import subprocess
import requests
import json

# ── (1) 코드→메뉴 매핑
MENU_MAP = {
    "12345": "콩자반",
    "12355": "소세지",
    "12365": "메추리알",
    "12375": "어묵",
    "12385": "밥",
}

# ── (2) Ollama 설정: 모델 이름은 네가 만든 별칭으로!
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "CLOVA"     # 예: 'exaone', 'clova', 'exaone3.5:2.4b' 등

def draw_box_and_text(frame, barcode, text, color=(0, 255, 0)):
    (x, y, w, h) = barcode.rect
    cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
    text_y = max(25, y - 10)
    cv2.putText(frame, text, (x, text_y),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2, cv2.LINE_AA)

def speak(text: str):
    """Mimic3 로 음성 출력"""
    try:
        # stdin으로 안전하게 전달
        p1 = subprocess.Popen(
            ["mimic3", "--voice", "ko_KO/kss_low", "--stdout"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE
        )
        audio, _ = p1.communicate(input=text.encode("utf-8"), timeout=30)
        # 재생
        subprocess.run(["aplay"], input=audio, check=False)
    except Exception as e:
        print(f"⚠️ 음성 출력 실패: {e}")

def llm_explain(items: list[str]) -> str:
    """Ollama 로 설명 생성 (stream API 수신)"""
    if not items:
        return "스캔된 항목이 없어 설명할 내용이 없습니다."

    prompt = (
        "모든 음식 항목은 하나의 문단으로 끝나도록 해줘"
        "절대로 특수문자와 LATEX 문법을 쓰지마, **은 절대로 쓰지마."
        f"항목 목록: {', '.join(items)}\n"
    )

    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": True,
        # 필요 시 맥스 토큰/온도 조정:
        # "options": {"temperature": 0.6, "num_ctx": 1024}
    }

    try:
        with requests.post(OLLAMA_URL, json=payload, stream=True, timeout=60) as r:
            r.raise_for_status()
            chunks = []
            for line in r.iter_lines():
                if not line:
                    continue
                obj = json.loads(line.decode("utf-8"))
                if "response" in obj:
                    chunks.append(obj["response"])
                if obj.get("done"):
                    break
            text = "".join(chunks).strip()
            return text or "설명을 생성하지 못했습니다."
    except Exception as e:
        return f"Ollama 요청 중 오류가 발생했습니다: {e}"

def main():
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("❌ 카메라를 열 수 없습니다.")
        return

    print("📸 스캔을 시작합니다. (10초 동안, 5자리 숫자만)")
    start_time = time.time()
    duration = 10  # seconds

    detected_codes = []   # 순서 보존 + 중복 방지 수동
    known_items   = []    # 매핑 성공한 품목명
    unknown_codes = []    # 매핑 실패 코드

    while True:
        ret, frame = cap.read()
        if not ret:
            print("❌ 프레임을 가져올 수 없습니다.")
            break

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
                    # dup 표시
                    label = MENU_MAP.get(data, data)
                    draw_box_and_text(frame, bc, f"(dup) {label}", color=(0, 180, 180))
            else:
                # 표시까지 원치 않으면 이 줄을 지워도 됨
                draw_box_and_text(frame, bc, "ignored", color=(0, 0, 255))

        cv2.imshow("Barcode Scanner (Ollama+TTS)", frame)
        if cv2.waitKey(1) & 0xFF == 27:
            break
        if time.time() - start_time > duration:
            break

    cap.release()
    cv2.destroyAllWindows()

    # ── 결과 출력 (콘솔)
    print("\n==============================")
    if detected_codes:
        print("인식된 코드:", ", ".join(detected_codes))
        if known_items:
            print("인식된 항목:", ", ".join(known_items))
        if unknown_codes:
            print("미등록 코드:", ", ".join(unknown_codes))
    else:
        print("⛔ 10초 동안 5자리 숫자 바코드가 인식되지 않았습니다.")
    print("스캔이 끝났습니다.")
    print("==============================\n")

    # ── LLM 설명 + TTS
    if known_items:
        explanation = llm_explain(known_items)
        print("\n[LLM 설명]\n", explanation, "\n")
        speak(f"스캔이 끝났습니다. {explanation}")
    elif detected_codes:
        speak("스캔이 끝났습니다. 미등록 코드만 인식되었습니다.")
    else:
        speak("스캔이 끝났습니다. 인식된 바코드가 없습니다.")

if __name__ == "__main__":
    main()
