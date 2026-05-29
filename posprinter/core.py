import logging
from typing import List

try:
    import win32print
except ImportError:
    win32print = None

from posprinter.calibration import print_calibration_text
from posprinter.models import (
    ConnectionConfig,
    PrintCalibrationTextRequest,
    PrinterInfo,
    PrinterStatusData,
    PrintJobRequest,
)
from posprinter.printer import PrinterHandler

# Configure logging for production environments
logger = logging.getLogger(__name__)


class PrinterService:
    """
    Service responsible for executing print operations.

    Connections are NOT cached. Each operation opens a fresh connection, uses
    it, and closes it (via ``PrinterHandler``'s context manager). This is the
    only robust model for network/serial POS printers: a long-lived daemon that
    holds persistent sockets inevitably hits stale connections — network
    printers silently drop idle TCP links (ECONNRESET / WinError 10054), many
    accept only one connection at a time, and USB-serial adapters can be
    unplugged. A per-operation connection sidesteps all of that with negligible
    cost at human (cash-desk) printing rates, and needs no reconnect/retry/sleep
    logic to stay healthy.
    """

    # --- API Methods ---

    def get_printers(self) -> List[PrinterInfo]:
        """Enumerates local and network printers available to the OS (Windows only)."""
        if not win32print:
            raise RuntimeError("win32print module is not available.")

        try:
            flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
            printers = win32print.EnumPrinters(flags)
            return [
                PrinterInfo(name=str(p[2]), port=str(p[1]), driver=str(p[3]))
                for p in printers
            ]
        except Exception as e:
            logger.error(f"Failed to enumerate printers: {e}")
            return []

    def check_status(self, config: ConnectionConfig) -> PrinterStatusData:
        """Checks the hardware status of the printer."""
        with PrinterHandler(config) as handler:
            return handler.get_status()

    def print_job(self, request: PrintJobRequest) -> None:
        """Executes a full print job consisting of multiple tasks."""
        with PrinterHandler(request.connection) as handler:
            # Initialize hardware layout (Margins/Width) before the job starts
            handler.setup_hardware_layout(request.profile)

            for task in request.tasks:
                handler.process_task(task, request.profile)

            # Win32 buffers the job and sends it to the spooler on flush/close;
            # for network/serial this is a no-op (bytes are already on the wire).
            handler.flush()

    def print_calibration_text(self, request: PrintCalibrationTextRequest) -> None:
        """Executes a calibration test print."""
        with PrinterHandler(request.connection) as handler:
            print_calibration_text(handler.p)


# Singleton Instance
_service_instance = PrinterService()


def get_service() -> PrinterService:
    return _service_instance
