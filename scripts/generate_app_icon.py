"""Draw the iTunes Shuffler icon using only Pillow.

Run from any working directory: python path/to/scripts/generate_app_icon.py
The flat badge and crossing playback arrows are designed in 1024-pixel
coordinates. Tweak the palette, stroke width, or geometry below to restyle.
"""

from pathlib import Path

from PIL import Image, ImageDraw


SOURCE_SIZE = 1024
ICON_SIZES = (16, 32, 48, 64, 128, 256)
BADGE = "#2563EB"
RIM = "#60A5FA"
INK = "#FFFFFF"
STROKE = 88
OUTPUT_DIR = Path(__file__).resolve().parents[1] / "assets"


def bezier(start, control1, control2, end, steps=96):
    """Sample a cubic curve for Pillow's rounded line drawing."""
    points = []
    for step in range(steps + 1):
        t = step / steps
        u = 1 - t
        points.append(tuple(
            round(u**3 * start[axis] + 3 * u**2 * t * control1[axis]
                  + 3 * u * t**2 * control2[axis] + t**3 * end[axis])
            for axis in (0, 1)
        ))
    return points


def draw_source():
    """Return an RGBA source; only the area outside the badge is clear."""
    source = Image.new("RGBA", (SOURCE_SIZE, SOURCE_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(source)
    draw.rounded_rectangle((32, 32, 991, 991), radius=224, fill=RIM)
    draw.rounded_rectangle((48, 48, 975, 975), radius=208, fill=BADGE)

    downward = [(224, 344)] + bezier(
        (312, 344), (440, 344), (552, 680), (680, 680)
    ) + [(720, 680)]
    upward = [(224, 680)] + bezier(
        (312, 680), (440, 680), (552, 344), (680, 344)
    ) + [(720, 344)]

    draw.line(downward, fill=INK, width=STROKE, joint="curve")
    # A badge-colored knockout separates the paths at their crossing.
    draw.line(upward, fill=BADGE, width=STROKE + 56, joint="curve")
    draw.line(upward, fill=INK, width=STROKE, joint="curve")
    for y in (344, 680):
        radius = STROKE // 2
        draw.ellipse((224 - radius, y - radius,
                      224 + radius, y + radius), fill=INK)
        draw.polygon(((680, y - 120), (840, y), (680, y + 120)), fill=INK)
    return source


def verify_outputs(png_path, ico_path):
    """Decode every stored size, checking dimensions and badge opacity."""
    with Image.open(png_path) as source:
        source.load()
        assert source.format == "PNG"
        assert source.mode == "RGBA"
        assert source.size == (SOURCE_SIZE, SOURCE_SIZE)
    with Image.open(ico_path) as icon:
        assert icon.format == "ICO"
        assert icon.ico.sizes() == {(size, size) for size in ICON_SIZES}
        for size in ICON_SIZES:
            frame = icon.ico.getimage((size, size)).convert("RGBA")
            frame.load()
            assert frame.size == (size, size)
            assert frame.getpixel((size // 2, size // 2))[3] == 255
            # Lanczos can leave a near-transparent fringe at the smallest size.
            assert frame.getpixel((0, 0))[3] <= 3


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    png_path = OUTPUT_DIR / "app_icon_1024.png"
    ico_path = OUTPUT_DIR / "app_icon.ico"
    source = draw_source()
    source.save(png_path)
    # Resize each frame directly from the source to avoid cascaded resizing.
    frames = [source.resize((size, size), Image.Resampling.LANCZOS)
              for size in ICON_SIZES]
    frames[-1].save(
        ico_path,
        format="ICO",
        sizes=[(size, size) for size in ICON_SIZES],
        append_images=frames[:-1],
    )
    verify_outputs(png_path, ico_path)
    print(f"Saved {png_path}")
    print(f"Saved {ico_path}")
    print("PASS: RGBA 1024x1024 PNG and ICO sizes 16, 32, 48, 64, 128, 256")


if __name__ == "__main__":
    main()
