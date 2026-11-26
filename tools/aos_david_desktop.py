"""Native offline document preview on a disposable Xvfb display."""
from __future__ import annotations

import io
import sys
import textwrap

from PIL import Image, ImageDraw, ImageFont, ImageGrab
from Xlib import X, display

WIDTH, HEIGHT = 960, 660


def frame(data, mode, scroll):
    canvas = Image.new("RGB", (WIDTH, HEIGHT), "white")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 16)
    draw.rectangle((0, 0, WIDTH, 36), fill="#243447")
    draw.text((16, 8), "David document preview", fill="white", font=font)
    if mode == "text":
        lines = [part for line in data.decode("utf-8").splitlines() for part in textwrap.wrap(line, 92) or [""]]
        first = max(0, min(scroll, len(lines) - 1))
        draw.multiline_text((20, 52), "\n".join(lines[first:first+27]), fill="black", font=font, spacing=6)
    else:
        image = Image.open(io.BytesIO(data), formats=["JPEG"]).convert("RGB")
        image.thumbnail((WIDTH - 20, HEIGHT - 48))
        canvas.paste(image, ((WIDTH - image.width) // 2, 40))
    return canvas


def capture(mode, scroll):
    data = sys.stdin.buffer.read(1024 * 1024)
    server = display.Display()
    try:
        screen = server.screen()
        root = screen.root
        window = root.create_window(0, 0, WIDTH, HEIGHT, 0, screen.root_depth, X.InputOutput,
                                    X.CopyFromParent, background_pixel=screen.white_pixel)
        window.set_wm_name("David document preview")
        window.map()
        gc = window.create_gc()
        # X11 requests have a size limit. Draw the fixed viewport in strips.
        canvas = frame(data, mode, scroll)
        for y in range(0, HEIGHT, 48):
            strip = canvas.crop((0, y, WIDTH, min(y + 48, HEIGHT)))
            window.put_image(gc, 0, y, WIDTH, strip.height, X.ZPixmap, screen.root_depth, 0,
                             strip.tobytes("raw", "BGRX"))
        server.sync()
        ImageGrab.grab(xdisplay=server.get_display_name()).convert("RGB").save(sys.stdout.buffer, "JPEG", quality=80)
    finally:
        server.close()


if __name__ == "__main__":
    capture(sys.argv[1], int(sys.argv[2]))
