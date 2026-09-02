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
    public delegate bool EnumWindowsProc(IntPtr window, IntPtr parameter);

    [DllImport("user32.dll")]
    public static extern bool EnumWindows(EnumWindowsProc callback, IntPtr parameter);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern int GetWindowText(IntPtr window, System.Text.StringBuilder text, int count);

    [DllImport("user32.dll")]
    public static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);

    [DllImport("user32.dll")]
    public static extern bool IsWindowVisible(IntPtr window);

    [DllImport("user32.dll")]
    public static extern bool PostMessage(IntPtr window, uint message, IntPtr wParam, IntPtr lParam);

    [DllImport("user32.dll")]
    public static extern bool SetCursorPos(int x, int y);

    [DllImport("user32.dll")]
    public static extern void mouse_event(uint flags, uint dx, uint dy, uint data, UIntPtr extraInfo);

    public static IntPtr FindWindowForProcess(uint processId, string title)
    {
        IntPtr result = IntPtr.Zero;
        EnumWindows(delegate(IntPtr window, IntPtr parameter)
        {
            uint ownerId;
            GetWindowThreadProcessId(window, out ownerId);
            if (ownerId != processId)
            {
                return true;
            }

            var text = new System.Text.StringBuilder(256);
            GetWindowText(window, text, text.Capacity);
            if (text.ToString() == title)
            {
                result = window;
                return false;
            }
            return true;
        }, IntPtr.Zero);
        return result;
    }
}
"@

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$resolvedExecutable = (Resolve-Path -LiteralPath $Executable).Path
$existingIds = @(
    Get-Process -Name "TapTap" -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty Id
)
$tempBase = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { $env:TEMP }
$smokeId = [guid]::NewGuid().ToString("N")
$testRoot = Join-Path $tempBase ("taptap-background-" + $smokeId)
$env:TAPTAP_DB_PATH = Join-Path $testRoot "events.db"
$env:TAPTAP_DATA_DIR = Join-Path $testRoot "data"
$env:TAPTAP_LOG_DIR = Join-Path $testRoot "logs"
$env:TAPTAP_SMOKE_TRAY_TEXT = "TapTap Smoke " + $smokeId
$env:TAPTAP_SMOKE_WINDOW_TITLE = "TapTap Smoke Window " + $smokeId
$runKeyPath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$runValueName = "TapTap"
$savedRunValue = $null
$hadRunValue = $false
try {
    $savedRunValue = (Get-ItemProperty -Path $runKeyPath -Name $runValueName).$runValueName
    $hadRunValue = $true
}
catch {
    $hadRunValue = $false
}
# A frozen test copy must never repair a user's real opt-in startup command.
Remove-ItemProperty -Path $runKeyPath -Name $runValueName -ErrorAction SilentlyContinue

function Get-TestWindow {
    $newIds = @(
        Get-Process -Name "TapTap" -ErrorAction SilentlyContinue |
            Where-Object { $existingIds -notcontains $_.Id } |
            Select-Object -ExpandProperty Id
    )
    foreach ($candidateId in $newIds) {
        $handle = [TapTapWindowApi]::FindWindowForProcess(
            $candidateId,
            $env:TAPTAP_SMOKE_WINDOW_TITLE
        )
        if ($handle -ne [IntPtr]::Zero) {
            return [pscustomobject]@{
                Handle = $handle
                ProcessId = [int]$candidateId
                Visible = [TapTapWindowApi]::IsWindowVisible($handle)
            }
        }
    }
    return $null
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

function Find-AutomationElement([string]$Name, $ControlType) {
    $condition = [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::NameProperty,
        $Name
    )
    $matches = [System.Windows.Automation.AutomationElement]::RootElement.FindAll(
        [System.Windows.Automation.TreeScope]::Descendants,
        $condition
    )
    foreach ($candidate in $matches) {
        if (
            $candidate.Current.ControlType -eq $ControlType -and
            -not $candidate.Current.IsOffscreen -and
            $candidate.Current.BoundingRectangle.Width -gt 0
        ) {
            return $candidate
        }
    }
    return $null
}

function Find-AutomationElementByClassAndId([string]$ClassName, [string]$AutomationId) {
    $matches = [System.Windows.Automation.AutomationElement]::RootElement.FindAll(
        [System.Windows.Automation.TreeScope]::Descendants,
        [System.Windows.Automation.Condition]::TrueCondition
    )
    foreach ($candidate in $matches) {
        if (
            $candidate.Current.ClassName -eq $ClassName -and
            $candidate.Current.AutomationId -eq $AutomationId
        ) {
            return $candidate
        }
    }
    return $null
}

function Invoke-PhysicalClick($Element, [bool]$RightClick = $false) {
    $bounds = $Element.Current.BoundingRectangle
    if ($bounds.Width -le 0 -or $bounds.Height -le 0) {
        throw "Windows exposed an automation element without clickable bounds."
    }
    $x = [int]($bounds.Left + ($bounds.Width / 2))
    $y = [int]($bounds.Top + ($bounds.Height / 2))
    [void][TapTapWindowApi]::SetCursorPos($x, $y)
    if ($RightClick) {
        [TapTapWindowApi]::mouse_event(0x0008, 0, 0, 0, [UIntPtr]::Zero)
        [TapTapWindowApi]::mouse_event(0x0010, 0, 0, 0, [UIntPtr]::Zero)
    }
    else {
        [TapTapWindowApi]::mouse_event(0x0002, 0, 0, 0, [UIntPtr]::Zero)
        [TapTapWindowApi]::mouse_event(0x0004, 0, 0, 0, [UIntPtr]::Zero)
    }
}

function Invoke-AutomationAction($Element) {
    try {
        $pattern = $Element.GetCurrentPattern(
            [System.Windows.Automation.InvokePattern]::Pattern
        )
        $pattern.Invoke()
        return
    }
    catch {
        $pattern = $Element.GetCurrentPattern(
            [System.Windows.Automation.LegacyIAccessiblePattern]::Pattern
        )
        $pattern.DoDefaultAction()
    }
}

function Wait-TrayIcon([int]$Attempts = 40) {
    for ($attempt = 0; $attempt -lt $Attempts; $attempt++) {
        $icon = Find-AutomationElement `
            -Name $env:TAPTAP_SMOKE_TRAY_TEXT `
            -ControlType ([System.Windows.Automation.ControlType]::Button)
        if ($icon -and $icon.Current.BoundingRectangle.Width -gt 0) {
            return $icon
        }
        if ($attempt -eq 4) {
            # Windows localizes this button's accessible name. Its class and
            # automation ID are stable across the supported Windows 11 layouts.
            $chevron = Find-AutomationElementByClassAndId `
                -ClassName "SystemTray.NormalButton" `
                -AutomationId "SystemTrayIcon"
            if (-not $chevron) {
                foreach ($chevronName in @("Show hidden icons", "Notification Chevron")) {
                    $chevron = Find-AutomationElement `
                        -Name $chevronName `
                        -ControlType ([System.Windows.Automation.ControlType]::Button)
                    if ($chevron) {
                        break
                    }
                }
            }
            if ($chevron) {
                Invoke-PhysicalClick $chevron
            }
        }
        Start-Sleep -Milliseconds 250
    }
    return $null
}

function Wait-MenuItem([string]$Name, [int]$Attempts = 20) {
    for ($attempt = 0; $attempt -lt $Attempts; $attempt++) {
        $item = Find-AutomationElement `
            -Name $Name `
            -ControlType ([System.Windows.Automation.ControlType]::MenuItem)
        if ($item) {
            return $item
        }
        Start-Sleep -Milliseconds 200
    }
    return $null
}

function Open-TrayMenuItem([string]$Name) {
    for ($menuAttempt = 0; $menuAttempt -lt 3; $menuAttempt++) {
        $icon = Wait-TrayIcon
        if ($icon) {
            Invoke-PhysicalClick $icon -RightClick $true
            $item = Wait-MenuItem -Name $Name -Attempts 10
            if ($item) {
                return $item
            }
        }
        Start-Sleep -Seconds 1
    }
    return $null
}

New-Item -ItemType Directory -Path $testRoot -Force | Out-Null
$alternateExecutable = Join-Path $testRoot "existing-TapTap.exe"
New-Item -ItemType File -Path $alternateExecutable -Force | Out-Null
$alternateCommand = '"' + $alternateExecutable + '" --startup'
Set-ItemProperty -Path $runKeyPath -Name $runValueName -Value $alternateCommand
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

    $preservedRunValue = (Get-ItemProperty -Path $runKeyPath -Name $runValueName).$runValueName
    if ($preservedRunValue -ne $alternateCommand) {
        throw "A temporary TapTap copy took over an existing valid startup entry."
    }
    Write-Output "TapTap preserved the existing valid Windows startup owner."

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

    $deliveryMode = $null
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        Start-Sleep -Milliseconds 250
        if (Test-Path $logPath) {
            if (Select-String -LiteralPath $logPath -Pattern "Native desktop notification dispatched for event 1" -Quiet) {
                $deliveryMode = "native"
                break
            }
            if (Select-String -LiteralPath $logPath -Pattern "Background fallback alert activated for event 1" -Quiet) {
                $deliveryMode = "fallback"
                break
            }
        }
    }
    if (-not $deliveryMode) {
        throw "TapTap produced neither a native notification nor its background fallback alarm."
    }
    if ($deliveryMode -eq "native") {
        Write-Output "The hidden worker dispatched a confirmed native Windows notification."
    }
    else {
        $fallbackWindow = Wait-TestWindow -Visible $true -Attempts 30
        if (-not $fallbackWindow -or $fallbackWindow.ProcessId -ne $backgroundId) {
            throw "The permission-independent fallback did not reveal TapTap's alarm."
        }
        Write-Output "Native notifications are disabled; the background fallback revealed TapTap."
    }

    $outboxProbe = @'
import os
import sqlite3
import sys
import time

mode = sys.argv[1]
deadline = time.monotonic() + 10
while time.monotonic() < deadline:
    with sqlite3.connect(os.environ["TAPTAP_DB_PATH"], timeout=5) as connection:
        row = connection.execute(
            "SELECT state, attempts, fallback_at, popup_consumed_at "
            "FROM reminder_outbox WHERE event_id=1"
        ).fetchone()
    if mode == "native" and row and row[0] == "delivered" and row[1] >= 1:
        print("The durable outbox recorded confirmed native delivery.")
        sys.exit(0)
    if (
        mode == "fallback" and row and row[0] == "pending" and row[1] >= 1
        and row[2] and row[3]
    ):
        print("The durable outbox retained the alarm and the visible UI consumed its fallback.")
        sys.exit(0)
    time.sleep(0.25)
print("The durable outbox did not reach the expected delivery state.", file=sys.stderr)
sys.exit(1)
'@
    $outboxProbe | python - $deliveryMode
    if ($LASTEXITCODE -ne 0) {
        throw "TapTap did not complete its durable delivery record."
    }

    if ($deliveryMode -eq "fallback") {
        [void][TapTapWindowApi]::PostMessage(
            $fallbackWindow.Handle,
            0x0112,
            [IntPtr]0xF060,
            [IntPtr]::Zero
        )
        if (-not (Wait-TestWindow -Visible $false -Attempts 30)) {
            throw "TapTap did not return to the tray after its fallback alarm."
        }
    }

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

    $reopenedWindow = $null
    for ($clickAttempt = 0; $clickAttempt -lt 3; $clickAttempt++) {
        $trayIcon = Wait-TrayIcon
        if (-not $trayIcon) {
            continue
        }
        Invoke-PhysicalClick $trayIcon
        $reopenedWindow = Wait-TestWindow -Visible $true -Attempts 10
        if ($reopenedWindow) {
            break
        }
        # Keep retries outside Windows' double-click interval. Each attempt is
        # still one real click on a freshly resolved accessibility element.
        Start-Sleep -Seconds 1
    }
    if (-not $reopenedWindow -or $reopenedWindow.ProcessId -ne $backgroundId) {
        throw "A real single left-click on TapTap's tray icon did not reopen the window."
    }
    Write-Output "A real single left-click reopened TapTap (PID $backgroundId)."

    [void][TapTapWindowApi]::PostMessage(
        $reopenedWindow.Handle,
        0x0112,
        [IntPtr]0xF060,
        [IntPtr]::Zero
    )
    if (-not (Wait-TestWindow -Visible $false -Attempts 30)) {
        throw "TapTap did not return to the notification area before the menu test."
    }

    $openItem = Open-TrayMenuItem -Name "Open TapTap"
    if (-not $openItem) {
        throw "TapTap's tray menu did not expose Open TapTap."
    }
    Invoke-AutomationAction $openItem
    $menuOpenedWindow = Wait-TestWindow -Visible $true -Attempts 30
    if (-not $menuOpenedWindow -or $menuOpenedWindow.ProcessId -ne $backgroundId) {
        throw "The Open TapTap tray command did not reopen the existing window."
    }
    Write-Output "The right-click tray menu exposed and ran Open TapTap."

    [void][TapTapWindowApi]::PostMessage(
        $menuOpenedWindow.Handle,
        0x0112,
        [IntPtr]0xF060,
        [IntPtr]::Zero
    )
    if (-not (Wait-TestWindow -Visible $false -Attempts 30)) {
        throw "TapTap did not return to the notification area before Quit TapTap."
    }

    $quitItem = Open-TrayMenuItem -Name "Quit TapTap"
    if (-not $quitItem) {
        throw "TapTap's tray menu did not expose Quit TapTap."
    }
    Invoke-AutomationAction $quitItem
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        if (-not (Get-Process -Id $backgroundId -ErrorAction SilentlyContinue)) {
            break
        }
        Start-Sleep -Milliseconds 250
    }
    if (Get-Process -Id $backgroundId -ErrorAction SilentlyContinue) {
        throw "Quit TapTap did not terminate the process gracefully."
    }
    Write-Output "Quit TapTap terminated the packaged process gracefully."

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
    if ($hadRunValue) {
        Set-ItemProperty -Path $runKeyPath -Name $runValueName -Value $savedRunValue
    }
    else {
        Remove-ItemProperty -Path $runKeyPath -Name $runValueName -ErrorAction SilentlyContinue
    }
}
