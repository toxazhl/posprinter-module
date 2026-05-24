
# POS Printer Daemon CLI

This script acts as a JSON-based Command Line Interface (CLI) wrapper for POS printer operations. It reads JSON requests from `stdin` and outputs JSON responses to `stdout`.

## ⚠️ CRITICAL WARNING: Handle STDERR

**You must consume the `stderr` stream.**

The daemon redirects all logs, debug messages, and Python warnings to `stderr`. The `stdout` stream is reserved strictly for clean JSON responses.

If your client application does not read from the `stderr` pipe, the Operating System's buffer will eventually fill up. **Once the buffer is full, the daemon will hang (deadlock)** trying to write logs, and it will stop processing requests.

## Communication Protocol

1.  **Input:** Send a compact JSON object followed by a newline character (`\n`) to `stdin`.
2.  **Output:** Read a line from `stdout` and parse it as JSON.
3.  **Logs:** Monitor `stderr` for non-protocol messages.

## Data Models

### Connections (`connection`)
One of the following configurations must be passed with print or status requests.

*   **Windows:** `{"type": "windows", "printer_name": "EPSON TM-T20"}`
*   **Network:** `{"type": "network", "host": "192.168.1.100", "port": 9100}`
*   **Serial:** `{"type": "serial", "port": "COM3", "baudrate": 9600}`
*   **Dummy:** `{"type": "dummy"}` (For testing)

### Printer Profile (`profile`)
Defines the printable area and character encoding for the print job. Values are sent to the printer as ESC/POS hardware commands (`GS L` for the left margin, `GS W` for the print width, `ESC t` for the codepage) before each job.

*   `left_margin_dots` *(int, required)*: Left margin in dots. `0` = start at the physical left edge of the head.
*   `print_width_dots` *(int, required)*: Width of the printable area in dots. For 80 mm @ 203 DPI typically `576`, for 58 mm — `384`. For 180 DPI clones — `512` / `360`. The image task auto-resizes pictures to this width, and the table task derives the character count from `print_width_dots // 12` (Font A).
*   `encoding` *(str, default `"cp1251"`)*: Python encoding used to encode text bytes. Common: `cp1251` (Windows Cyrillic), `cp866` (DOS Cyrillic), `cp1125` (Ukrainian DOS, includes `Ґ`).
*   `codepage_id` *(int, optional)*: Printer codepage command ID sent via `ESC t n`. If `null`, it is inferred from `encoding` (cp1251→73, cp866→17, otherwise 0). Set explicitly when your printer firmware uses non-standard IDs — discover the right one by running `print_calibration_text` and reading the codepage diagnostics block on paper.

> **Calibration sets these values.** If unsure, run `print_calibration_text` once per printer model — the receipt shows safe-zone brackets (which give you `left_margin_dots` + `print_width_dots`) and a 17-row codepage matrix (which gives you `encoding` + `codepage_id`).

---

## Request Payloads

### 1. Get Printers
Retrieves a list of available system printers (Windows only).

```json
{
  "action": "get_printers"
}
```

### 2. Check Status
Checks if the printer is online, out of paper, or in an error state.

```json
{
  "action": "check_status",
  "connection": {
    "type": "network",
    "host": "192.168.1.50",
    "port": 9100
  }
}
```

### 3. Print Job
The main payload for printing. It requires a list of `tasks`.

**Task Types:**
*   `text`: Prints string.
*   `table`: Prints 2-column layout.
*   `image`: Prints Base64 encoded image.
*   `feed`: Feeds paper.
*   `cut`: Cuts paper.
*   `raw`: Sends raw hex bytes.

```json
{
  "action": "print",
  "connection": {
    "type": "windows",
    "printer_name": "Receipt Printer"
  },
  "profile": {
    "left_margin_dots": 0,
    "print_width_dots": 576,
    "encoding": "cp1251",
    "codepage_id": null
  },
  "tasks": [
    {
      "type": "text",
      "align": "center",
      "value": "Welcome to Our Store"
    },
    {
      "type": "feed",
      "lines": 1
    },
    {
      "type": "table",
      "columns_ratio": [0.7, 0.3],
      "data": [
        ["Item A", "$10.00"],
        ["Item B", "$5.50"]
      ]
    },
    {
      "type": "cut"
    }
  ]
}
```

### 4. Calibration
Prints a single reference receipt that combines two diagnostics in one pass:

1.  **Safe-zone map** — a 640×1150 bitmap with a dots-ruler at the top and labeled brackets for the four common geometries: `80mm FULL (0-576)`, `58mm CENTER (96-480)`, `58mm LEFT (0-384)`, `58mm RIGHT (192-576)` for 203 DPI, plus the equivalents for 180 DPI clones. Look at the receipt and pick the bracket whose endpoints align with the physical edges of your paper — that pair gives you `left_margin_dots` (start) and `print_width_dots` (end − start).
2.  **Codepage diagnostics** — prints the Cyrillic string `123 AБВабвІЇЄҐіїєґ` 17 times, each with a different `(codepage_id, encoding)` pair. Find the row where the text renders correctly and use that pair in `profile.codepage_id` + `profile.encoding`.

The `start`, `end`, `step` parameters control an additional text-width sweep (font A character grids) used for legacy calibration; they don't affect the bitmap or codepage diagnostics above.

```json
{
  "action": "print_calibration_text",
  "connection": { "type": "windows", "printer_name": "EPSON TM-T20X Receipt" },
  "start": 30,
  "end": 48,
  "step": 1
}
```

---

## Responses

### Success Response
```json
{
  "status": "success",
  "data": ... // (Optional data depending on request)
}
```

### Error Response
```json
{
  "status": "error",
  "error": "",
  "message": "Connection timed out",
  "traceback": "..."
}
```
```

---

### Python Client Example

This example demonstrates how to interact with the daemon safely using the `subprocess` module.

**Note:** It includes a background thread to continuously drain `stderr`. This is mandatory to prevent the application from hanging.

```python
import subprocess
import json
import threading
import sys
import time

# Path to the printer daemon script
DAEMON_SCRIPT = "printer_daemon.py"

class PrinterClient:
    def __init__(self, script_path):
        self.process = subprocess.Popen(
            [sys.executable, script_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1  # Line buffered
        )
        self.running = True
        
        # ---------------------------------------------------------
        # CRITICAL: Background thread to consume STDERR
        # If you don't do this, the buffer fills and the script hangs.
        # ---------------------------------------------------------
        self.stderr_thread = threading.Thread(target=self._monitor_stderr)
        self.stderr_thread.daemon = True
        self.stderr_thread.start()

    def _monitor_stderr(self):
        """Reads logs/errors from the daemon and prints them to console."""
        while self.running and self.process.poll() is None:
            line = self.process.stderr.readline()
            if line:
                # In a real app, use the logging module here
                print(f"[DAEMON LOG]: {line.strip()}")

    def send_request(self, payload: dict) -> dict:
        """Sends JSON to stdin and waits for JSON from stdout."""
        if self.process.poll() is not None:
            raise RuntimeError("Daemon process is not running")

        try:
            # 1. Serialize and Write
            json_str = json.dumps(payload)
            self.process.stdin.write(json_str + "\n")
            self.process.stdin.flush()

            # 2. Read Response
            response_line = self.process.stdout.readline()
            
            if not response_line:
                raise EOFError("Daemon closed the connection.")

            # 3. Parse Response
            return json.loads(response_line)

        except Exception as e:
            return {"status": "error", "error": "Client Error", "message": str(e)}

    def close(self):
        self.running = False
        if self.process:
            self.process.terminate()
            self.process.wait()


# --- Usage Examples ---

if __name__ == "__main__":
    client = PrinterClient(DAEMON_SCRIPT)
    
    # Give the daemon a moment to initialize
    time.sleep(1) 

    print("--- 1. Get Printers ---")
    req_printers = {"action": "get_printers"}
    resp = client.send_request(req_printers)
    print(f"Response: {json.dumps(resp, indent=2)}\n")

    print("--- 2. Check Status (Dummy) ---")
    req_status = {
        "action": "check_status",
        "connection": {"type": "dummy"}
    }
    resp = client.send_request(req_status)
    print(f"Response: {json.dumps(resp, indent=2)}\n")

    print("--- 3. Print Job ---")
    req_print = {
        "action": "print",
        "connection": {"type": "dummy"},
        "profile": {
            "left_margin_dots": 0,
            "print_width_dots": 576,
            "encoding": "cp1251",
            "codepage_id": None,
        },
        "tasks": [
            {"type": "text", "align": "center", "value": "TEST RECEIPT"},
            {"type": "feed", "lines": 2},
            {"type": "cut"}
        ]
    }
    resp = client.send_request(req_print)
    print(f"Response: {json.dumps(resp, indent=2)}\n")

    client.close()
