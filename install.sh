#!/usr/bin/env bash
# Linux installer: venv + deps, udev rule for non-root USB access, and a
# systemd user service that starts the driver at login and restarts on failure.
# Run as your normal user (it calls sudo only for the udev rule).
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="lm360-display.service"
UDEV_RULE="/etc/udev/rules.d/70-lm360-display.rules"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

if [[ $EUID -eq 0 ]]; then
    echo "Run this as your normal user, not root (it installs a per-user service)." >&2
    exit 1
fi

echo "==> Creating venv and installing Python dependencies"
python3 -m venv "$DIR/.venv"
"$DIR/.venv/bin/pip" install --quiet --upgrade pip
"$DIR/.venv/bin/pip" install --quiet -r "$DIR/requirements.txt"

echo "==> Installing udev rule ($UDEV_RULE)"
sudo tee "$UDEV_RULE" >/dev/null <<'EOF'
# DeepCool LM-series pump-cap LCD - let the logged-in user / plugdev drive it
SUBSYSTEM=="usb", ATTR{idVendor}=="3633", ATTR{idProduct}=="0026", MODE="0660", GROUP="plugdev", TAG+="uaccess"
EOF
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=usb --attr-match=idVendor=3633 --attr-match=idProduct=0026

echo "==> Installing systemd user service ($UNIT_DIR/$SERVICE_NAME)"
mkdir -p "$UNIT_DIR"
cat >"$UNIT_DIR/$SERVICE_NAME" <<EOF
[Unit]
Description=LM360 custom display driver
After=graphical-session.target

[Service]
ExecStart=$DIR/.venv/bin/python $DIR/pc_display.py
WorkingDirectory=$DIR
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now "$SERVICE_NAME"
systemctl --user restart "$SERVICE_NAME"

echo
echo "Done. Status:  systemctl --user status $SERVICE_NAME"
echo "      Logs:    journalctl --user -u $SERVICE_NAME -f"
echo "               ${XDG_STATE_HOME:-$HOME/.local/state}/LM360Driver/logs/pc_display.log"
