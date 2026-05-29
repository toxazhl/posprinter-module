import base64
import json
import queue
import subprocess
import threading
import time

CONNECTION_CONFIG = {
    "type": "serial",
    "port": "/dev/cu.usbserial-210",
    "baudrate": 115200,
    "timeout": 2,
    "dsrdtr": True,
}

PRINTER_SETUP = {
    "left_margin_dots": 0,
    "print_width_dots": 576,
    "encoding": "cp1251",
    "codepage_id": None,
}

IMAGE_FILENAME = "test_ticket.png"


def enqueue_output(out, q):
    try:
        for line in iter(out.readline, b""):
            q.put(line)
    except ValueError:
        pass
    out.close()


def read_response(q_stdout, q_stderr, timeout=15):
    start = time.time()
    while time.time() - start < timeout:
        try:
            err = q_stderr.get_nowait()
            print(f"[STDERR] {err.decode('utf-8', errors='replace').strip()}")
        except queue.Empty:
            pass
        try:
            return q_stdout.get_nowait()
        except queue.Empty:
            time.sleep(0.1)
    raise TimeoutError(f"No response within {timeout}s")


def log_response(byte_line):
    if not byte_line:
        print("<<< [EMPTY]")
        return
    text = byte_line.decode("utf-8", errors="replace").strip()
    try:
        print(json.dumps(json.loads(text), indent=2, ensure_ascii=False))
    except json.JSONDecodeError:
        print(f"<<< [RAW] {text}")


def main():
    with open(IMAGE_FILENAME, "rb") as f:
        img_data = base64.b64encode(f.read()).decode("utf-8")

    print(">>> Starting daemon...")
    process = subprocess.Popen(
        ["uv", "run", "python", "-m", "posprinter"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )

    q_stdout, q_stderr = queue.Queue(), queue.Queue()
    threading.Thread(target=enqueue_output, args=(process.stdout, q_stdout), daemon=True).start()
    threading.Thread(target=enqueue_output, args=(process.stderr, q_stderr), daemon=True).start()

    try:
        time.sleep(1)

        print("\n>>> [1] Checking status...")
        req = {"action": "check_status", "connection": CONNECTION_CONFIG}
        process.stdin.write((json.dumps(req) + "\n").encode("utf-8"))
        process.stdin.flush()
        try:
            log_response(read_response(q_stdout, q_stderr, timeout=8))
        except TimeoutError:
            print("Timeout on status")

        print("\n>>> [2] Printing image...")
        req = {
            "action": "print",
            "connection": CONNECTION_CONFIG,
            "profile": PRINTER_SETUP,
            "tasks": [
                {"type": "image", "data": img_data},
                {"type": "feed", "lines": 2},
                {"type": "cut"},
            ],
        }
        process.stdin.write((json.dumps(req) + "\n").encode("utf-8"))
        process.stdin.flush()
        try:
            log_response(read_response(q_stdout, q_stderr, timeout=20))
        except TimeoutError:
            print("Timeout on print")
    finally:
        print("\n>>> Stopping daemon...")
        process.terminate()
        try:
            process.wait(timeout=2)
        except Exception:
            process.kill()
        print(">>> Done.")


if __name__ == "__main__":
    main()
