from typing import Dict, List

try:
    import win32print
except ImportError:
    win32print = None  # type: ignore

from posprinter.calibration.image import print_calibration_image
from posprinter.calibration.text import print_calibration_text
from posprinter.models import (
    ConnectionConfig,
    PrintCalibrationImageRequest,
    PrintCalibrationTextRequest,
    PrinterInfo,
    PrinterStatusData,
    PrintJobRequest,
)
from posprinter.printer import PrinterHandler


class PrinterService:
    def __init__(self):
        # Кеш хендлерів: Key -> PrinterHandler
        self._handlers: Dict[str, PrinterHandler] = {}

    def _get_handler(self, config: ConnectionConfig) -> PrinterHandler:
        if config.type == "serial":
            resource_key = f"serial:{config.port}"
        elif config.type == "windows":
            resource_key = f"windows:{config.printer_name}"
        elif config.type == "dummy":
            resource_key = "dummy"
        else:
            resource_key = f"network:{config.host}:{config.port}"

        if resource_key in self._handlers:
            handler = self._handlers[resource_key]
            if handler.config != config:
                handler.close()
                del self._handlers[resource_key]
            else:
                handler.connect_if_needed()
                return handler

        handler = PrinterHandler(config)
        handler.connect_if_needed()
        self._handlers[resource_key] = handler
        return handler

    def close_all(self):
        for h in self._handlers.values():
            try:
                h.close()
            except Exception:
                pass
        self._handlers.clear()

    # --- API Methods ---

    def get_printers(self) -> List[PrinterInfo]:
        if not win32print:
            raise RuntimeError("win32print module is not available.")

        data = []
        flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        printers = win32print.EnumPrinters(flags)

        for p in printers:
            info = PrinterInfo(name=str(p[2]), port=str(p[1]), driver=str(p[3]))
            data.append(info)

        return data

    def check_status(self, config: ConnectionConfig) -> PrinterStatusData:
        handler = self._get_handler(config)
        try:
            return handler.get_status()
        except OSError:
            handler.reconnect()
            return handler.get_status()

    def print_job(self, request: PrintJobRequest) -> None:
        handler = self._get_handler(request.connection)
        try:
            for task in request.tasks:
                handler.process_task(task, request.profile)
        except (OSError, RuntimeError) as e:
            handler.close()
            raise e

    def print_calibration_image(self, request: PrintCalibrationImageRequest) -> None:
        handler = self._get_handler(request.connection)
        p = handler.p
        print_calibration_image(p, request.start, request.end, request.step)

    def print_calibration_text(self, request: PrintCalibrationTextRequest) -> None:
        handler = self._get_handler(request.connection)
        p = handler.p
        print_calibration_text(p, request.start, request.end, request.step)


_service_instance = PrinterService()


def get_service():
    return _service_instance
