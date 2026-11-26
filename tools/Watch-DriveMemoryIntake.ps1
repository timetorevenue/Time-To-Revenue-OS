# Revisit: when DriveFS mount or the WSL staging contract changes. Created 2026-09-23.
# Windows only reads DriveFS and copies stable bytes to AgenticOSClean.
param(
    [switch]$Ingest,
    [switch]$Once,
    [string]$FileName,
    [ValidateRange(5, 3600)][int]$PollSeconds = 20,
    # Bounded retry: a file that fails is retried with exponential backoff, at most $MaxAttempts times
    # for the same file identity, then parked until the file changes or the state file is cleared.
    [ValidateRange(1, 10)][int]$MaxAttempts = 4,
    [ValidateRange(10, 3600)][int]$BackoffBaseSeconds = 60,
    [ValidateRange(60, 86400)][int]$BackoffCapSeconds = 3600
)

$ErrorActionPreference = 'Stop'
$Inbox = 'G:\My Drive\TTROS Memory Intake\Inbox'
$WslRoot = '\\wsl.localhost\AgenticOSClean\home\casey\agentic-os-live'
$Staging = Join-Path $WslRoot 'queue\inbox\staging\drive'
$Helper = '/opt/ttros-core/tools/drive_memory_intake.py'
$Python = '/opt/ttros-core/.venv/bin/python'
$Supported = @('.md', '.txt', '.docx', '.pdf')
$Seen = @{}
$Completed = @{}
$Passes = 0
# Persisted so a restart cannot reset the retry budget. Key: full path + size:mtime identity.
$StatePath = Join-Path $PSScriptRoot 'drive_intake_retry_state.json'
$Retry = @{}
if (Test-Path -LiteralPath $StatePath) {
    try {
        (Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json).PSObject.Properties |
            ForEach-Object { $Retry[$_.Name] = @{ Attempts = [int]$_.Value.Attempts; NextTry = [string]$_.Value.NextTry; Parked = [bool]$_.Value.Parked; Reason = [string]$_.Value.Reason } }
    } catch { Write-Warning "Retry state unreadable; starting empty: $($_.Exception.Message)" }
}
function Save-RetryState {
    $temporary = "$StatePath.tmp"
    ($Retry | ConvertTo-Json -Depth 4) | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $StatePath -Force
}

function Invoke-IntakeHelper([string]$Action, [string]$Digest, [string]$Suffix, [string]$Name) {
    $encodedName = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Name))
    $output = & wsl.exe -d AgenticOSClean -u casey -- $Python $Helper $Action --sha256 $Digest --suffix $Suffix --name-b64 $encodedName
    $exit = $LASTEXITCODE
    $parsed = $null
    try { $parsed = ($output | Select-Object -Last 1 | ConvertFrom-Json) } catch { }
    if ($exit -ne 0) {
        # A helper that answered with a structured refusal (needs_attention/failed, or retryable=false)
        # will answer the same way again: do not retry it. Only transport failures are transient.
        $answered = $null -ne $parsed -and $parsed.status -in @('needs_attention', 'failed')
        $terminal = $answered -and ($parsed.PSObject.Properties.Name -notcontains 'retryable' -or -not $parsed.retryable)
        $err = [Exception]::new("WSL intake helper failed: $output")
        $err.Data['NonRetryable'] = [bool]$terminal
        throw $err
    }
    return $parsed
}

if (-not (Test-Path -LiteralPath $Inbox -PathType Container)) {
    throw "DriveFS inbox unavailable: $Inbox"
}
if ($PSBoundParameters.ContainsKey('FileName') -and
    ([string]::IsNullOrWhiteSpace($FileName) -or $FileName -match '[\\/:]' -or $FileName -in @('.', '..'))) {
    throw '-FileName must be one exact filename in the Drive Memory Intake Inbox'
}

do {
    $Passes++
    if ($PSBoundParameters.ContainsKey('FileName')) {
        $selectedPath = Join-Path $Inbox $FileName
        $selectedFile = Get-ChildItem -LiteralPath $Inbox -File |
            Where-Object { $_.Name -ceq $FileName } |
            Select-Object -First 1
        if ($null -eq $selectedFile) {
            if (Test-Path -LiteralPath $selectedPath -PathType Leaf) {
                throw "Selected Drive Memory Intake file does not exist with exact name: $FileName"
            }
            throw "Selected Drive Memory Intake file does not exist: $FileName"
        }
        $candidates = @($selectedFile)
    } else {
        $candidates = Get-ChildItem -LiteralPath $Inbox -File
    }
    $candidates | ForEach-Object {
        $file = $_
        $suffix = $file.Extension.ToLowerInvariant()
        if ($Supported -notcontains $suffix -or $file.Name.StartsWith('.') -or $file.Name.StartsWith('~$') -or
            $file.Name -match '(?i)(~\$|\.tmp$|\.partial$|\.crdownload$)' -or
            ($file.Attributes -band [IO.FileAttributes]::ReparsePoint)) { return }
        $identity = "$($file.Length):$($file.LastWriteTimeUtc.Ticks)"
        if ($Completed[$file.FullName] -eq $identity) { return }
        if ($Seen[$file.FullName] -ne $identity) {
            $Seen[$file.FullName] = $identity
            return
        }
        $retryKey = "$($file.FullName)|$identity"
        $state = $Retry[$retryKey]
        if ($null -ne $state) {
            if ($state.Parked) { return }
            if ([datetime]::Parse($state.NextTry).ToUniversalTime() -gt [datetime]::UtcNow) { return }
        }
        try {
            $fresh = Get-Item -LiteralPath $file.FullName
            if ($fresh.Length -ne $file.Length -or $fresh.LastWriteTimeUtc.Ticks -ne $file.LastWriteTimeUtc.Ticks) { return }
            $digest = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            $check = Invoke-IntakeHelper 'check' $digest $suffix $file.Name
            if ($check.status -eq 'reconciled') {
                Write-Output "Reconciled $($file.Name) sha256:$digest"
                $Completed[$file.FullName] = $identity
                return
            }
            if (-not $Ingest) {
                Write-Output "Eligible $($file.Name) sha256:$digest (reconciliation only)"
                $Completed[$file.FullName] = $identity
                return
            }
            $folder = Join-Path $Staging $digest
            New-Item -ItemType Directory -Path $folder -Force | Out-Null
            $target = Join-Path $folder "source$suffix"
            if (-not (Test-Path -LiteralPath $target)) {
                $temporary = Join-Path $folder ("source$suffix." + [guid]::NewGuid().ToString('N') + '.partial')
                try {
                    Copy-Item -LiteralPath $file.FullName -Destination $temporary
                    if ((Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash.ToLowerInvariant() -ne $digest) {
                        throw 'staged copy SHA-256 mismatch'
                    }
                    Move-Item -LiteralPath $temporary -Destination $target
                } finally {
                    if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force }
                }
            }
            $result = Invoke-IntakeHelper 'ingest' $digest $suffix $file.Name
            Write-Output "$($result.status) $($file.Name) sha256:$digest"
            $Completed[$file.FullName] = $identity
            if ($Retry.ContainsKey($retryKey)) { $Retry.Remove($retryKey); Save-RetryState }
        } catch {
            $attempts = 1 + $(if ($null -ne $Retry[$retryKey]) { $Retry[$retryKey].Attempts } else { 0 })
            $terminal = [bool]$_.Exception.Data['NonRetryable']
            $parked = $terminal -or $attempts -ge $MaxAttempts
            $delay = [Math]::Min($BackoffCapSeconds, $BackoffBaseSeconds * [Math]::Pow(2, $attempts - 1))
            $Retry[$retryKey] = @{
                Attempts = $attempts; Parked = $parked; Reason = $_.Exception.Message
                NextTry = [datetime]::UtcNow.AddSeconds($delay).ToString('o')
            }
            Save-RetryState
            if ($parked) {
                Write-Warning "Drive intake PARKED for $($file.Name) after $attempts attempt(s): $($_.Exception.Message). Edit or remove its entry in $StatePath to retry."
            } else {
                Write-Warning "Drive intake retry $attempts/$MaxAttempts for $($file.Name) in ${delay}s: $($_.Exception.Message)"
            }
        }
    }
    if (-not $Once -or $Passes -lt 2) { Start-Sleep -Seconds $PollSeconds }
} while (-not $Once -or $Passes -lt 2)
