"""Draw the app icon (a waveform on a rounded square) and write every size Tauri
lists in shell/src-tauri/tauri.conf.json. Generated, so no artwork is committed
that we can't regenerate."""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "shell" / "src-tauri" / "icons"


def draw(size: int = 1024) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = size // 10
    d.rounded_rectangle((pad, pad, size - pad, size - pad), radius=size // 5, fill="#1f4e5a")
    bars = 9
    inner = size - 4 * pad
    step = inner / bars
    for i in range(bars):
        h = inner * (0.25 + 0.7 * abs(math.sin(i * 0.9 + 0.4)))
        x = 2 * pad + i * step + step * 0.2
        y = size / 2 - h / 2
        d.rounded_rectangle((x, y, x + step * 0.6, y + h), radius=step * 0.3, fill="#f3efe4")
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    big = draw()
    for name, px in (("32x32.png", 32), ("128x128.png", 128), ("128x128@2x.png", 256)):
        big.resize((px, px), Image.Resampling.LANCZOS).save(OUT / name)
    big.save(OUT / "icon.png")
    big.save(OUT / "icon.icns")
    big.save(OUT / "icon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (256, 256)])


if __name__ == "__main__":
    main()
