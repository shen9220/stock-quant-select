#!/bin/bash
# 打开 Amphetamine 界面（菜单栏图标 → 主窗口）
set -euo pipefail
open -a Amphetamine
sleep 1
osascript <<'APPLESCRIPT' || true
tell application "Amphetamine"
    activate
    reopen
end tell
delay 0.5
tell application "System Events"
    if exists process "Amphetamine" then
        tell process "Amphetamine" to set frontmost to true
    end if
end tell
APPLESCRIPT
echo "Amphetamine 已打开。若看不到窗口，请看屏幕右上角菜单栏闪电/药丸图标。"
