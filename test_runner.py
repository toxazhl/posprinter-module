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
    "port": "COM3",  # Зміни на свій порт!
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

# Налаштування розмірів (для тексту)
PRINTER_SETUP = {
    "printer_total_chars": 48,  # Фізична ширина (48 для 80мм)
    "paper_width_chars": 32,  # Робоча область (32 для 58мм або відступів)
}

IMAGE_FILENAME = "logo.png"  # Назва файлу поруч зі скриптом

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

        # 2. Формуємо запит (Згідно з новим Pydantic models)
        req_print = {
            "action": "print",
            "connection": CONNECTION_CONFIG,
            "profile": {
                "printer_total_chars": PRINTER_SETUP["printer_total_chars"],
                "paper_width_chars": PRINTER_SETUP["paper_width_chars"],
                "image_width_px": 900,
            },
            "tasks": [
                # Заголовок
                {
                    "type": "text",
                    "value": "--- POS PRINTER TEST ---",
                    "align": "center",
                },
                {"type": "feed", "lines": 1},
                # Текст зліва
                {
                    "type": "text",
                    "value": f"Port: {CONNECTION_CONFIG.get('port', 'Unknown')}",
                    "align": "left",
                },
                {
                    "type": "text",
                    "value": "Status: Connected",
                    "align": "left",
                },
                # Картинка
                {
                    "type": "image",
                    "data": img_data,
                    "align": "center",
                },
                # Таблиця
                {
                    "type": "table",
                    "data": [
                        ["Item A", "100.00"],
                        ["Item B long name", "50.50"],
                        ["Discount", "-10.00"],
                        ["TOTAL", "140.50"],
                    ],
                    "columns_ratio": [0.65, 0.35],
                    "align": "left",
                    **PRINTER_SETUP,
                },
                # Футер
                {"type": "feed", "lines": 1},
                {
                    "type": "text",
                    "value": "Дякуємо за покупку!",
                    "align": "center",
                    **PRINTER_SETUP,
                },
                {
                    "type": "text",
                    "value": "Slava Ukraini!",
                    "align": "center",
                    **PRINTER_SETUP,
                },
                # Обрізка
                {"type": "feed", "lines": 2},
                {"type": "cut"},
            ],
        }

        print(">>> Sending Print Job...")
        msg = json.dumps(req_print) + "\n"
        process.stdin.write(msg.encode("utf-8"))
        process.stdin.flush()

        try:
            # На друк даємо більше часу
            resp = read_response_with_timeout(process, q_stdout, q_stderr, timeout=15)
            safe_log_response(resp)
        except TimeoutError:
            print("⏰ Timeout on Print Job")

        process.stdin.write(msg.encode("utf-8"))
        process.stdin.flush()

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
