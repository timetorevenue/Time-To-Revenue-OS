$ErrorActionPreference = "Stop"
$root = "C:\ttros-core"
$bridgeDir = Join-Path $root "connectors\telegram_bridge"

Get-CimInstance Win32_Process |
  Where-Object {
    $_.CommandLine -and
    $_.ProcessId -ne $PID -and
    ($_.CommandLine -like "*telegram_bridge.py*")
  } |
  ForEach-Object {
    try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch {}
  }

Set-Location $bridgeDir
wsl.exe -d AgenticOSClean -- bash -lc 'cd "/var/lib/ttros/legacy-snapshot/connectors/telegram_bridge" && python3 telegram_bridge.py'
