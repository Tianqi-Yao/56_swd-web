#!/usr/bin/env bash
# 启动 Firebase Emulator，跑 edge/ 的端到端测试，结束后自动关掉 Emulator。
# 用法：conda activate swd-web && bash scripts/e2e.sh
set -euo pipefail
cd "$(dirname "$0")/.."
npx firebase emulators:exec --only auth,database,storage "cd edge && pytest -m emulator -v"
