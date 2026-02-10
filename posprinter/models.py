from typing import Annotated, Any, List, Literal, Optional, TypeVar, Union

from pydantic import BaseModel, Field

# --- DATA MODELS  ---


class PrinterInfo(BaseModel):
    name: str
    port: str
    driver: str


class PrinterStatusData(BaseModel):
    ready: bool
    online: bool = False
    paper_out: bool = False
    error: Optional[str] = None
    details: Optional[str] = None
    warning: bool = False


# --- Tasks ---


class BaseTask(BaseModel):
    align: Literal["left", "center", "right"] = "center"


class TextTask(BaseTask):
    type: Literal["text"]
    value: str


class TableTask(BaseTask):
    type: Literal["table"]
    data: List[List[str]]
    columns_ratio: List[float] = [0.7, 0.3]


class ImageTask(BaseTask):
    type: Literal["image"]
    data: str  # Base64 string


class PdfTask(BaseTask):
    type: Literal["pdf"]
    data: str  # Base64 string


class FeedTask(BaseModel):
    type: Literal["feed"]
    lines: int = Field(default=1, ge=1, le=10)


class CutTask(BaseModel):
    type: Literal["cut"]


class RawTask(BaseModel):
    type: Literal["raw"]
    hex_data: str


PrintTask = Annotated[
    Union[TextTask, ImageTask, PdfTask, TableTask, FeedTask, CutTask, RawTask],
    Field(discriminator="type"),
]

# --- Connection Configs ---


class Win32Connection(BaseModel):
    type: Literal["windows"]
    printer_name: str


class NetworkConnection(BaseModel):
    type: Literal["network"]
    host: str
    port: int = 9100
    timeout: int = 10


class SerialConnection(BaseModel):
    type: Literal["serial"]
    port: str  # COM3, /dev/ttyUSB0
    baudrate: int = 9600
    bytesize: int = 8
    parity: Literal["N", "E", "O", "M", "S"] = "N"
    stopbits: int = 1
    timeout: int = 1
    dsrdtr: bool = True


class DummyConnection(BaseModel):
    type: Literal["dummy"]


# Об'єднання конфігів
ConnectionConfig = Annotated[
    Union[Win32Connection, NetworkConnection, SerialConnection, DummyConnection],
    Field(discriminator="type"),
]

# --- Requests ---


class PrinterProfile(BaseModel):
    printer_total_chars: int = Field(ge=20, le=100)
    paper_width_chars: int = Field(ge=10, le=100)
    image_width_px: int = Field(default=384, ge=100, le=3000)
    encoding: str = "cp1251"
    codepage_id: int | None = None


class PrintJobRequest(BaseModel):
    action: Literal["print"]
    connection: ConnectionConfig
    profile: PrinterProfile

    tasks: List[PrintTask]


class GetPrintersRequest(BaseModel):
    action: Literal["get_printers"]
    # Це працює тільки для Windows драйверів, для Network/Serial сканування писати не буду, йди в сраку


class CheckStatusRequest(BaseModel):
    action: Literal["check_status"]
    connection: ConnectionConfig


class PrintCalibrationImageRequest(BaseModel):
    action: Literal["print_calibration_image"]
    connection: ConnectionConfig
    start: int = Field(default=450, ge=10, le=1500)
    end: int = Field(default=700, ge=10, le=1500)
    step: int = Field(default=10, ge=5, le=100)


class PrintCalibrationTextRequest(BaseModel):
    action: Literal["print_calibration_text"]
    connection: ConnectionConfig

    start: int = Field(default=20, ge=10, le=200)
    end: int = Field(default=60, ge=10, le=200)
    step: int = Field(default=2, ge=1, le=50)


RequestModel = Annotated[
    Union[
        PrintJobRequest,
        GetPrintersRequest,
        CheckStatusRequest,
        PrintCalibrationImageRequest,
        PrintCalibrationTextRequest,
    ],
    Field(discriminator="action"),
]


# --- Responses ---

T = TypeVar("T")


class BaseResponse(BaseModel):
    status: str


class ErrorResponse(BaseResponse):
    status: Literal["error"] = "error"
    error: str
    message: Optional[str] = None
    details: Optional[Any] = None
    traceback: Optional[str] = None


class SuccessResponse(BaseResponse):
    status: Literal["success"] = "success"


class GetPrintersResponse(SuccessResponse):
    data: List[PrinterInfo]


class CheckStatusResponse(SuccessResponse):
    data: PrinterStatusData
