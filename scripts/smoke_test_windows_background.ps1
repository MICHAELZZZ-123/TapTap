param(
    [Parameter(Mandatory = $true)]
    [string]$Executable
)

$ErrorActionPreference = "Stop"

if ($env:OS -ne "Windows_NT") {
    throw "The background-lifecycle smoke test must run on Windows."
}

Add-Type @"
using System;
using System.Runtime.InteropServices;

public static class TapTapWindowApi
{
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern IntPtr FindWindow(string className, string windowName);

    [DllImport("user32.dll")]
    public static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);

    [DllImport("user32.dll")]
    public static extern bool IsWindowVisible(IntPtr window);

    [DllImport("user32.dll")]
    public static extern bool PostMessage(IntPtr window, uint message, IntPtr wParam, IntPtr lParam);
}
"@

$resolvedExecutable = (Resolve-Path -LiteralPath $Executable).Path
$existingIds = @(
    Get-Process -Name "TapTap" -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty Id
)
$tempBase = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { $env:TEMP }
$testRoot = Join-Path $tempBase ("taptap-background-" + [guid]::NewGuid())
$env:TAPTAP_DB_PATH = Join-Path $testRoot "events.db"
$env:TAPTAP_DATA_DIR = Join-Path $testRoot "data"
$env:TAPTAP_LOG_DIR = Join-Path $testRoot "logs"

function Get-TestWindow {
    # Windows PowerShell marshals $null as an empty string for this P/Invoke;
    # NullString is required to mean "any window class" on both 5.1 and 7.x.
    $handle = [TapTapWindowApi]::FindWindow([NullString]::Value, "TapTap")
    if ($handle -eq [IntPtr]::Zero) {
        return $null
    }
    [uint32]$ownerId = 0
    [void][TapTapWindowApi]::GetWindowThreadProcessId($handle, [ref]$ownerId)
    if ($existingIds -contains [int]$ownerId) {
        return $null
    }
    return [pscustomobject]@{
        Handle = $handle
        ProcessId = [int]$ownerId
        Visible = [TapTapWindowApi]::IsWindowVisible($handle)
    }
}

function Wait-TestWindow([bool]$Visible, [int]$Attempts = 60) {
    for ($attempt = 0; $attempt -lt $Attempts; $attempt++) {
        Start-Sleep -Milliseconds 500
        $candidate = Get-TestWindow
        if ($candidate -and $candidate.Visible -eq $Visible) {
            return $candidate
        }
    }
    return $null
}

New-Item -ItemType Directory -Path $testRoot -Force | Out-Null
[void](Start-Process -FilePath $resolvedExecutable -ArgumentList @("--startup") -PassThru)

try {
    $hiddenWindow = Wait-TestWindow -Visible $false
    if (-not $hiddenWindow) {
        throw "TapTap --startup did not create a hidden native window."
    }

    $backgroundId = $hiddenWindow.ProcessId
    $logPath = Join-Path $env:TAPTAP_LOG_DIR "taptap.log"
    $trayReady = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Milliseconds 250
        if (
            (Test-Path $logPath) -and
            (Select-String -LiteralPath $logPath -Pattern "Windows tray lifecycle ready" -Quiet)
        ) {
            $trayReady = $true
            break
        }
    }
    if (-not $trayReady) {
        throw "TapTap did not report a ready Windows tray lifecycle."
    }
    Write-Output "TapTap startup mode is hidden with a ready tray (PID $backgroundId)."

    $workerProbe = @'
import datetime
import os
import sqlite3
import sys
import time

database = os.environ["TAPTAP_DB_PATH"]
now = datetime.datetime.now()
with sqlite3.connect(database, timeout=5) as connection:
    cursor = connection.execute(
        "INSERT INTO events "
        "(name, event_date, event_time, reminder_min, recurrence, active) "
        "VALUES (?, ?, ?, ?, 'none', 1)",
        (
            "Background notification smoke test",
            now.strftime("%Y-%m-%d"),
            now.strftime("%H:%M"),
            "0",
        ),
    )
    event_id = cursor.lastrowid
    connection.commit()

deadline = time.monotonic() + 10
while time.monotonic() < deadline:
    with sqlite3.connect(database, timeout=5) as connection:
        row = connection.execute(
            "SELECT last_reminded FROM events WHERE id=?",
            (event_id,),
        ).fetchone()
    if row and row[0] == "0":
        print(f"Hidden reminder worker claimed event {event_id}.")
        sys.exit(0)
    time.sleep(0.25)

print("The hidden reminder worker did not claim its due event.", file=sys.stderr)
sys.exit(1)
'@
    $workerProbe | python -
    if ($LASTEXITCODE -ne 0) {
        throw "TapTap did not process reminders while its window was hidden."
    }

    $nativeDelivery = $false
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        Start-Sleep -Milliseconds 250
        if (
            (Test-Path $logPath) -and
            (Select-String -LiteralPath $logPath -Pattern "Native desktop notification dispatched for event 1" -Quiet)
        ) {
            $nativeDelivery = $true
            break
        }
    }
    if (-not $nativeDelivery) {
        throw "TapTap did not dispatch a native notification while hidden."
    }
    Write-Output "The hidden worker dispatched a native Windows notification."

    $activation = Start-Process -FilePath $resolvedExecutable -PassThru
    $visibleWindow = Wait-TestWindow -Visible $true
    if (-not $visibleWindow -or $visibleWindow.ProcessId -ne $backgroundId) {
        throw "A normal second launch did not activate the existing TapTap window."
    }
    [void]$activation.WaitForExit(10000)
    Write-Output "A normal second launch reopened the existing window without a duplicate instance."

    # WM_SYSCOMMAND/SC_CLOSE follows the same UserClosing path as the title-bar X.
    [void][TapTapWindowApi]::PostMessage(
        $visibleWindow.Handle,
        0x0112,
        [IntPtr]0xF060,
        [IntPtr]::Zero
    )
    $closedToTray = Wait-TestWindow -Visible $false -Attempts 30
    if (-not $closedToTray -or $closedToTray.ProcessId -ne $backgroundId) {
        throw "Closing TapTap did not hide the existing process to the notification area."
    }
    if (-not (Get-Process -Id $backgroundId -ErrorAction SilentlyContinue)) {
        throw "The reminder process exited when the main window closed."
    }
    Write-Output "Closing the window kept TapTap running in the background."

    [void](Start-Process -FilePath $resolvedExecutable -PassThru)
    $reopenedWindow = Wait-TestWindow -Visible $true
    if (-not $reopenedWindow -or $reopenedWindow.ProcessId -ne $backgroundId) {
        throw "TapTap could not reopen after being hidden to the notification area."
    }
    Write-Output "TapTap reopened from its background process (PID $backgroundId)."

}
catch {
    $logPath = Join-Path $env:TAPTAP_LOG_DIR "taptap.log"
    if (Test-Path $logPath) {
        Write-Output "TapTap background lifecycle log:"
        Get-Content $logPath -Tail 200
    }
    throw
}
finally {
    # ROBUSTNESS: Stop only TapTap processes created by this isolated test.
    Get-Process -Name "TapTap" -ErrorAction SilentlyContinue |
        Where-Object { $existingIds -notcontains $_.Id } |
        Stop-Process -Force -ErrorAction SilentlyContinue
}
