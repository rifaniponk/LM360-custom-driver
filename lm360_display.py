"""USB transport for the DeepCool LM360 pump-cap LCD.

Protocol confirmed identical to the LM240 (same VID:PID, same init/frame/
brightness bytes) - cross-checked against daedlock/deepcool-lm, a third-party
MIT-licensed reverse-engineering project whose Linux driver was tested and
built specifically against real LM360 hardware.

Knows nothing about what's drawn - only moves bytes over USB. render.py is
called as a black box for the RGB565 conversion.
"""

from __future__ import annotations

import logging

import usb.core
import usb.util

import render

logger = logging.getLogger(__name__)

VENDOR_ID = 0x3633
PRODUCT_ID = 0x0026
INTERFACE = 0
ENDPOINT_OUT = 0x01

WIDTH, HEIGHT = render.LM360_W, render.LM360_H

INIT_CMD = bytes([0xAA, 0x01, 0x00, 0x09, 0x29, 0x91])
FRAME_HEADER = bytes(
    [0xAA, 0x08, 0x00, 0x00, 0x01, 0x00, 0x58, 0x02, 0x00, 0x2C, 0x01, 0xBC, 0x11]
)
BRIGHTNESS_UP_CMD = bytes([0xAA, 0x04, 0x00, 0x06, 0x03, 0x61, 0x00, 0xD2, 0x46])
BRIGHTNESS_DOWN_CMD = bytes([0xAA, 0x04, 0x00, 0x06, 0x03, 0x1D, 0x00, 0xE6, 0x0B])


class DeviceNotFoundError(RuntimeError):
    pass


def _get_backend():
    """Prefer the vendored libusb-package's bundled DLL; fall back to system discovery."""
    try:
        import libusb_package

        return libusb_package.get_libusb1_backend()
    except Exception:
        logger.debug("libusb-package backend unavailable, falling back to default discovery", exc_info=True)
        return None


class LM360Display:
    def __init__(self):
        self.dev: usb.core.Device | None = None

    def connect(self) -> None:
        backend = _get_backend()
        dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID, backend=backend)
        if dev is None:
            raise DeviceNotFoundError(f"LM360 not found (VID {VENDOR_ID:#06x} PID {PRODUCT_ID:#06x})")

        try:
            dev.set_configuration()
        except usb.core.USBError:
            logger.debug("set_configuration failed (often harmless if already configured)", exc_info=True)

        usb.util.claim_interface(dev, INTERFACE)

        self.dev = dev
        self._write(INIT_CMD)
        logger.info("Connected to LM360 display")

    def close(self) -> None:
        if self.dev is not None:
            try:
                usb.util.release_interface(self.dev, INTERFACE)
                usb.util.dispose_resources(self.dev)
            except usb.core.USBError:
                logger.debug("Error releasing USB interface", exc_info=True)
            self.dev = None

    def _write(self, data: bytes, timeout: int = 5000) -> None:
        if self.dev is None:
            raise DeviceNotFoundError("Display not connected")
        self.dev.write(ENDPOINT_OUT, data, timeout=timeout)

    def send_frame(self, image) -> None:
        """image: PIL.Image, any mode/size - resized/converted as needed."""
        if image.size != (WIDTH, HEIGHT):
            image = image.resize((WIDTH, HEIGHT))
        framebuffer = render.rgb_to_framebuffer(image)
        self._write(FRAME_HEADER)
        self._write(framebuffer)

    def brightness_up(self) -> None:
        self._write(BRIGHTNESS_UP_CMD)

    def brightness_down(self) -> None:
        self._write(BRIGHTNESS_DOWN_CMD)
