import logging
from typing import Dict, List

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
    Singleton service responsible for managing printer connections and
    executing print jobs.
    """

    def __init__(self):
        self._handlers: Dict[str, PrinterHandler] = {}

    def _get_resource_key(self, config: ConnectionConfig) -> str:
        """Generates a unique key for connection caching."""
        if config.type == "serial":
            return f"serial:{config.port}"
        elif config.type == "windows":
            return f"windows:{config.printer_name}"
        elif config.type == "dummy":
            return "dummy"
        elif config.type == "network":
            return f"network:{config.host}:{config.port}"
        return f"unknown:{id(config)}"

    def _get_handler(self, config: ConnectionConfig) -> PrinterHandler:
        """
        Retrieves an existing handler or creates a new one.
        Ensures the configuration matches the cached instance.
        """
        key = self._get_resource_key(config)

        if key in self._handlers:
            handler = self._handlers[key]
            if handler.config != config:
                logger.info(f"Configuration changed for {key}. Reconnecting.")
                handler.close()
                del self._handlers[key]
            else:
                handler.connect_if_needed()
                return handler

        handler = PrinterHandler(config)
        handler.connect_if_needed()
        self._handlers[key] = handler
        return handler

    def close_all(self):
        """Gracefully closes all active printer connections."""
        for key, handler in self._handlers.items():
            try:
                handler.close()
            except Exception as e:
                logger.error(f"Error closing handler {key}: {e}")
        self._handlers.clear()

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
        handler = self._get_handler(config)
        try:
            return handler.get_status()
        except OSError:
            # Attempt one reconnection before failing
            handler.reconnect()
            return handler.get_status()

    def print_job(self, request: PrintJobRequest) -> None:
        """
        Executes a full print job consisting of multiple tasks.
        """
        handler = self._get_handler(request.connection)
        try:
            # Initialize hardware layout (Margins/Width) before the job starts
            handler.setup_hardware_layout(request.profile)

            for task in request.tasks:
                handler.process_task(task, request.profile)

        except (OSError, RuntimeError) as e:
            logger.error(f"Print job failed: {e}")
            handler.close()
            raise e

        finally:
            handler.flush()

    def print_calibration_text(self, request: PrintCalibrationTextRequest) -> None:
        """Executes a calibration test print."""
        handler = self._get_handler(request.connection)
        try:
            # Ensure we have access to the underlying Escpos object
            print_calibration_text(handler.p, request.start, request.end, request.step)
        finally:
            # Calibration is an edge case; safer to close connection immediately
            handler.close()


# Singleton Instance
_service_instance = PrinterService()


def get_service() -> PrinterService:
    return _service_instance
