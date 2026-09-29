# LM360 Custom Display Driver

A small, lightweight replacement for DeepCool's factory app - just to drive the LCD on a
**DeepCool LM360** AIO pump cap with live CPU/GPU/RAM/SSD stats, without running the full vendor
software stack in the background.

![Resolution](https://img.shields.io/badge/Resolution-320x240-blue)
![Platform](https://img.shields.io/badge/Platform-Windows-informational)

## What it shows

- **CPU** and **GPU**: radial gauges - arc length is load %, arc color escalates through
  cool/green/yellow/orange/red as load rises, with the temperature as a smaller centered number
  (colored by its own temperature scale)
- **RAM**: used/total (`51/128 GB (40%)`) with a color-tiered bar
- **SSD**: used/total capacity (`1.6/1.8 TB (89%)`) with a color-tiered bar, plus temperature as a
  plain colored number alongside

Bar/arc colors are tiered per metric with thresholds calibrated to what's actually normal for that
metric - CPU/GPU load leaves headroom for ordinary spikes, RAM and storage capacity warn earlier
since running low on headroom there matters more.

## Hardware

Targets the LM-series pump-cap LCD (USB VID:PID `3633:0026`, 320x240 RGB565, USB bulk transfer).
Confirmed working live on a DeepCool LM360. The protocol was cross-checked against
[daedlock/deepcool-lm](https://github.com/daedlock/deepcool-lm), a third-party MIT-licensed
reverse-engineering project - its Linux driver is tested primarily against real LM360 hardware,
and its documented init/frame/brightness command bytes matched this project's exactly.

## Requirements

- Windows 10/11
- Python 3.10+
- [PawnIO](https://github.com/namazso/PawnIO) kernel driver (for CPU temperature/clock sensors via
  LibreHardwareMonitorLib) - `install.ps1` installs this for you
- The driver process must run **elevated** to read CPU temperature/clock (ring0 MSR access) -
  `install.ps1` registers it that way automatically; without elevation, CPU load/GPU temp+load
  still work, only CPU temp/clock come back blank

## Install

Right-click `Install.cmd` and run as administrator (or run `install.ps1` from an elevated
PowerShell prompt). This:

1. Installs the Python dependencies (`requirements.txt`)
2. Installs the PawnIO kernel driver
3. Disables DeepCool's factory app autostart, where it can find it
4. Registers a hidden, highest-privilege Task Scheduler task (`LM360CustomDisplay`) that starts
   the driver at logon and restarts it on failure

## Uninstall

Right-click `Uninstall.cmd` and run as administrator. Removes the scheduled task and stops any
running instance. Leaves PawnIO and the pip packages installed since other tools may depend on
them.

## Architecture

Transport and rendering are fully decoupled - `render.py` never touches USB, and the USB layer
never touches Pillow beyond calling into `render.py`'s RGB565 conversion as a black box.

| File | Responsibility |
|---|---|
| `render.py` | Pure Pillow drawing - takes plain sensor dicts in, returns a PIL `Image` out. OS/hardware-independent. |
| `lm360_display.py` | USB transport for the LM360 panel (connect, init, send frames, brightness) via `pyusb` + `libusb-package`. |
| `sensors_windows.py` | CPU/GPU/RAM/storage temp via LibreHardwareMonitorLib (pythonnet), storage capacity via `shutil`, integrated-GPU load fallback via Windows PDH. |
| `pc_display.py` | Main loop - sensors -> render -> transport, ~1fps, with reconnect-on-failure and rotating file logging. |
| `libs/` | Vendored LibreHardwareMonitorLib.dll and its .NET Framework dependency DLLs (from the [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) release, MIT). |

## Credits

- [daedlock/deepcool-lm](https://github.com/daedlock/deepcool-lm) - protocol reference (MIT)
- [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) - hardware
  sensor library (MIT), vendored in `libs/`
- [PawnIO](https://github.com/namazso/PawnIO) - kernel driver used for CPU MSR access
