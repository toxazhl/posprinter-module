import logging
from typing import Tuple

from escpos.printer import Escpos
from PIL import Image, ImageDraw, ImageFont

# Налаштування логування
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class CalibrationRenderer:
    """
    Handles the generation of calibration graphics for thermal printers.
    Supports visualization of safe zones for 203 DPI and 180 DPI emulation.
    """

    # --- Constants for Layout ---
    WIDTH_PX = 640
    HEIGHT_PX = 1150
    SPLIT_Y = 580

    # Zones definition: (x1, y1, x2, y2)
    ZONE_203_DPI = (192, 0, 384, SPLIT_Y)
    ZONE_180_DPI = (152, SPLIT_Y, 360, HEIGHT_PX)

    # Centers
    CENTER_203 = 288
    CENTER_180 = 256

    def __init__(self):
        self._font = self._load_font(size=22)

    def _load_font(self, size: int) -> ImageFont.ImageFont:
        """
        Attempts to load a bold system font. Falls back to default if not found.
        """
        font_candidates = [
            "arialbd.ttf",
            "Arial_Bold.ttf",
            "DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "C:\\Windows\\Fonts\\arialbd.ttf",
            "/System/Library/Fonts/HelveticaNeue.ttc",
        ]

        for path in font_candidates:
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue

        logger.warning("Custom font not found. Using default PIL font.")
        return ImageFont.load_default()

    def _draw_hatch_pattern(
        self, draw: ImageDraw.ImageDraw, box: Tuple[int, int, int, int], step: int = 10
    ):
        """
        Fills a specific rectangular area with a diagonal hatch pattern.
        """
        x1, y1, x2, y2 = box
        width = x2 - x1
        height = y2 - y1

        # Clip area handling
        for i in range(-height, width, step):
            # Draw diagonal lines. Coordinate math ensures they stay within bounds
            # logically, but we rely on PIL to handle the intersection visually if needed.
            # For strict bounding, we check coordinates.

            line_start_x = x1 + i
            line_start_y = y1

            line_end_x = x1 + i + height
            line_end_y = y1 + height

            # Simple clipping for the specific box area
            # A distinct line drawing approach to ensure clean edges
            start = (
                max(x1, line_start_x),
                max(y1, line_start_y + (x1 - line_start_x if line_start_x < x1 else 0)),
            )
            end = (
                min(x2, line_end_x),
                min(y2, line_end_y - (line_end_x - x2 if line_end_x > x2 else 0)),
            )

            # Correction to simple diagonal logic for visual "hatch" inside rect
            # Easier approach: draw line, PIL clips automatically if drawing on a mask,
            # but since we draw on main image, we accept slight overflow or complex math.
            # Professional approach: Draw lines.

            draw.line(
                (max(x1, line_start_x), line_start_y, min(x2, line_end_x), line_end_y),
                fill="#d0d0d0",
                width=1,
            )

    def _draw_hatch_zone_robust(self, img: Image.Image, box: Tuple[int, int, int, int]):
        """
        Robust hatching using a mask to ensure clean edges without complex math.
        """
        x1, y1, x2, y2 = box
        w, h = x2 - x1, y2 - y1

        # Create a temporary transparent layer for the pattern
        pattern = Image.new("RGBA", (w, h), (255, 255, 255, 0))
        d = ImageDraw.Draw(pattern)

        step = 8
        for i in range(-h, w, step):
            d.line((i, 0, i + h, h), fill="gray", width=1)

        # Paste pattern onto the main image
        img.paste(pattern, (x1, y1), mask=pattern)

    def _draw_text_box(
        self, draw: ImageDraw.ImageDraw, x: int, y: int, text: str, bg: bool = True
    ):
        """Draws centered text with optional white background for readability."""
        if hasattr(draw, "textbbox"):
            bbox = draw.textbbox((0, 0), text, font=self._font)
            text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        else:
            text_w, text_h = self._font.getsize(text)

        pos_x = x - text_w / 2

        if bg:
            pad = 3
            draw.rectangle(
                (pos_x - pad, y + 2, pos_x + text_w + pad, y + text_h - 2), fill="white"
            )
        draw.text((pos_x, y), text, fill="black", font=self._font)

    def _draw_bracket(
        self,
        draw: ImageDraw.ImageDraw,
        y: int,
        start: int,
        end: int,
        label: str,
        is_bold: bool = False,
    ):
        """Draws a measurement bracket with labels."""
        line_w = 5 if is_bold else 3

        # Horizontal line
        draw.line((start, y, end, y), fill="black", width=line_w)
        # Vertical caps
        draw.line((start, y - 8, start, y + 12), fill="black", width=line_w)
        draw.line((end, y - 8, end, y + 12), fill="black", width=line_w)

        mid = (start + end) / 2
        # Label (Top)
        self._draw_text_box(draw, int(mid), y - 25, label)
        # Range (Bottom)
        self._draw_text_box(draw, int(mid), y + 15, f"{start}-{end}")

    def create_image(self) -> Image.Image:
        """Generates the full calibration bitmap."""
        img = Image.new("RGB", (self.WIDTH_PX, self.HEIGHT_PX), color="white")
        draw = ImageDraw.Draw(img)

        # 1. Draw Safe Zones (Hatched)
        self._draw_hatch_zone_robust(img, self.ZONE_203_DPI)
        self._draw_hatch_zone_robust(img, self.ZONE_180_DPI)

        # 2. Draw Ruler
        ruler_y = 50
        draw.line((0, ruler_y, 600, ruler_y), fill="black", width=2)
        for x in range(0, 601, 10):
            h = 5
            if x % 50 == 0:
                h = 12
            if x % 100 == 0:
                h = 20
                self._draw_text_box(draw, x, ruler_y - 40, str(x), bg=False)
            draw.line((x, ruler_y, x, ruler_y - h), fill="black", width=2)

        # 3. Sections Configuration
        GAP_HEADER_TO_FIRST = 70
        GAP_STEP = 90

        # --- Section 1: 203 DPI ---
        header_y = 120
        self._draw_text_box(draw, self.CENTER_203, header_y, "=== 203 DPI ===")
        # Note: "Safe Center" text removed as per request

        base_y = header_y + GAP_HEADER_TO_FIRST + 10
        self._draw_bracket(draw, base_y, 0, 576, "80mm FULL", is_bold=True)
        self._draw_bracket(draw, base_y + GAP_STEP, 96, 480, "58mm CENTER")
        self._draw_bracket(draw, base_y + GAP_STEP * 2, 0, 384, "58mm LEFT")
        self._draw_bracket(draw, base_y + GAP_STEP * 3, 192, 576, "58mm RIGHT")

        # --- Section 2: 180 DPI ---
        header_180_y = self.SPLIT_Y + 40
        self._draw_text_box(draw, self.CENTER_180, header_180_y, "=== 180 DPI ===")
        # Note: "Safe Center" text removed as per request

        base_y_180 = header_180_y + GAP_HEADER_TO_FIRST + 10
        self._draw_bracket(draw, base_y_180, 0, 512, "80mm FULL", is_bold=True)
        self._draw_bracket(draw, base_y_180 + GAP_STEP, 76, 436, "58mm CENTER")
        self._draw_bracket(draw, base_y_180 + GAP_STEP * 2, 0, 360, "58mm LEFT")
        self._draw_bracket(draw, base_y_180 + GAP_STEP * 3, 152, 512, "58mm RIGHT")

        # 4. Draw Guidelines
        # Vertical boundaries for 203 DPI
        draw.line((192, ruler_y, 192, self.SPLIT_Y), fill="black", width=1)
        draw.line((384, ruler_y, 384, self.SPLIT_Y), fill="black", width=1)
        # Vertical boundaries for 180 DPI
        draw.line((152, self.SPLIT_Y, 152, self.HEIGHT_PX), fill="black", width=1)
        draw.line((360, self.SPLIT_Y, 360, self.HEIGHT_PX), fill="black", width=1)

        # Horizontal Splitter
        draw.line((0, self.SPLIT_Y, self.WIDTH_PX, self.SPLIT_Y), fill="gray", width=1)

        # Vertical Grid Lines (Guides)
        guides = [0, 76, 96, 152, 192, 360, 384, 436, 480, 512, 576]
        for g in guides:
            for y in range(ruler_y + 15, self.HEIGHT_PX - 15, 15):
                draw.line((g, y, g, y + 5), fill="#cccccc", width=1)

        return img


class CodepageTester:
    """
    Handles brute-forcing and testing of printer code pages for Cyrillic support.
    """

    # Priority list of codepages to test
    # Format: (Printer_Command_ID, Python_Encoding_Name, Description)
    CANDIDATES = [
        # === TIER 1: The Standards (Most likely to work) ===
        (17, "cp866", "ID 17: PC866 (Standard Cyrillic DOS)"),
        (73, "cp1251", "ID 73: Windows-1251 (Standard Cyrillic Win)"),
        (0, "cp437", "ID 0:  PC437 (USA - English Check)"),
        # === TIER 2: Common Chinese/XPrinter Variations ===
        (255, "cp1251", "ID 255: XPrinter Special (Win-1251)"),
        (255, "cp866", "ID 255: XPrinter Special (PC866)"),
        (1, "cp1251", "ID 1:   Katakana slot often used for 1251"),
        # === TIER 3: Legacy & Alternative IDs ===
        (6, "cp866", "ID 6:  Old Standard PC866"),
        (6, "cp1251", "ID 6:  Old Standard Win-1251"),
        (17, "cp1251", "ID 17: Hidden Win-1251 (Rare firmware)"),
        (73, "cp866", "ID 73: Hidden PC866"),
        # === TIER 4: Specific Regional/Rare Mappings ===
        (34, "cp855", "ID 34: PC855 (Cyrillic Serbian/Bulgarian)"),
        (22, "cp1251", "ID 22: Arabic/Cyrillic Mix"),
        (24, "cp866", "ID 24: Rare PC866"),
        (33, "cp1251", "ID 33: Rare Win-1251"),
        (48, "cp1251", "ID 48: Large Buffer Win-1251"),
        # === TIER 5: Ukrainian Specific (The "Jackpot") ===
        # CP1125 is the specific Ukrainian DOS encoding containing 'Ґ'
        (17, "cp1125", "ID 17: UA-DOS (CP1125)"),
        (6, "cp1125", "ID 6:  UA-DOS (CP1125)"),
    ]

    TEST_PHRASE = "123 AБВабвІЇЄҐіїєґ"

    @classmethod
    def run_diagnostics(cls, printer: Escpos):
        """Iterates through known codepages to test character rendering."""
        printer.text("------------------------------\n")
        printer.text("Codepage Diagnostics Routine\n")
        printer.text(f"Testing {len(cls.CANDIDATES)} configurations...\n")
        printer.text("------------------------------\n")

        for cmd_id, py_enc, desc in cls.CANDIDATES:
            try:
                # Set printer codepage
                printer._raw(b"\x1b\x74" + bytes([cmd_id]))

                # Header (ASCII safe)
                info_line = f"[{cmd_id}] {py_enc.upper()}\n"
                printer._raw(info_line.encode("ascii", "ignore"))

                # Payload
                encoded_data = cls.TEST_PHRASE.encode(py_enc, errors="replace")
                printer._raw(encoded_data + b"\n")
                printer._raw(b"- - - - - - - - -\n")

            except Exception as e:
                logger.error(f"Failed testing {desc}: {e}")
                printer._raw(b"Error: Test failed\n")

        printer.text("\nDiagnostic Complete.\n\n")


def print_calibration_text(p: Escpos, start: int, end: int, step: int) -> None:
    """
    Main orchestration function.
    """
    # 1. Initialize logic
    renderer = CalibrationRenderer()

    try:
        # 2. Reset Printer
        p._raw(b"\x1b\x40")
        logger.info("Printer reset. Starting job.")

        # 3. Generate and Print Alignment Image
        logger.info("Generating calibration image...")
        img = renderer.create_image()

        p.set(align="left")
        p.image(
            img, impl="bitImageRaster"
        )  # bitImageRaster is generally more compatible
        p.text("\n")

        # 4. Run Text Encoding Diagnostics
        p.set(align="center")
        logger.info("Running codepage diagnostics...")
        CodepageTester.run_diagnostics(p)

        # 5. Final Cut
        p.cut(mode="PART", feed=True)
        logger.info("Job completed successfully.")

    except Exception as e:
        logger.critical(f"Critical printing error: {e}")
        # Attempt to print error on paper if connection is still alive
        try:
            p.text(f"\nSYSTEM ERROR: {str(e)}\n")
            p.cut()
        except:
            pass
