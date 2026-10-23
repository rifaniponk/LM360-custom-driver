"""Main loop: sensors -> render -> LM360 transport.

Runs at ~1fps with reconnect-on-failure so it survives the AIO being
unplugged/replugged or a transient USB error. Logs to a rotating file (in
addition to the console when attached to one) since this runs headless under
Task Scheduler most of the time.
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import signal
import sys
import time

import render
import sensors_windows
from PIL import Image

from lm360_display import DeviceNotFoundError, LM360Display

LOG_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.dirname(os.path.abspath(__file__))), "LM360Driver", "logs")
LOG_FILE = os.path.join(LOG_DIR, "pc_display.log")

FRAME_INTERVAL_SEC = 1.0
RECONNECT_BACKOFF_SEC = 5.0

logger = logging.getLogger("pc_display")

_shutdown = False


def _setup_logging() -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    file_handler = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=3)
    file_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)

    if sys.stdout is not None and sys.stdout.isatty():
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(fmt)
        root.addHandler(console_handler)


def _handle_signal(signum, _frame) -> None:
    global _shutdown
    logger.info("Received signal %s, shutting down", signum)
    _shutdown = True


def _wait_for_device(display: LM360Display) -> None:
    while not _shutdown:
        try:
            display.connect()
            return
        except DeviceNotFoundError:
            logger.warning("LM360 not found, retrying in %ss", RECONNECT_BACKOFF_SEC)
            time.sleep(RECONNECT_BACKOFF_SEC)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Drive the DeepCool LM360 LCD with PC stats.")
    # The panel is 320x240, so only 180 keeps the layout fitting; 90/270 would need a portrait layout.
    parser.add_argument("--rotate", type=int, choices=(0, 180), default=0,
                        help="Rotate the rendered view by this many degrees (for upside-down AIO mounting)")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    _setup_logging()
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    logger.info("Starting pc_display (rotate=%s)", args.rotate)

    reader = sensors_windows.SensorReader()
    display = LM360Display()

    try:
        _wait_for_device(display)

        while not _shutdown:
            loop_start = time.monotonic()
            try:
                stats = reader.read()
                image = render.render(stats)
                if args.rotate == 180:
                    image = image.transpose(Image.Transpose.ROTATE_180)
                display.send_frame(image)
            except Exception:
                logger.exception("Frame update failed, reconnecting")
                display.close()
                _wait_for_device(display)

            elapsed = time.monotonic() - loop_start
            time.sleep(max(0.0, FRAME_INTERVAL_SEC - elapsed))
    finally:
        display.close()
        reader.close()
        logger.info("pc_display stopped")


if __name__ == "__main__":
    main()
