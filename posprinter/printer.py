import base64
import logging
import time
from io import BytesIO
from typing import List, Optional

import pypdfium2 as pdfium
from escpos.printer import Dummy, Escpos, Network, Serial
from PIL import Image

try:
    from escpos.printer import Win32Raw
except ImportError:
    Win32Raw = None

from posprinter.models import (
    ConnectionConfig,
    CutTask,
    DummyConnection,
    FeedTask,
    ImageTask,
    NetworkConnection,
    PdfTask,
    PrinterProfile,
    PrinterStatusData,
    PrintTask,
    RawTask,
    SerialConnection,
    TableTask,
    TextTask,
    Win32Connection,
)

logger = logging.getLogger(__name__)

# Standard Font A width in dots (commonly 12x24)
FONT_A_WIDTH_DOTS = 12

# Luminance cutoff (0-255) for converting images to 1-bit black/white.
# Pixels brighter than this become white, darker become black. Higher values
# print more pixels as black (bolder/darker); lower values print lighter.
IMAGE_THRESHOLD = 128


class PrinterHandler:
    """
    Handles low-level communication with the POS printer using the ESC/POS protocol.
    Manages connection state and task translation.
    """

    def __init__(self, config: ConnectionConfig):
        self.config = config
        self.p: Optional[Escpos] = None
        self.is_connected = False

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def connect(self):
        """Establishes connection based on configuration type."""
        if self.is_connected and self.p:
            return

        try:
            if isinstance(self.config, Win32Connection):
                if not Win32Raw:
                    raise RuntimeError("Win32Raw printer is not available.")
                self.p = Win32Raw(printer_name=self.config.printer_name)

            elif isinstance(self.config, SerialConnection):
                self.p = Serial(
                    devfile=self.config.port,
                    baudrate=self.config.baudrate,
                    bytesize=self.config.bytesize,
                    parity=self.config.parity,
                    stopbits=self.config.stopbits,
                    timeout=self.config.timeout,
                    dsrdtr=self.config.dsrdtr,
                )

            elif isinstance(self.config, NetworkConnection):
                self.p = Network(
                    host=self.config.host,
                    port=self.config.port,
                    timeout=self.config.timeout,
                )

            elif isinstance(self.config, DummyConnection):
                self.p = Dummy()

            else:
                raise RuntimeError(f"Unsupported connection type: {type(self.config)}")

            if hasattr(self.p, "open"):
                self.p.open()

            # Prevent python-escpos logging spam regarding pixel widths
            if hasattr(self.p, "profile"):
                self.p.profile.profile_data.get("media", {}).get("width", {}).pop(
                    "pixels", None
                )

            # Initial Reset
            self.p._raw(b"\x1b\x40")
            self.is_connected = True

        except Exception as e:
            self.is_connected = False
            self.p = None
            raise RuntimeError(f"Connection failed: {e}")

    def close(self):
        """Closes the connection safely."""
        if self.p:
            try:
                self.p.close()
            except Exception:
                pass
        self.p = None
        self.is_connected = False

    def reconnect(self):
        self.close()
        time.sleep(0.5)
        self.connect()

    def connect_if_needed(self):
        if not self.is_connected or not self.p:
            self.connect()

    # --- Status Management ---

    def get_status(self) -> PrinterStatusData:
        """Queries the printer for real-time status (ASB)."""
        self.connect_if_needed()
        if not self.p:
            raise RuntimeError("Printer not connected")

        raw = self._query_status_raw()
        return PrinterStatusData(
            ready=raw.get("ready", False),
            online=raw.get("online", False),
            paper_out=raw.get("paper_out", False),
            error=raw.get("error"),
            details=raw.get("details"),
            warning=raw.get("warning", False),
        )

    def _query_status_raw(self) -> dict:
        """Sends DLE EOT commands to get printer status bits."""
        try:
            # Flush input buffer to remove old data
            if hasattr(self.p.device, "flush_input"):
                self.p.device.flush_input()

            # DLE EOT 1: Printer Status
            self.p.device.write(b"\x10\x04\x01")
            status_byte = self.p.device.read(1)

            if not status_byte:
                return {
                    "ready": True,
                    "details": "No response (Assuming Online)",
                    "warning": True,
                }

            val = int.from_bytes(status_byte, "little")
            is_offline = bool(val & 0b00001000)

            # DLE EOT 4: Paper Sensor
            self.p.device.write(b"\x10\x04\x04")
            paper_byte = self.p.device.read(1)
            is_paper_out = False
            if paper_byte:
                pval = int.from_bytes(paper_byte, "little")
                if pval & 0b01100000:
                    is_paper_out = True

            return {
                "online": not is_offline,
                "paper_out": is_paper_out,
                "ready": (not is_offline and not is_paper_out),
            }
        except Exception as e:
            return {"ready": False, "error": "IO Error", "details": str(e)}

    # --- Print Logic ---

    def setup_hardware_layout(self, profile: PrinterProfile):
        """
        Configures the printer's printable area and margins using hardware commands.
        Uses `left_margin_dots` (GS L) and `print_width_dots` (GS W).
        """
        if not self.p:
            return

        # 1. Reset printer to clear previous states
        self.p._raw(b"\x1b\x40")

        # 3. Set Left Margin (GS L nL nH)
        # Allows moving the print area to the right physically.
        self.p._raw(b"\x1d\x4c" + profile.left_margin_dots.to_bytes(2, "little"))

        # 4. Set Printing Area Width (GS W nL nH)
        # Defines the width of the printable line.
        self.p._raw(b"\x1d\x57" + profile.print_width_dots.to_bytes(2, "little"))

    def set_codepage_by_encoding(self, profile: PrinterProfile):
        """Sets the printer codepage based on the requested encoding."""
        if not self.p:
            return

        # Use explicitly provided ID if available, otherwise guess based on encoding name
        codepage_id = profile.codepage_id
        if codepage_id is None:
            enc = profile.encoding.lower().replace("-", "")
            if enc in ["cp866", "ibm866"]:
                codepage_id = 17
            elif enc in ["win1251", "cp1251", "windows1251"]:
                codepage_id = 73
            elif enc == "pc437":
                codepage_id = 0
            else:
                codepage_id = 0

        # ESC t n
        self.p._raw(b"\x1b\x74" + bytes([codepage_id]))

    def process_task(self, task: PrintTask, profile: PrinterProfile):
        """Process a single task within a print job."""
        self.connect_if_needed()

        # Ensure correct encoding for every task
        self.set_codepage_by_encoding(profile)
        encoding = profile.encoding

        try:
            # Set default alignment to left before processing text
            if not isinstance(task, (ImageTask, PdfTask)):
                self.p.set(align="left")

            if isinstance(task, TextTask):
                self._process_text_task(task, encoding)

            elif isinstance(task, TableTask):
                self._process_table_task(task, profile, encoding)

            elif isinstance(task, ImageTask):
                self._process_image_task(task, profile)

            elif isinstance(task, PdfTask):
                self._process_pdf_task(task, profile)

            elif isinstance(task, FeedTask):
                self.p._raw(b"\n" * task.lines)

            elif isinstance(task, CutTask):
                self.p._raw(b"\n\n\n")
                self.p.cut(mode="PART")

            elif isinstance(task, RawTask):
                # Sanitize and send raw hex data
                clean_hex = task.hex_data.replace(" ", "").replace("\n", "")
                self.p._raw(bytes.fromhex(clean_hex))

        except Exception as e:
            logger.error(f"Task execution failed: {e}")
            raise

    def flush(self):
        """
        Finalizes the print job.
        For Windows: Sends the accumulated buffer to the OS Spooler properly.
        """
        if not self.p:
            return

        # Handle Windows printing specifically
        if Win32Raw is not None and isinstance(self.p, Win32Raw):
            self.close()

    def _process_text_task(self, task: TextTask, encoding: str):
        """
        Handles text printing.
        Relying on hardware wrapping (GS W) configured in setup_hardware_layout.
        """
        align = task.align.lower()
        if align in ["center", "right"]:
            self.p.set(align=align)
        else:
            self.p.set(align="left")

        # Encode text with fallback
        try:
            encoded_bytes = task.value.encode(encoding, "replace")
        except LookupError:
            logger.warning(f"Encoding {encoding} not supported. Fallback to cp866.")
            encoded_bytes = task.value.encode("cp866", "replace")

        self.p._raw(encoded_bytes + b"\n")

        # Restore default alignment
        self.p.set(align="left")

    def _process_table_task(
        self, task: TableTask, profile: PrinterProfile, encoding: str
    ):
        """
        Handles table printing by calculating column widths in characters.
        """
        print_width_dots = profile.print_width_dots
        total_chars = print_width_dots // FONT_A_WIDTH_DOTS

        cols_count = len(task.columns_ratio)

        for row in task.data:
            if len(row) != cols_count:
                continue

            # Calculate width per column
            col_widths = [int(total_chars * ratio) for ratio in task.columns_ratio]
            # Adjust last column to absorb rounding errors
            col_widths[-1] = total_chars - sum(col_widths[:-1])

            line_buffer = ""
            for i, text in enumerate(row):
                width = col_widths[i]
                # Truncate if too long to prevent breaking layout
                text_cut = text[:width]

                if i == cols_count - 1:
                    line_buffer += text_cut.rjust(width)
                else:
                    line_buffer += text_cut.ljust(width)

            self.p._raw(line_buffer.encode(encoding, "replace") + b"\n")

    def _process_image_task(self, task: ImageTask, profile: PrinterProfile):
        self.p.set(align="left")
        img_bytes = base64.b64decode(task.data)
        self._print_image_bytes(
            img_bytes, profile, dither=task.dither, threshold=task.threshold
        )
        self.p.set(align="left")

    def _process_pdf_task(self, task: PdfTask, profile: PrinterProfile):
        self.p.set(align="left")
        images = self._pdf_to_base64_images(base64.b64decode(task.data))
        for img_str in images:
            img_bytes = base64.b64decode(img_str)
            self._print_image_bytes(
                img_bytes, profile, dither=task.dither, threshold=task.threshold
            )
        self.p.set(align="left")

    def _print_image_bytes(
        self,
        img_bytes: bytes,
        profile: PrinterProfile,
        dither: bool = False,
        threshold: int = IMAGE_THRESHOLD,
    ):
        """
        Resizes and prints an image.
        Uses `print_width_dots` from the profile to determine target width.
        """
        img = Image.open(BytesIO(img_bytes))

        if img.mode in ("RGBA", "LA") or (
            img.mode == "P" and "transparency" in img.info
        ):
            if img.mode != "RGBA":
                img = img.convert("RGBA")
            background = Image.new("RGB", img.size, (255, 255, 255))
            # Paste using alpha channel as mask
            background.paste(img, mask=img.split()[3])
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")

        # Use the configured print width as the target width for images
        target_width = profile.print_width_dots

        if img.width == 0:
            return

        # Calculate new height to maintain aspect ratio
        ratio = target_width / float(img.width)
        new_h = int(img.height * ratio)

        img = img.resize((target_width, new_h), Image.Resampling.LANCZOS)

        # Convert to 1-bit for the thermal head. Either way the OUTPUT is pure
        # black/white — no gray pixel ever reaches the printer.
        if dither:
            # Floyd-Steinberg dithering fakes grayscale by scattering dots.
            # Good for photos/shaded graphics, bad for text (looks smudged).
            img = img.convert("1")
        else:
            # Hard luminance threshold: every pixel becomes solid black or
            # white. Keeps glyph edges crisp — best for receipts/line-art.
            # LANCZOS-then-threshold beats threshold-then-NEAREST here: it gives
            # smoother edges on upscaled text and preserves thin features (e.g.
            # dashed rules) that native-res thresholding would drop.
            img = img.convert("L").point(
                lambda px: 255 if px >= threshold else 0, mode="1"
            )

        # Use raster bit image for better compatibility and performance
        self.p.image(img, impl="bitImageRaster")

    @staticmethod
    def _pdf_to_base64_images(pdf_bytes: bytes) -> List[str]:
        result = []
        pdf = pdfium.PdfDocument(pdf_bytes)
        for i in range(len(pdf)):
            page = pdf[i]
            # Render at high DPI for clarity
            bitmap = page.render(scale=4)
            pil_image = bitmap.to_pil()

            buffered = BytesIO()
            pil_image.save(buffered, format="PNG")
            result.append(base64.b64encode(buffered.getvalue()).decode())
        return result
