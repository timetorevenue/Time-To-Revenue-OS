$root = "C:\ttros-core"
Write-Host "Telegram bridge running. Leave this window open."
wsl.exe -d AgenticOSClean -- bash -lc "pkill -f '[t]elegram_bridge.py' 2>/dev/null || true; cd '/var/lib/ttros/legacy-snapshot' && exec python3 -u connectors/telegram_bridge/telegram_bridge.py"
