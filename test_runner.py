import base64
import json
import os
import queue
import subprocess
import threading
import time

# --- 1. НАЛАШТУВАННЯ ПІДКЛЮЧЕННЯ ---

# Варіант А: Serial (COM порт)
CONNECTION_CONFIG = {
    "type": "serial",
    "port": "/dev/cu.usbmodem8E748C6505371",  # Зміни на свій порт!
    "baudrate": 115200,  # Перевір швидкість принтера (9600, 19200, 115200)
    "timeout": 2,
    "dsrdtr": True,
}

# Варіант Б: Windows Driver (USB/LAN через спулер)
# CONNECTION_CONFIG = {
#     "type": "windows",
#     "printer_name": "XP-80C"
# }

# Варіант В: Network (Direct LAN)
# CONNECTION_CONFIG = {
#     "type": "network",
#     "host": "192.168.1.100",
#     "port": 9100
# }

# Налаштування друкованої області (новий API: hardware-команди GS L / GS W / ESC t)
PRINTER_SETUP = {
    "left_margin_dots": 0,       # GS L — зсув від лівого краю голівки в dots
    "print_width_dots": 576,     # GS W — ширина області друку в dots (80мм @ 203 DPI = 576)
    "encoding": "cp1251",        # python-енкодинг для байтів тексту
    "codepage_id": None,         # ESC t n — None = автогадання з encoding (cp1251→73, cp866→17)
}

IMAGE_FILENAME = "rec.png"  # Назва файлу поруч зі скриптом
PDF_FILENAME = "TEST_kzDbXXeB9OvI5Q.pdf"  # Назва PDF файлу поруч зі скриптом

# --- 2. ДОПОМІЖНІ ФУНКЦІЇ ---


def image_to_base64(path):
    """Читає файл і кодує в Base64. Якщо файлу нема - повертає чорний квадрат."""
    if not os.path.exists(path):
        print(f"⚠️ Файл '{path}' не знайдено. Використовуємо тестовий квадрат.")
        # Маленький чорний квадрат 10x10
        return "R0lGODlhCgAKAPAAAP///wAAACH5BAEAAAAALAAAAAAKAAoAAAIRhI+py+0Po5y02ouz3rz7rxQAOw=="

    with open(path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")


def enqueue_output(out, q):
    """Читає потік у фоні, щоб не блокувати головний процес"""
    try:
        for line in iter(out.readline, b""):
            q.put(line)
    except ValueError:
        pass
    out.close()


def read_response_with_timeout(process, q_stdout, q_stderr, timeout=5):
    """Читає відповідь з таймаутом"""
    start_time = time.time()
    while time.time() - start_time < timeout:
        # Перевірка STDERR
        try:
            err_line = q_stderr.get_nowait()
            print(f"❌ [STDERR]: {err_line.decode('utf-8', errors='replace').strip()}")
        except queue.Empty:
            pass

        # Перевірка STDOUT
        try:
            line = q_stdout.get_nowait()
            return line
        except queue.Empty:
            time.sleep(0.1)

    raise TimeoutError(f"No response within {timeout} seconds.")


def safe_log_response(byte_line):
    """Красивий вивід JSON"""
    if not byte_line:
        print("<<< [EMPTY RESPONSE]")
        return
    text = byte_line.decode("utf-8", errors="replace").strip()
    try:
        data = json.loads(text)
        print(json.dumps(data, indent=2, ensure_ascii=False))
        if data.get("status") == "error":
            print(f"⚠️  ERROR DETAILS: {data.get('error')}")
    except json.JSONDecodeError:
        print(f"<<< [RAW]: {text}")


# --- 3. ГОЛОВНА ЛОГІКА ---


def run_test():
    print(">>> 🚀 Запускаємо демона (posprinter)...")

    # Запускаємо модуль як підпроцес
    process = subprocess.Popen(
        ["uv", "run", "python", "-m", "posprinter"],
        # ["./dist/posprinter.dist/posprinter"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )

    # Запускаємо потоки читання
    q_stdout = queue.Queue()
    q_stderr = queue.Queue()

    t_out = threading.Thread(target=enqueue_output, args=(process.stdout, q_stdout))
    t_out.daemon = True
    t_out.start()

    t_err = threading.Thread(target=enqueue_output, args=(process.stderr, q_stderr))
    t_err.daemon = True
    t_err.start()

    try:
        time.sleep(1)  # Прогрів

        # === ТЕСТ 1: STATUS ===
        req_status = {"action": "check_status", "connection": CONNECTION_CONFIG}
        print("\n>>> [1] Checking Status...")

        msg = json.dumps(req_status) + "\n"
        process.stdin.write(msg.encode("utf-8"))
        process.stdin.flush()

        try:
            resp = read_response_with_timeout(process, q_stdout, q_stderr, timeout=5)
            safe_log_response(resp)
        except TimeoutError:
            print("⏰ Timeout on Status Check")

        # === ТЕСТ 2: PRINT RECEIPT ===
        print("\n>>> [2] Preparing Receipt...")

        # 1. Беремо картинку
        img_data = image_to_base64(IMAGE_FILENAME)
        pdf_data = image_to_base64(PDF_FILENAME)

        # 2. Формуємо запит (Згідно з новим Pydantic models)
        req_calibration = {
            "action": "print_calibration_text",
            "connection": CONNECTION_CONFIG,
            "start": 20,
            "end": 60,
            "step": 2,
        }
        print(json.dumps(req_calibration, indent=2, ensure_ascii=False))

        req_print = {
            "action": "print",
            "connection": CONNECTION_CONFIG,
            "profile": PRINTER_SETUP,
            "tasks": [
                # Заголовок
                {
                    "type": "image",
                    "data": img_data,
                },
                {"type": "cut"},
                {
                    "type": "pdf",
                    "data": pdf_data,
                },
                # {
                #     "type": "text",
                #     "value": "ОФФЛАЙН ТАЛОН\n\n",
                #     "align": "center",
                # },
                # {
                #     "type": "text",
                #     "value": 'ТРЦ "РайON" м. Київ, вул.Лаврухіна 4\n'
                #     'ТОВ "Європаркінг", 063-6422712\n'
                #     "Талон обов'язковий для в'їзду\n"
                #     "Нічний тариф - оплата з 1 хв",
                #     "align": "left",
                # },
                # {"type": "feed", "lines": 2},     о
                # {
                #     "type": "text",
                #     "value": "В'їзд: 25.06.2024 14:30",
                #     "align": "center",
                # },
                # {"type": "feed", "lines": 2},
                # {
                #     "type": "text",
                #     "value": "Безкоштовно 60хв\n"
                #     "Кожна наступна година - 30 грн\n"
                #     "Доба - 200 грн\n"
                #     "За втрату талону штраф 300 грн",
                #     "align": "left",
                # },
                {"type": "cut"},
            ],
        }

        print(">>> Sending Print Job...")
        msg = json.dumps(req_print) + "\n"
        process.stdin.write(msg.encode("utf-8"))
        process.stdin.flush()
        print(">>> Print Job Sent. Awaiting response...")
        print(json.dumps(req_print, indent=2, ensure_ascii=False))

        try:
            # На друк даємо більше часу
            resp = read_response_with_timeout(process, q_stdout, q_stderr, timeout=15)
            safe_log_response(resp)
        except TimeoutError:
            print("⏰ Timeout on Print Job")

    except KeyboardInterrupt:
        print("\n🛑 Interrupted by user")
    finally:
        print("\n>>> ☠️ Stopping process...")
        process.terminate()
        try:
            process.wait(timeout=2)
        except:
            process.kill()
        print(">>> Done.")


if __name__ == "__main__":
    run_test()
