#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")"
python3 host/device.py switch-to-badge
echo "已将 ESP32 屏幕切回 AI Mac mini 状态牌。"
