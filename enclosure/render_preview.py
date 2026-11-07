#!/usr/bin/env python3
"""Render a dependency-light v3 preview sheet from the validated binary STLs."""

from __future__ import annotations

import math
import struct
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent
STL_DIR = ROOT / "stl-v3"
OUTPUT = ROOT / "previews" / "v3_parts.png"


def load_binary_stl(filename: str) -> np.ndarray:
    data = (STL_DIR / filename).read_bytes()
    count = struct.unpack_from("<I", data, 80)[0]
    triangles = np.empty((count, 3, 3), dtype=np.float64)
    for index in range(count):
        offset = 84 + index * 50 + 12
        triangles[index] = [
            struct.unpack_from("<fff", data, offset + vertex * 12)
            for vertex in range(3)
        ]
    return triangles


def moved(
    triangles: np.ndarray,
    translation: tuple[float, float, float] = (0, 0, 0),
    matrix: np.ndarray | None = None,
) -> np.ndarray:
    transformed = triangles if matrix is None else triangles @ matrix.T
    return transformed + np.asarray(translation)


def render_meshes(
    meshes: list[tuple[np.ndarray, tuple[int, int, int]]],
    size: tuple[int, int],
    azimuth: float,
    elevation: float,
) -> Image.Image:
    width, height = size
    az = math.radians(azimuth)
    el = math.radians(elevation)
    view = np.array(
        [math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el)]
    )
    right = np.array([-math.sin(az), math.cos(az), 0.0])
    up = np.cross(view, right)

    all_triangles = np.concatenate([mesh[0] for mesh in meshes])
    centre = (all_triangles.min(axis=(0, 1)) + all_triangles.max(axis=(0, 1))) / 2
    all_triangles = all_triangles - centre
    counts = [len(mesh[0]) for mesh in meshes]
    colors = np.concatenate(
        [np.tile(np.asarray(color), (count, 1)) for count, (_, color) in zip(counts, meshes)]
    )

    px = np.tensordot(all_triangles, right, axes=([2], [0]))
    py = np.tensordot(all_triangles, up, axes=([2], [0]))
    depth = np.tensordot(all_triangles, view, axes=([2], [0])).mean(axis=1)
    span_x = max(1.0, float(px.max() - px.min()))
    span_y = max(1.0, float(py.max() - py.min()))
    scale = min((width - 34) / span_x, (height - 34) / span_y)
    sx = (px - (px.max() + px.min()) / 2) * scale + width / 2
    sy = -(py - (py.max() + py.min()) / 2) * scale + height / 2

    normals = np.cross(all_triangles[:, 1] - all_triangles[:, 0],
                       all_triangles[:, 2] - all_triangles[:, 0])
    lengths = np.linalg.norm(normals, axis=1)
    lengths[lengths == 0] = 1
    normals /= lengths[:, None]
    light = np.array([0.25, -0.35, 0.90])
    light /= np.linalg.norm(light)
    shade = 0.34 + 0.66 * np.abs(normals @ light)

    image = Image.new("RGB", size, (13, 19, 24))
    draw = ImageDraw.Draw(image)
    for index in np.argsort(depth):
        points = [(float(sx[index, vertex]), float(sy[index, vertex])) for vertex in range(3)]
        face = tuple(int(min(255, channel * shade[index])) for channel in colors[index])
        draw.polygon(points, fill=face)
    return image


def font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold
             else "/System/Library/Fonts/Supplemental/Arial.ttf"),
        Path("/System/Library/Fonts/Helvetica.ttc"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def panel(
    canvas: Image.Image,
    box: tuple[int, int, int, int],
    title: str,
    subtitle: str,
    meshes: list[tuple[np.ndarray, tuple[int, int, int]]],
    view: tuple[float, float],
) -> None:
    draw = ImageDraw.Draw(canvas)
    x0, y0, x1, y1 = box
    draw.rounded_rectangle(box, radius=18, fill=(15, 23, 29),
                           outline=(40, 55, 64), width=2)
    draw.text((x0 + 22, y0 + 16), title, font=font(24, True), fill=(229, 238, 243))
    draw.text((x0 + 22, y0 + 50), subtitle, font=font(17), fill=(139, 164, 177))
    rendered = render_meshes(meshes, (x1 - x0 - 24, y1 - y0 - 90), *view)
    canvas.paste(rendered, (x0 + 12, y0 + 78))


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    front = load_binary_stl("front_chassis_v3.stl")
    retainer = load_binary_stl("display_retainer_low_profile.stl")
    carrier = load_binary_stl("electronics_carrier_photo_corrected.stl")
    rear = load_binary_stl("rear_cap_compact.stl")
    rotate_x_180 = np.diag([1.0, -1.0, -1.0])

    exploded = [
        (front, (52, 145, 181)),
        (moved(retainer, (0, 0, 17.0)), (121, 177, 197)),
        (moved(carrier, (0, 0, 38.0)), (209, 164, 74)),
        (moved(rear, (0, 0, 79.0), rotate_x_180), (55, 82, 98)),
    ]
    shells = [
        (moved(front, (-112, 0, 0)), (53, 151, 190)),
        (moved(rear, (112, 0, 0)), (61, 91, 108)),
    ]
    internals = [
        (moved(retainer, (-108, 0, 0)), (127, 183, 205)),
        (moved(carrier, (104, 0, 0)), (216, 169, 73)),
    ]
    checks = [
        (moved(load_binary_stl("closure_coupon_front.stl"), (-120, 32, 0)),
         (90, 166, 190)),
        (moved(load_binary_stl("closure_coupon_rear.stl"), (-82, 32, 0)),
         (68, 111, 130)),
        (moved(load_binary_stl("seam_draw_key_loose.stl"), (-120, -10, 0)),
         (188, 203, 210)),
        (moved(load_binary_stl("seam_draw_key_nominal.stl"), (-88, -10, 0)),
         (222, 174, 82)),
        (moved(load_binary_stl("seam_draw_key_snug.stl"), (-56, -10, 0)),
         (198, 130, 84)),
        (moved(load_binary_stl("io_wall_coupon.stl"), (20, 32, 0)),
         (85, 161, 181)),
        (moved(load_binary_stl("io_bezel_hood.stl"), (120, 32, 0)),
         (86, 117, 133)),
        (moved(load_binary_stl("io_slider_universal.stl"), (20, -30, 0)),
         (219, 174, 83)),
        (moved(load_binary_stl("io_backer.stl"), (132, -30, 0)),
         (177, 190, 197)),
    ]

    canvas = Image.new("RGB", (1800, 1230), (8, 12, 16))
    draw = ImageDraw.Draw(canvas)
    draw.text((54, 34), "PiRouter SnapShell v3", font=font(45, True),
              fill=(236, 243, 246))
    draw.text((56, 91),
              "photo-corrected · positive-lock draw-keys · adjustable I/O · support-free",
              font=font(23), fill=(113, 193, 220))

    panel(canvas, (45, 145, 885, 625), "Exploded architecture",
          "latched 2.4 mm retainer → SSD bay → corrected carrier → compact rear",
          exploded, (-38, 25))
    panel(canvas, (915, 145, 1755, 625), "Bed-oriented chassis shells",
          "204 × 128 × 62.6 mm body · rear-only seam-open I/O bay",
          shells, (-34, 28))
    panel(canvas, (45, 655, 885, 1135), "Replaceable structural internals",
          "no SSD towers · Pi keyed to the NU08's photo-verified left end",
          internals, (25, 30))
    panel(canvas, (915, 655, 1755, 1135), "Print-before-the-shell checks",
          "draw-key coupon + three fits · I/O wall + hood + floating mask/backer",
          checks, (-30, 48))

    draw.text((56, 1170),
              "Every exported STL: 1 connected shell · 0 boundary edges · 0 non-manifold edges · 0 degenerate triangles",
              font=font(21, True), fill=(137, 215, 177))
    canvas.save(OUTPUT)
    print(OUTPUT)


if __name__ == "__main__":
    main()
