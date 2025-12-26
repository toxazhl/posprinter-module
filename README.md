
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
Defines the physical characteristics of the printer for formatting.
*   `printer_total_chars`: Characters per line (e.g., 42 or 48).
*   `paper_width_chars`: Printable width chars.
*   `image_width_px`: Width to resize images to (typically 384 or 512).

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
    "printer_total_chars": 48,
    "paper_width_chars": 48,
    "image_width_px": 384
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

### 4. Calibration (Image)
Prints a series of images with varying densities/sizes to calibrate settings.

```json
{
  "action": "print_calibration_image",
  "connection": { "type": "dummy" },
  "start": 450,
  "end": 500,
  "step": 10
}
```

### 5. Calibration (Text)
Prints text grids to determine the correct character width configuration.

```json
{
  "action": "print_calibration_text",
  "connection": { "type": "dummy" },
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
  "error": "Printer Error",
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
            "printer_total_chars": 48,
            "paper_width_chars": 48,
            "image_width_px": 384
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
