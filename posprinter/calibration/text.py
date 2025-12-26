from escpos.printer import Escpos


def print_calibration_text(p: Escpos, start: int, end: int, step: int) -> None:
    p._raw(b"\x1b\x40")
    p._raw(b"\x1b\x74\x11")
    p.set(align="center")

    p._raw(b"--- TEXT CALIBRATION ---\n\n")

    p._raw(b"1. PRINTER LIMIT (Total Chars) \n")
    p._raw(b"Find the MAX number that stays \n")
    p._raw(b"on ONE single line.            \n")
    p._raw(b"(If it splits/wraps -> Too Big)\n\n")

    p._raw(b"2. PAPER LIMIT (Paper Width)   \n")
    p._raw(b"Find the MAX number where      \n")
    p._raw(b"you see BOTH brackets [ ]      \n")
    p._raw(b"(If bracket is gone -> Too Big)\n\n")

    p._raw(b"--------------------------------\n\n")

    for width in range(start, end, step):
        label = f" {width} "

        available = width - 2 - len(label)

        left_len = available // 2
        right_len = available - left_len

        line = f"[{'<' * left_len}{label}{'>' * right_len}]\n"

        p._raw(line.encode("cp866"))

    p._raw(b"\n\n\n")
    p.cut(mode="PART")
