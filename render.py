"""Pure Pillow rendering for the LM360 pump-cap display.

Knows nothing about USB or Windows sensor APIs - takes plain dicts of sensor
values in, returns a PIL Image out. Transport modules call rgb_to_framebuffer()
to get bytes ready to send over the wire.

Drawn at SUPERSAMPLE x resolution and downscaled with LANCZOS at the end -
this small low-DPI panel makes circles/text look noticeably smoother that way
for a negligible cost at 1fps.
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw, ImageFont

LM360_W, LM360_H = 320, 240

SUPERSAMPLE = 3

BG_TOP = (16, 17, 23)
BG_BOTTOM = (27, 25, 35)
PANEL_COLOR = (35, 36, 45)
RING_TRACK = (52, 53, 62)
BAR_TRACK_COLOR = (52, 53, 62)
TEXT_COLOR = (235, 235, 240)
MUTED_COLOR = (150, 150, 160)
LOAD_COLOR = (120, 160, 245)

_FONT_CANDIDATES = [
    "segoeui.ttf",
    "arial.ttf",
    "C:\\Windows\\Fonts\\segoeui.ttf",
    "C:\\Windows\\Fonts\\arial.ttf",
]

_font_cache: dict[int, ImageFont.ImageFont] = {}


def _load_font(size: float) -> ImageFont.ImageFont:
    size = max(1, int(round(size)))
    if size in _font_cache:
        return _font_cache[size]
    for candidate in _FONT_CANDIDATES:
        try:
            font = ImageFont.truetype(candidate, size)
            _font_cache[size] = font
            return font
        except OSError:
            continue
    font = ImageFont.load_default()
    _font_cache[size] = font
    return font


def _fit_text(draw: ImageDraw.ImageDraw, text: str, max_width: float, start_size: float, min_size: float = 16):
    """Shrink font size until text fits max_width, floor at min_size."""
    size = start_size
    while size > min_size:
        font = _load_font(size)
        bbox = draw.textbbox((0, 0), text, font=font)
        if bbox[2] - bbox[0] <= max_width:
            return font
        size -= max(1, start_size // 20)
    return _load_font(min_size)


def temp_color(celsius: float | None) -> tuple[int, int, int]:
    if celsius is None:
        return MUTED_COLOR
    if celsius < 40:
        return (90, 170, 250)
    if celsius < 60:
        return (110, 210, 120)
    if celsius < 75:
        return (235, 210, 80)
    if celsius < 85:
        return (240, 150, 60)
    return (235, 70, 70)


def _vertical_gradient(w: int, h: int, top, bottom) -> Image.Image:
    ys = np.linspace(0.0, 1.0, h, dtype=np.float32).reshape(h, 1, 1)
    top_arr = np.array(top, dtype=np.float32).reshape(1, 1, 3)
    bottom_arr = np.array(bottom, dtype=np.float32).reshape(1, 1, 3)
    grad = top_arr + (bottom_arr - top_arr) * ys
    grad = np.repeat(grad.astype(np.uint8), w, axis=1)
    return Image.fromarray(grad, "RGB")


def draw_rounded_rect(draw: ImageDraw.ImageDraw, box, radius: float, fill=None, outline=None, width: int = 1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def draw_stat_bar(draw: ImageDraw.ImageDraw, x: float, y: float, w: float, h: float, fraction: float, color):
    """Rounded-rect progress bar. fraction is clamped to [0, 1]."""
    fraction = max(0.0, min(1.0, fraction))
    draw_rounded_rect(draw, (x, y, x + w, y + h), radius=h / 2, fill=BAR_TRACK_COLOR)
    fill_w = w * fraction
    if fill_w > h:
        draw_rounded_rect(draw, (x, y, x + fill_w, y + h), radius=h / 2, fill=color)
    elif fill_w > 0:
        draw.ellipse((x, y, x + h, y + h), fill=color)


def draw_gauge(draw: ImageDraw.ImageDraw, cx, cy, radius, thickness, fraction, color, start=135, end=405):
    """Speedometer-style ring: 270deg sweep with a gap at the bottom, fills clockwise."""
    bbox = (cx - radius, cy - radius, cx + radius, cy + radius)
    draw.arc(bbox, start, end, fill=RING_TRACK, width=thickness)
    fraction = max(0.0, min(1.0, fraction))
    if fraction > 0.003:
        draw.arc(bbox, start, start + (end - start) * fraction, fill=color, width=thickness)


def draw_ring_metric(draw, cx, cy, radius, thickness, label, value_text, sub_text, fraction, color, s):
    draw_gauge(draw, cx, cy, radius, thickness, fraction, color)
    draw.text((cx, cy - radius - 13 * s), label, font=_load_font(12 * s), fill=MUTED_COLOR, anchor="mm")
    value_font = _fit_text(draw, value_text, radius * 1.5, start_size=38 * s, min_size=18 * s)
    draw.text((cx, cy - 5 * s), value_text, font=value_font, fill=TEXT_COLOR, anchor="mm")
    draw.text((cx, cy + 16 * s), sub_text, font=_load_font(11.5 * s), fill=color, anchor="mm")


def draw_metric_row(draw, x0, x1, y_center, label, value_text, fraction, color, s, label_w=42, value_w=50, bar_h=7):
    label_w *= s
    value_w *= s
    bar_h *= s
    draw.text((x0, y_center), label, font=_load_font(11.5 * s), fill=MUTED_COLOR, anchor="lm")
    bar_x0 = x0 + label_w
    bar_x1 = x1 - value_w
    draw_stat_bar(draw, bar_x0, y_center - bar_h / 2, bar_x1 - bar_x0, bar_h, fraction, color)
    draw.text((x1, y_center), value_text, font=_load_font(12.5 * s), fill=TEXT_COLOR, anchor="rm")


def _temp_fraction(celsius, lo=30.0, hi=70.0):
    if celsius is None:
        return 0.0
    return max(0.0, min(1.0, (celsius - lo) / (hi - lo)))


def render(stats: dict, width: int = LM360_W, height: int = LM360_H) -> Image.Image:
    """stats keys expected (all optional, missing -> shown as --):
    cpu_temp_c, cpu_load_pct, cpu_freq_ghz, gpu_temp_c, gpu_load_pct,
    ram_used_pct, ssd_temp_c, disk_busy_pct
    """
    s = SUPERSAMPLE
    W, H = width * s, height * s
    img = _vertical_gradient(W, H, BG_TOP, BG_BOTTOM)
    draw = ImageDraw.Draw(img)

    margin = 10 * s
    gauge_radius = 45 * s
    gauge_thickness = 9 * s
    gauge_cy = 80 * s
    gauge_cx_left = 92 * s
    gauge_cx_right = W - 92 * s

    cpu_temp = stats.get("cpu_temp_c")
    cpu_load = stats.get("cpu_load_pct")
    draw_ring_metric(
        draw,
        gauge_cx_left,
        gauge_cy,
        gauge_radius,
        gauge_thickness,
        "CPU",
        f"{cpu_temp:.0f}\u00b0" if cpu_temp is not None else "--\u00b0",
        f"{cpu_load:.0f}% load" if cpu_load is not None else "-- load",
        (cpu_load or 0) / 100.0,
        temp_color(cpu_temp),
        s,
    )

    gpu_temp = stats.get("gpu_temp_c")
    gpu_load = stats.get("gpu_load_pct")
    draw_ring_metric(
        draw,
        gauge_cx_right,
        gauge_cy,
        gauge_radius,
        gauge_thickness,
        "GPU",
        f"{gpu_temp:.0f}\u00b0" if gpu_temp is not None else "--\u00b0",
        f"{gpu_load:.0f}% load" if gpu_load is not None else "-- load",
        (gpu_load or 0) / 100.0,
        temp_color(gpu_temp),
        s,
    )

    panel_y0, panel_y1 = 144 * s, 232 * s
    draw_rounded_rect(draw, (margin, panel_y0, W - margin, panel_y1), radius=10 * s, fill=PANEL_COLOR)

    pad = 12 * s
    inner_x0, inner_x1 = margin + pad, W - margin - pad
    content_top, content_bottom = panel_y0 + pad, panel_y1 - pad
    row_spacing = (content_bottom - content_top) / 3
    row_ys = [content_top + row_spacing * (i + 0.5) for i in range(3)]

    ram_pct = stats.get("ram_used_pct")
    draw_metric_row(
        draw,
        inner_x0,
        inner_x1,
        row_ys[0],
        "RAM",
        f"{ram_pct:.0f}%" if ram_pct is not None else "--%",
        (ram_pct or 0) / 100.0,
        LOAD_COLOR,
        s,
    )

    ssd_temp = stats.get("ssd_temp_c")
    draw_metric_row(
        draw,
        inner_x0,
        inner_x1,
        row_ys[1],
        "SSD",
        f"{ssd_temp:.0f}\u00b0C" if ssd_temp is not None else "--\u00b0C",
        _temp_fraction(ssd_temp),
        temp_color(ssd_temp),
        s,
    )

    disk_pct = stats.get("disk_busy_pct")
    draw_metric_row(
        draw,
        inner_x0,
        inner_x1,
        row_ys[2],
        "DISK",
        f"{disk_pct:.0f}%" if disk_pct is not None else "--%",
        (disk_pct or 0) / 100.0,
        LOAD_COLOR,
        s,
    )

    return img.resize((width, height), Image.LANCZOS).convert("RGB")


def rgb_to_framebuffer(image: Image.Image) -> bytes:
    """Convert a PIL image to RGB565 little-endian bytes."""
    img = image.convert("RGB")
    arr = np.asarray(img, dtype=np.uint32)
    r = (arr[:, :, 0] >> 3) << 11
    g = (arr[:, :, 1] >> 2) << 5
    b = arr[:, :, 2] >> 3
    rgb565 = (r | g | b).astype("<u2")
    return rgb565.tobytes()
