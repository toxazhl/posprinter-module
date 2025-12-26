import base64
import time
from io import BytesIO

# Імпортуємо класи принтерів
from escpos.printer import Dummy, Escpos, Network, Serial

try:
    from escpos.printer import Win32Raw
except ImportError:
    Win32Raw = None  # type: ignore

from PIL import Image

from posprinter.models import (
    ConnectionConfig,
    CutTask,
    DummyConnection,
    FeedTask,
    ImageTask,
    NetworkConnection,
    PrinterProfile,
    PrinterStatusData,
    PrintTask,
    RawTask,
    SerialConnection,
    TableTask,
    TextTask,
    Win32Connection,
)


class PrinterHandler:
    def __init__(self, config: ConnectionConfig):
        self.config = config
        self.p = None
        self.is_connected = False

    def get_status(self) -> PrinterStatusData:
        self.connect_if_needed()
        raw_status = self._query_status_raw(self.p)
        return PrinterStatusData(
            ready=raw_status.get("ready", False),
            online=raw_status.get("online", False),
            paper_out=raw_status.get("paper_out", False),
            error=raw_status.get("error"),
            details=raw_status.get("details"),
            warning=raw_status.get("warning", False),
        )

    def connect(self):
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
                raise RuntimeError("Unsupported connection type.")

            if hasattr(self.p, "open"):
                self.p.open()

            # To avoid dumb logs in console (The media.width.pixel...)
            self.p.profile.profile_data["media"]["width"].pop("pixels", None)

            self.p._raw(b"\x1b\x40")
            # self.p._raw(b"\x1b\x74\x11")
            self.p._raw(b"\x1b\x74\x49")

            self.is_connected = True

        except Exception as e:
            self.is_connected = False
            self.p = None
            raise RuntimeError(f"Connection failed: {e}")

    def connect_if_needed(self):
        if not self.is_connected or not self.p:
            self.connect()

    def close(self):
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

    def _query_status_raw(self, printer_instance: Escpos) -> dict:
        try:
            try:
                if hasattr(printer_instance.device, "flush_input"):
                    printer_instance.device.flush_input()
            except Exception:
                pass

            # 1. Offline (DLE EOT 1)
            printer_instance.device.write(b"\x10\x04\x01")
            status_byte = printer_instance.device.read(1)

            if not status_byte:
                return {
                    "ready": True,
                    "details": "No response (Assuming Online)",
                    "warning": True,
                }

            val = int.from_bytes(status_byte, "little")
            is_offline = bool(val & 0b00001000)

            # 2. Paper (DLE EOT 4)
            printer_instance.device.write(b"\x10\x04\x04")
            paper_byte = printer_instance.device.read(1)
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

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def process_task(self, task: PrintTask, profile: PrinterProfile):
        if not self.is_connected:
            self.connect()

        if not isinstance(task, ImageTask):
            self.p.set(align="left")

        if isinstance(task, TextTask):
            # НЕ ЗАБУДЬ import textwrap ЗВЕРХУ, БОВДУР
            import textwrap

            # 1. Рахуємо відступ зліва (щоб центрувати сам "аркуш" на "принтері")
            margin_base = max(
                0, (profile.printer_total_chars - profile.paper_width_chars) // 2
            )

            width = profile.paper_width_chars
            align = task.align.lower()
            original_text = task.value

            # 2. Спочатку розбиваємо текст по ЯВНИХ переносах рядків (\n), які ти туди запхав
            # keepends=False, щоб прибрати самі \n, ми їх додамо потім
            explicit_lines = original_text.split("\n")

            for paragraph in explicit_lines:
                # Якщо рядок порожній (наприклад, два \n підряд), просто друкуємо пустий рядок
                if not paragraph:
                    self.p._raw(b"\n")
                    continue

                # 3. Тепер ріжемо довгі рядки, щоб вони влізли в ширину паперу
                # break_long_words=True розрубає навіть довгі слова, щоб не вилізло за межі
                wrapped_chunks = textwrap.wrap(
                    paragraph, width=width, break_long_words=True
                )

                if not wrapped_chunks:
                    # На випадок якоїсь магії, якщо текст був, а чанків нема
                    self.p._raw(b"\n")
                    continue

                # 4. Обробляємо КОЖЕН шматок (рядок) окремо
                for chunk in wrapped_chunks:
                    chunk_len = len(chunk)
                    padding = 0

                    # Рахуємо відступ для вирівнювання всередині ширини паперу
                    if align == "center":
                        padding = (width - chunk_len) // 2
                    elif align == "right":
                        padding = width - chunk_len

                    # 5. Збираємо все до купи: Базовий відступ + Відступ вирівнювання + Текст
                    full_padding = margin_base + padding
                    final_line = (" " * full_padding) + chunk

                    # Відправляємо на друк
                    self.p._raw(final_line.encode("cp1251", "replace") + b"\n")
        elif isinstance(task, TableTask):
            margin_base = max(
                0, (profile.printer_total_chars - profile.paper_width_chars) // 2
            )
            cols_count = len(task.columns_ratio)
            for row in task.data:
                if len(row) != cols_count:
                    continue
                col_widths = [
                    int(profile.paper_width_chars * ratio)
                    for ratio in task.columns_ratio
                ]
                col_widths[-1] = profile.paper_width_chars - sum(col_widths[:-1])
                line_buffer = ""
                for i, text in enumerate(row):
                    width = col_widths[i]
                    text_cut = text[:width]
                    if i == cols_count - 1:
                        line_buffer += text_cut.rjust(width)
                    else:
                        line_buffer += text_cut.ljust(width)
                final_line = (" " * margin_base) + line_buffer
                self.p._raw(final_line.encode("cp1251", "replace") + b"\n")

        elif isinstance(task, ImageTask):
            self.p.set(align="center")
            try:
                img_bytes = base64.b64decode(task.data)
                img = Image.open(BytesIO(img_bytes))
                if img.mode != "1":
                    img = img.convert("1")
                ratio = profile.image_width_px / float(img.width)
                new_h = int(img.height * ratio)
                img = img.resize(
                    (profile.image_width_px, new_h), Image.Resampling.LANCZOS
                )
                self.p.image(img, impl="bitImageRaster")
                self.p.set(align="left")
            except Exception:
                pass

        elif isinstance(task, FeedTask):
            self.p._raw(b"\n" * task.lines)

        elif isinstance(task, CutTask):
            self.p._raw(b"\n\n\n")
            self.p.cut(mode="PART")

        elif isinstance(task, RawTask):
            self.p._raw(bytes.fromhex(task.hex_data.replace(" ", "")))
