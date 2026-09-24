#!/usr/bin/env bash
# Install ParkWatch on a Raspberry Pi (Raspberry Pi OS 64-bit) as a service that
# starts on boot. Run it from the parkwatch folder as your normal user:
#
#   ./pi/install.sh
#
# Re-run it any time to update after copying over new code.
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUN_USER="$(id -un)"
PORT="${PORT:-8000}"
SERVICE=/etc/systemd/system/parkwatch.service

say() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33mWarning:\033[0m %s\n' "$*"; }

if [ "$(id -u)" -eq 0 ]; then
  echo "Run this as your normal user (not with sudo); it will ask for sudo when needed." >&2
  exit 1
fi
if [ "$(uname -s)" != "Linux" ]; then
  echo "This installer is for Raspberry Pi OS / Linux. On a Mac or PC, use ./run.sh instead." >&2
  exit 1
fi
if [ "$(uname -m)" != "aarch64" ]; then
  warn "This isn't a 64-bit OS ($(uname -m)). OpenCV has no ready-made package for it, so"
  warn "installation may fail or take hours. Reinstall with Raspberry Pi OS (64-bit) if so."
fi

say "Installing system packages"
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-venv python3-pip curl >/dev/null
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
  echo "ParkWatch needs Python 3.10+, but this system has $(python3 --version)." >&2
  echo "Please install the current Raspberry Pi OS (64-bit) and try again." >&2
  exit 1
fi

say "Creating Python environment in $APP_DIR/.venv"
if [ -d "$APP_DIR/.venv" ] && ! "$APP_DIR/.venv/bin/python" -c "" 2>/dev/null; then
  rm -rf "$APP_DIR/.venv"   # e.g. copied over from a Mac, which won't run here
fi
[ -d "$APP_DIR/.venv" ] || python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

say "Setting up the parkwatch service"
sudo tee "$SERVICE" >/dev/null <<EOF
[Unit]
Description=ParkWatch parking space monitor
After=network-online.target
Wants=network-online.target

[Service]
User=$RUN_USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port $PORT
Restart=always
RestartSec=5
# Keep OpenCV/numpy from grabbing every core so the Pi stays responsive.
Environment=OMP_NUM_THREADS=2
Environment=OPENCV_FFMPEG_CAPTURE_OPTIONS=rtsp_transport;tcp

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now parkwatch >/dev/null 2>&1
sudo systemctl restart parkwatch

say "Checking it started"
for _ in $(seq 1 30); do
  if curl -fs "http://127.0.0.1:$PORT/api/info" >/dev/null; then
    IP="$(hostname -I | awk '{print $1}')"
    printf '\n\033[1;32mParkWatch is running.\033[0m Open it from any device on your network:\n'
    printf '  http://%s.local:%s\n  http://%s:%s\n\n' "$(hostname)" "$PORT" "$IP" "$PORT"
    echo "It starts automatically when the Pi boots."
    echo "Logs:     journalctl -u parkwatch -f"
    echo "Restart:  sudo systemctl restart parkwatch"
    exit 0
  fi
  sleep 1
done
echo "ParkWatch didn't respond. Recent logs:" >&2
sudo journalctl -u parkwatch -n 30 --no-pager >&2
exit 1
