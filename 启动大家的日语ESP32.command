#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")"
python3 host/service.py install
python3 host/device.py ensure-start
python3 host/bridge.py --once || true
open "http://127.0.0.1:7865"
echo "已启动《大家的日语》ESP32 学习端与后台同步服务，并打开网页复读控制台。"
