#!/usr/bin/env bash
# Removes the systemd user service and the udev rule. Leaves the venv in place
# (delete .venv/ yourself if you want it gone).
set -euo pipefail

SERVICE_NAME="lm360-display.service"
UDEV_RULE="/etc/udev/rules.d/70-lm360-display.rules"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

systemctl --user disable --now "$SERVICE_NAME" 2>/dev/null || true
rm -f "$UNIT_DIR/$SERVICE_NAME"
systemctl --user daemon-reload

if [[ -f "$UDEV_RULE" ]]; then
    sudo rm -f "$UDEV_RULE"
    sudo udevadm control --reload-rules
fi

echo "LM360 display driver uninstalled."
