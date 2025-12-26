from escpos.printer import Escpos
from PIL import Image, ImageDraw, ImageFont

ROW_HEIGHT = 40
FONT_SIZE = 20
BAR_THICKNESS = 6
LINE_THICKNESS = 2


def print_calibration_image(p: Escpos, start: int, end: int, step: int) -> None:
    p._raw(b"\x1b\x40")  # Reset
    p.set(align="center")  # Centered

    p._raw(b"--- IMAGE WIDTH CALIBRATION ---\n")
    p._raw(f"Range: {start}-{end} px\n".encode("cp866"))
    p._raw(b"Find the widest line\n")
    p._raw(b"with visible BOTH bars |\n\n")

    try:
        font = ImageFont.truetype("arialbd.ttf", FONT_SIZE)
    except IOError:
        try:
            font = ImageFont.truetype("arial.ttf", FONT_SIZE)
        except IOError:
            font = ImageFont.load_default()

    for width in range(start, end + 1, step):
        img = Image.new("1", (width, ROW_HEIGHT), 1)
        draw = ImageDraw.Draw(img)

        x_start = 0
        x_end = width - 1

        y_top = 0
        y_bottom = ROW_HEIGHT - 1
        y_mid = ROW_HEIGHT // 2

        # 1. Horizontal line
        draw.line([(x_start, y_mid), (x_end, y_mid)], fill=0, width=LINE_THICKNESS)

        # 2. Left bar
        draw.rectangle([x_start, y_top, x_start + BAR_THICKNESS, y_bottom], fill=0)

        # 3. Right bar
        draw.rectangle([x_end - BAR_THICKNESS, y_top, x_end, y_bottom], fill=0)

        # Text
        text = f"{width}"

        # try:
        bbox = draw.textbbox((0, 0), text, font=font)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        # except AttributeError:
        #     w, h = draw.textsize(text, font=font)

        # center text
        text_x = (width - w) // 2
        text_y = (ROW_HEIGHT - h) // 2 - 2

        # 4. white rectangle background
        pad = 6
        draw.rectangle(
            [text_x - pad, y_top + 4, text_x + w + pad, y_bottom - 4], fill=1
        )

        # 5. Text
        draw.text((text_x, text_y), text, font=font, fill=0)

        # Print image
        p.image(img, impl="bitImageRaster")

    p._raw(b"\n\n\n")
    p.cut(mode="PART")
