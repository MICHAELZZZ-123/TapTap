param(
    [Parameter(Mandatory = $true)]
    [string]$Executable
)

$ErrorActionPreference = "Stop"

if ($env:OS -ne "Windows_NT") {
    throw "The native-window smoke test must run on Windows."
}

$resolvedExecutable = (Resolve-Path -LiteralPath $Executable).Path
$existingIds = @(
    Get-Process -Name "TapTap" -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty Id
)
$tempBase = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { $env:TEMP }
$testRoot = Join-Path $tempBase ("taptap-native-window-" + [guid]::NewGuid())
$env:TAPTAP_DB_PATH = Join-Path $testRoot "events.db"
$env:TAPTAP_DATA_DIR = Join-Path $testRoot "data"
$env:TAPTAP_LOG_DIR = Join-Path $testRoot "logs"

New-Item -ItemType Directory -Path $testRoot -Force | Out-Null
$rootProcess = Start-Process -FilePath $resolvedExecutable -PassThru
$windowProcess = $null

try {
    # PACKAGING: --help cannot detect failures on pywebview's CLR UI thread.
    # Require the frozen process to expose a real, responsive TapTap window.
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        Start-Sleep -Seconds 1
        $newProcesses = @(
            Get-Process -Name "TapTap" -ErrorAction SilentlyContinue |
                Where-Object { $existingIds -notcontains $_.Id }
        )
        $windowProcess = $newProcesses |
            Where-Object {
                $_.Responding -and
                $_.MainWindowHandle -ne 0 -and
                $_.MainWindowTitle -eq "TapTap"
            } |
            Select-Object -First 1

        if ($windowProcess) {
            Write-Output (
                "TapTap window ready (PID {0}, handle {1})." -f
                $windowProcess.Id, $windowProcess.MainWindowHandle
            )
            break
        }

        $rootProcess.Refresh()
        if ($rootProcess.HasExited -and $newProcesses.Count -eq 0) {
            break
        }
    }

    if (-not $windowProcess) {
        $logPath = Join-Path $env:TAPTAP_LOG_DIR "taptap.log"
        if (Test-Path $logPath) {
            Write-Output "TapTap startup log:"
            Get-Content $logPath -Tail 200
        }
        throw "TapTap did not create a responsive native Windows window."
    }
}
finally {
    # ROBUSTNESS: Stop only TapTap processes created by this isolated test.
    Get-Process -Name "TapTap" -ErrorAction SilentlyContinue |
        Where-Object { $existingIds -notcontains $_.Id } |
        Stop-Process -Force -ErrorAction SilentlyContinue
}
