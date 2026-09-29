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


# Shared 5-color scale (cool -> healthy -> warning -> hot -> critical), reused
# across metrics so the same color always means roughly the same "how worried
# should I be" - but the thresholds that trigger each color differ per metric,
# calibrated to what's actually normal for that specific kind of number.
_COOL = (90, 170, 250)
_GOOD = (110, 210, 120)
_WARN = (235, 210, 80)
_HOT = (240, 150, 60)
_CRIT = (235, 70, 70)


def _tier_color(value: float | None, bands):
    """bands: ascending [(upper_bound_exclusive, color), ..., (None, color)]."""
    if value is None:
        return MUTED_COLOR
    for upper, color in bands:
        if upper is None or value < upper:
            return color
    return bands[-1][1]


# Temperature bands per component - CPU/GPU/SSD have very different normal
# operating ranges, so one shared scale reads wrong for at least two of them.
#
# CPU: modern desktop CPUs (Intel/AMD) idle ~30-45C, run comfortably warm
# under sustained load into the 60-70s, and commonly throttle/TjMax around
# 95-100C - red leaves ~10C of headroom below that as an early warning.
_CPU_TEMP_BANDS = [(45, _COOL), (65, _GOOD), (78, _WARN), (90, _HOT), (None, _CRIT)]
# GPU: discrete GPUs are designed to run hotter than CPUs as part of normal
# boost behavior - NVIDIA's stock GPU Boost thermal target is ~83C on most
# cards, and typical throttle/hotspot risk starts around 90-95C.
_GPU_TEMP_BANDS = [(45, _COOL), (75, _GOOD), (83, _WARN), (90, _HOT), (None, _CRIT)]
# NVMe SSD: consumer drives idle ~30-40C; vendor warning/critical composite
# temperature thresholds (NVMe WCTEMP/CCTEMP) commonly sit around 70-85C, but
# this band runs more cautious than that spec ceiling - there's less thermal
# margin and less benefit to running hot, so the warning zone starts early.
_SSD_TEMP_BANDS = [(35, _COOL), (45, _GOOD), (55, _WARN), (65, _HOT), (None, _CRIT)]

# CPU/GPU load and disk I/O activity are instantaneous utilization - brief
# spikes to 100% are normal, so the danger zone starts high.
_LOAD_BANDS = [(25, _COOL), (50, _GOOD), (75, _WARN), (90, _HOT), (None, _CRIT)]
# RAM: common guidance is you don't want to be sitting near-full since that
# hurts caching/paging headroom - danger zone starts lower than raw CPU load.
_RAM_BANDS = [(40, _COOL), (65, _GOOD), (85, _WARN), (95, _HOT), (None, _CRIT)]
# Storage capacity: keeping meaningful free space matters for sustained SSD
# performance/wear-leveling - common tooling warns around 80%, critical ~95%.
_CAPACITY_BANDS = [(50, _COOL), (70, _GOOD), (85, _WARN), (95, _HOT), (None, _CRIT)]


def cpu_temp_color(celsius):
    return _tier_color(celsius, _CPU_TEMP_BANDS)


def gpu_temp_color(celsius):
    return _tier_color(celsius, _GPU_TEMP_BANDS)


def ssd_temp_color(celsius):
    return _tier_color(celsius, _SSD_TEMP_BANDS)


def cpu_load_color(pct):
    return _tier_color(pct, _LOAD_BANDS)


def gpu_load_color(pct):
    return _tier_color(pct, _LOAD_BANDS)


def ram_color(pct):
    return _tier_color(pct, _RAM_BANDS)


def ssd_capacity_color(pct):
    return _tier_color(pct, _CAPACITY_BANDS)


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


def draw_ring_metric(draw, cx, cy, radius, thickness, label, value_text, value_color, sub_text, fraction, ring_color, s):
    draw_gauge(draw, cx, cy, radius, thickness, fraction, ring_color)
    draw.text((cx, cy - radius - 13 * s), label, font=_load_font(12 * s), fill=MUTED_COLOR, anchor="mm")
    value_font = _fit_text(draw, value_text, radius * 1.2, start_size=29 * s, min_size=16 * s)
    draw.text((cx, cy - 9 * s), value_text, font=value_font, fill=value_color, anchor="mm")
    draw.text((cx, cy + 20 * s), sub_text, font=_load_font(11.5 * s), fill=ring_color, anchor="mm")


def draw_metric_row(draw, x0, x1, y_center, label, value_text, fraction, color, s, label_w=42, bar_h=7):
    label_w *= s
    bar_h *= s
    value_font = _fit_text(draw, value_text, (x1 - x0) * 0.6, start_size=12.5 * s, min_size=10 * s)
    text_w = draw.textbbox((0, 0), value_text, font=value_font)[2]
    draw.text((x0, y_center), label, font=_load_font(11.5 * s), fill=MUTED_COLOR, anchor="lm")
    bar_x0 = x0 + label_w
    bar_x1 = x1 - text_w - 8 * s
    draw_stat_bar(draw, bar_x0, y_center - bar_h / 2, bar_x1 - bar_x0, bar_h, fraction, color)
    draw.text((x1, y_center), value_text, font=value_font, fill=TEXT_COLOR, anchor="rm")


def draw_ssd_row(draw, x0, x1, y0, y1, s, temp_c, used_gb, total_gb, label_w=42, bar_h=11):
    label_w *= s
    bar_h *= s
    line1_y = y0 + (y1 - y0) * 0.28
    line2_y = y0 + (y1 - y0) * 0.74

    draw.text((x0, line1_y), "SSD", font=_load_font(11.5 * s), fill=MUTED_COLOR, anchor="lm")
    temp_text = f"{temp_c:.0f}°C" if temp_c is not None else "--°C"
    draw.text((x1, line1_y), temp_text, font=_load_font(11.5 * s), fill=ssd_temp_color(temp_c), anchor="rm")

    if total_gb:
        fraction = max(0.0, min(1.0, (used_gb or 0) / total_gb))
        used_tb, total_tb = used_gb / 1024.0, total_gb / 1024.0
        cap_text = f"{used_tb:.1f}/{total_tb:.1f} TB ({fraction * 100:.0f}%)"
    else:
        fraction = 0.0
        cap_text = "--/-- TB"

    cap_font = _fit_text(draw, cap_text, (x1 - x0) * 0.62, start_size=12 * s, min_size=9 * s)
    text_w = draw.textbbox((0, 0), cap_text, font=cap_font)[2]
    bar_x0 = x0 + label_w
    bar_x1 = x1 - text_w - 8 * s
    draw_stat_bar(draw, bar_x0, line2_y - bar_h / 2, bar_x1 - bar_x0, bar_h, fraction, ssd_capacity_color(fraction * 100))
    draw.text((x1, line2_y), cap_text, font=cap_font, fill=TEXT_COLOR, anchor="rm")


def render(stats: dict, width: int = LM360_W, height: int = LM360_H) -> Image.Image:
    """stats keys expected (all optional, missing -> shown as --):
    cpu_temp_c, cpu_load_pct, cpu_freq_ghz, gpu_temp_c, gpu_load_pct,
    ram_used_pct, ram_used_gb, ram_total_gb, ssd_temp_c, ssd_used_gb, ssd_total_gb
    """
    s = SUPERSAMPLE
    W, H = width * s, height * s
    img = _vertical_gradient(W, H, BG_TOP, BG_BOTTOM)
    draw = ImageDraw.Draw(img)

    margin = 10 * s
    gauge_radius = 42 * s
    gauge_thickness = 8 * s
    gauge_cy = 70 * s
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
        cpu_temp_color(cpu_temp),
        f"{cpu_load:.0f}% load" if cpu_load is not None else "-- load",
        (cpu_load or 0) / 100.0,
        cpu_load_color(cpu_load),
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
        gpu_temp_color(gpu_temp),
        f"{gpu_load:.0f}% load" if gpu_load is not None else "-- load",
        (gpu_load or 0) / 100.0,
        gpu_load_color(gpu_load),
        s,
    )

    panel_y0, panel_y1 = 120 * s, 230 * s
    draw_rounded_rect(draw, (margin, panel_y0, W - margin, panel_y1), radius=10 * s, fill=PANEL_COLOR)

    pad = 8 * s
    gap = 10 * s
    inner_x0, inner_x1 = margin + pad, W - margin - pad
    ram_h, ssd_h = 34 * s, 50 * s

    ram_y0 = panel_y0 + pad
    ssd_y0 = ram_y0 + ram_h + gap

    ram_used_gb = stats.get("ram_used_gb")
    ram_total_gb = stats.get("ram_total_gb")
    ram_pct = stats.get("ram_used_pct")
    if ram_pct is None and ram_used_gb is not None and ram_total_gb:
        ram_pct = ram_used_gb / ram_total_gb * 100.0
    if ram_used_gb is not None and ram_total_gb:
        ram_text = f"{ram_used_gb:.0f}/{ram_total_gb:.0f} GB ({ram_pct:.0f}%)"
    else:
        ram_text = f"{ram_pct:.0f}%" if ram_pct is not None else "--%"
    draw_metric_row(
        draw,
        inner_x0,
        inner_x1,
        ram_y0 + ram_h / 2,
        "RAM",
        ram_text,
        (ram_pct or 0) / 100.0,
        ram_color(ram_pct),
        s,
        bar_h=11,
    )

    draw_ssd_row(
        draw,
        inner_x0,
        inner_x1,
        ssd_y0,
        ssd_y0 + ssd_h,
        s,
        stats.get("ssd_temp_c"),
        stats.get("ssd_used_gb"),
        stats.get("ssd_total_gb"),
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
