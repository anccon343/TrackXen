"""Draw TrackXen's icon (packaging/trackxen.ico): an orange ring and dot on dark, like the tray icon."""

from pathlib import Path

from PIL import Image, ImageDraw


def icon(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad, ring = size * 0.04, max(2, round(size * 0.1))
    d.ellipse((pad, pad, size - pad, size - pad), fill=(28, 28, 28, 255), outline=(217, 115, 13, 255), width=ring)
    r = size * 0.14
    d.ellipse((size / 2 - r, size / 2 - r, size / 2 + r, size / 2 + r), fill=(217, 115, 13, 255))
    return img


if __name__ == "__main__":
    out = Path(__file__).with_name("trackxen.ico")
    icon(256).save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(out)
