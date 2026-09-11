"""Three library-derived icon studies; Python + Pillow, no source-art writes.

Run from any directory: python path/to/scripts/generate_app_icon_candidates.py
Optional: --artwork PATH --output PATH --seed 271828 --count 72
The contact sheet includes actual 16px renders on light and dark backgrounds.
The JSON manifest records the sample, source hashes, and extracted palette.
"""

import argparse
import colorsys
import hashlib
import json
import math
from pathlib import Path
import random

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps


SIZE = 1024
ROOT = Path(__file__).resolve().parents[1]
ARTWORK = Path.home() / "Music" / "Apple Music" / "Apple Music Library.musiclibrary" / "artwork"
LANCZOS = Image.Resampling.LANCZOS


def sample_covers(folder, count, seed):
    paths = sorted(p for p in folder.rglob("*") if p.is_file())
    random.Random(seed).shuffle(paths)
    covers, records, seen = [], [], set()
    for path in paths:
        try:
            with Image.open(path) as source:
                cover = ImageOps.fit(ImageOps.exif_transpose(source).convert("RGB"), (256, 256))
            fingerprint = hashlib.sha256(cover.resize((32, 32)).tobytes()).hexdigest()
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            covers.append(cover)
            records.append({"file": str(path.relative_to(folder)),
                            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
            if len(covers) == count:
                break
        except (OSError, ValueError):
            continue
    if len(covers) < count:
        raise ValueError(f"Need {count} readable distinct covers; found {len(covers)}")
    return covers, records


def palette_from(covers):
    atlas = Image.new("RGB", (32 * len(covers), 32))
    for i, cover in enumerate(covers):
        atlas.paste(cover.resize((32, 32), LANCZOS), (i * 32, 0))
    quantized = atlas.quantize(colors=32, method=Image.Quantize.MEDIANCUT)
    palette = quantized.getpalette()
    swatches = []
    for weight, index in sorted(quantized.getcolors(), reverse=True):
        rgb = tuple(palette[index * 3:index * 3 + 3])
        swatches.append({"rgb": rgb, "pixels": weight})
    return swatches


def accents(swatches):
    # Pick the strongest warm and cool chromatic clusters, not preset colors.
    def choose(warm):
        options = []
        for item in swatches:
            rgb = item["rgb"]
            h, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))
            is_warm = h < .18 or h > .87
            if is_warm == warm and s > .22 and v > .22:
                options.append((s * v * math.sqrt(item["pixels"]), rgb))
        if not options:
            raise ValueError("Sample has insufficient warm/cool color diversity; try another seed")
        rgb = max(options)[1]
        h, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))
        return tuple(round(c * 255) for c in colorsys.hsv_to_rgb(h, min(s, .78), max(v, .90)))
    return choose(True), choose(False)


def mask():
    return Image.new("L", (SIZE, SIZE))


def solid(color):
    return Image.new("RGB", (SIZE, SIZE), color)


def paste_shape(canvas, texture, shape):
    canvas.paste(texture, (0, 0), shape)


def shadow(canvas, shape, offset=(0, 18), blur=20, opacity=140):
    shifted = mask()
    shifted.paste(shape, offset)
    shifted = shifted.filter(ImageFilter.GaussianBlur(blur)).point(lambda p: p * opacity // 255)
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    layer.paste((8, 7, 15, 255), (0, 0), shifted)
    canvas.alpha_composite(layer)


def mosaic(covers, columns=6, offset=0):
    tile = math.ceil(SIZE / columns)
    atlas = solid("#15131b")
    for row in range(columns):
        for col in range(columns):
            cover = covers[(offset + row * columns + col) % len(covers)]
            atlas.paste(ImageOps.fit(cover, (tile - 3, tile - 3)), (col * tile, row * tile))
    return atlas


def round_tile(color):
    canvas = Image.new("RGBA", (SIZE, SIZE))
    shape = mask()
    ImageDraw.Draw(shape).rounded_rectangle((38, 38, 986, 986), 218, fill=255)
    paste_shape(canvas, solid(color), shape)
    return canvas, shape


def record(covers, warm, cool):
    """A: a physical record, its outer ring assembled from actual cover wedges."""
    canvas = Image.new("RGBA", (SIZE, SIZE))
    disc = mask()
    ImageDraw.Draw(disc).ellipse((46, 46, 978, 978), fill=255)
    paste_shape(canvas, solid("#eddcbe"), disc)
    draw = ImageDraw.Draw(canvas)
    draw.ellipse((57, 57, 967, 967), fill="#1e1823")
    center, outer, inner = 512, 438, 193
    for i in range(18):
        start = i * 20 - 100
        wedge = mask()
        wd = ImageDraw.Draw(wedge)
        wd.pieslice((center - outer, center - outer, center + outer, center + outer),
                    start + .65, start + 19.35, fill=255)
        wd.ellipse((center - inner, center - inner, center + inner, center + inner), fill=0)
        bounds = wedge.getbbox()
        texture = solid("#17141c")
        texture.paste(ImageOps.fit(covers[i], (bounds[2] - bounds[0], bounds[3] - bounds[1])), bounds[:2])
        paste_shape(canvas, texture, wedge)
    # Fine grooves only add physical texture; the record silhouette does the work.
    detail = Image.new("RGBA", canvas.size)
    dd = ImageDraw.Draw(detail)
    for radius in range(209, 435, 14):
        dd.ellipse((512-radius, 512-radius, 512+radius, 512+radius), outline=(255, 237, 205, 34), width=2)
    canvas.alpha_composite(detail)
    draw = ImageDraw.Draw(canvas)
    draw.ellipse((326, 326, 698, 698), fill=warm)
    draw.pieslice((326, 326, 698, 698), 225, 405, fill="#f6ead3")
    draw.ellipse((430, 430, 594, 594), fill="#19151f")
    draw.ellipse((490, 490, 534, 534), fill="#f6ead3")
    return canvas


def curve(points, width):
    start, c1, c2, end = points
    samples = []
    for i in range(161):
        t = i / 160
        u = 1 - t
        samples.append(tuple(round(u**3 * start[a] + 3*u*u*t*c1[a] +
                                   3*u*t*t*c2[a] + t**3*end[a]) for a in (0, 1)))
    shape = mask()
    draw = ImageDraw.Draw(shape)
    draw.line(samples, fill=255, width=width, joint="curve")
    x, y = start
    draw.ellipse((x-width//2, y-width//2, x+width//2, y+width//2), fill=255)
    return shape


def ribbons(covers, warm, cool):
    """B: warm/cool album ribbons with an explicit over/under crossing."""
    canvas, tile = round_tile("#17131f")
    aura = mosaic(covers, 3, 42).filter(ImageFilter.GaussianBlur(105))
    aura = Image.blend(aura, solid("#14101d"), .74)
    paste_shape(canvas, aura, tile)
    down = curve(((208, 305), (420, 305), (574, 715), (755, 715)), 156)
    up = curve(((208, 715), (425, 715), (563, 305), (755, 305)), 156)
    for shape, y in ((down, 715), (up, 305)):
        ImageDraw.Draw(shape).polygon(((700, y-157), (912, y), (700, y+157)), fill=255)
    for shape, tint, offset in ((down, cool, 18), (up, warm, 36)):
        shadow(canvas, shape, (0, 17), 17, 200)
        # A broad luminous edge keeps cover-filled paths readable at 16px.
        paste_shape(canvas, solid(tint), shape)
        inset = shape.filter(ImageFilter.MinFilter(17))
        texture = Image.blend(mosaic(covers, 5, offset), solid(tint), .35)
        paste_shape(canvas, texture, inset)
    return canvas


def note(covers, warm, cool):
    """C: a single monumental cover-filled note with a dark extruded edge."""
    canvas, tile = round_tile("#f0e5cf")
    shape = mask()
    draw = ImageDraw.Draw(shape)
    draw.rounded_rectangle((480, 185, 634, 706), radius=24, fill=255)
    draw.ellipse((215, 586, 633, 847), fill=255)
    # The flag's generous negative space and the fat stem survive reduction.
    draw.polygon(((555, 185), (641, 185), (838, 328), (833, 485),
                  (739, 545), (747, 399), (579, 329)), fill=255)
    shadow(canvas, shape, (6, 26), 24, 70)
    extrusion = mask()
    for delta in range(36):
        extrusion = ImageChops.lighter(extrusion, ImageChops.offset(shape, delta, delta))
    paste_shape(canvas, solid("#211924"), extrusion)
    paste_shape(canvas, solid("#211924"), shape)
    inset = shape.filter(ImageFilter.MinFilter(23))
    art = mosaic(covers, 5, 47)
    art = Image.blend(art, solid(warm), .12)
    paste_shape(canvas, art, inset)
    return canvas


def comparison(images, output):
    sheet = Image.new("RGB", (1320, 690), "#edeae4")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=24)
    small = ImageFont.load_default(size=16)
    labels = ("A / DEEP CUTS", "B / CROSSFADE", "C / LINER NOTE")
    for i, (source, label) in enumerate(zip(images, labels)):
        x = 24 + i * 440
        draw.text((x, 20), label, font=font, fill="#201b28")
        preview = source.resize((384, 384), LANCZOS)
        sheet.paste(preview, (x+4, 66), preview)
        for j, bg in enumerate(("#24212b", "#ffffff")):
            y = 478 + j * 98
            draw.rounded_rectangle((x, y, x+392, y+88), 12, fill=bg)
            for size, dx in ((64, 18), (32, 119), (16, 215)):
                thumb = source.resize((size, size), LANCZOS)
                sheet.paste(thumb, (x+dx, y+(88-size)//2), thumb)
                draw.text((x+dx+size+8, y+36), str(size), font=small,
                          fill="#c3bbc9" if j == 0 else "#6d6472")
    sheet.save(output / "icon_candidates_comparison.png")


def verify(output):
    hashes = []
    for key in "abc":
        path = output / f"icon_candidate_{key}.png"
        with Image.open(path) as source:
            source.load()
            assert source.format == "PNG" and source.mode == "RGBA"
            assert source.size == (1024, 1024)
            assert source.getpixel((0, 0))[3] == 0
            hashes.append(hashlib.sha256(source.tobytes()).hexdigest())
            with Image.open(output / f"icon_candidate_{key}_64.png") as preview:
                preview.load()
                assert preview.format == "PNG" and preview.mode == "RGBA"
                assert preview.size == (64, 64)
                assert preview.tobytes() == source.resize((64, 64), LANCZOS).tobytes()
    assert len(set(hashes)) == 3, "Candidates must be distinct"
    print("PASS: three distinct RGBA 1024px PNGs; exact direct 64px downsample previews")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artwork", type=Path, default=ARTWORK)
    parser.add_argument("--output", type=Path, default=ROOT / "assets")
    parser.add_argument("--seed", type=int, default=271828)
    parser.add_argument("--count", type=int, default=72, choices=range(40, 81), metavar="40..80")
    args = parser.parse_args()
    artwork, output = args.artwork.resolve(), args.output.resolve()
    # Reject output locations that could write into the read-only collection.
    if output == artwork or artwork in output.parents:
        parser.error("Output must be outside the artwork folder")
    covers, records = sample_covers(artwork, args.count, args.seed)
    swatches = palette_from(covers)
    warm, cool = accents(swatches)
    output.mkdir(parents=True, exist_ok=True)
    images = [fn(covers, warm, cool) for fn in (record, ribbons, note)]
    for key, source in zip("abc", images):
        source.save(output / f"icon_candidate_{key}.png")
        source.resize((64, 64), LANCZOS).save(output / f"icon_candidate_{key}_64.png")
    comparison(images, output)
    manifest = {"seed": args.seed, "count": len(covers), "artwork_root": str(artwork),
                "palette": swatches, "derived_accents": {"warm": warm, "cool": cool},
                "covers": records}
    (output / "icon_candidates_sources.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    verify(output)
    print(f"Sampled {len(covers)} distinct covers. Derived warm {warm}, cool {cool}.")
    print(f"Saved candidates, comparison, and source manifest to {output}")


if __name__ == "__main__":
    main()
