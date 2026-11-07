#!/usr/bin/env bash
# MetaSync installer for Ubuntu / Debian
set -e
echo "==> Installing system packages (python3-tk, rsync, curl, unzip)..."
sudo apt update
sudo apt install -y python3 python3-tk rsync curl unzip openssh-client
if ! command -v rclone >/dev/null; then
  echo "==> Installing rclone..."
  curl -fsSL https://rclone.org/install.sh | sudo bash
fi
DIR="$(cd "$(dirname "$0")" && pwd)"
chmod +x "$DIR/metasync_gui.py" "$DIR"/scripts/*.sh
echo "==> Adding MetaSync to the application menu..."
/usr/bin/python3 "$DIR/metasync_gui.py" --install
echo
echo "Done. Open 'MetaSync' from the app menu, or run:"
echo "  /usr/bin/python3 $DIR/metasync_gui.py"
