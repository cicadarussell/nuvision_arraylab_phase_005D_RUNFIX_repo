param(
    [ValidateSet('doctor','dev','tests')]
    [string]$Mode = 'dev'
)

$ErrorActionPreference = 'Stop'
$Root = Resolve-Path (Join-Path $PSScriptRoot '..')
$Backend = Join-Path $Root 'backend'
$Venv = Join-Path $Backend '.venv'
$VenvPython = Join-Path $Venv 'Scripts\python.exe'
$Logs = Join-Path $Root 'logs'
New-Item -ItemType Directory -Force -Path $Logs | Out-Null

function Write-Step($msg) { Write-Host "[ArrayLab] $msg" -ForegroundColor Cyan }
function Write-Warn($msg) { Write-Host "[ArrayLab WARNING] $msg" -ForegroundColor Yellow }
function Write-Bad($msg) { Write-Host "[ArrayLab ERROR] $msg" -ForegroundColor Red }

function Invoke-PythonCheck($Exe, [string[]]$Args = @()) {
    try {
        $cmdArgs = @($Args + @('-c', 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"); raise SystemExit(0 if sys.version_info >= (3,9) else 7)'))
        $out = & $Exe @cmdArgs 2>$null
        if ($LASTEXITCODE -eq 0) {
            return [PSCustomObject]@{ Exe = $Exe; Args = $Args; Version = ($out | Select-Object -First 1) }
        }
    } catch {}
    return $null
}

function Find-Python39Plus() {
    $candidates = @(
        @('py', @('-3.13')),
        @('py', @('-3.12')),
        @('py', @('-3.11')),
        @('py', @('-3.10')),
        @('py', @('-3.9')),
        @('python', @()),
        @('python3', @())
    )
    foreach ($candidate in $candidates) {
        $found = Invoke-PythonCheck $candidate[0] $candidate[1]
        if ($found) { return $found }
    }
    return $null
}

function Test-VenvPython39Plus() {
    if (!(Test-Path $VenvPython)) { return $false }
    try {
        & $VenvPython -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,9) else 7)' 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

function Ensure-Venv() {
    Write-Step "Checking Python version..."
    $py = Find-Python39Plus
    if (!$py) {
        Write-Bad "No Python 3.9+ interpreter found. ArrayLab now supports 3.9+, but this machine still needs one visible to the launcher."
        Write-Host "Install one of: Python 3.12, 3.11, 3.10, or keep 3.9."
        Write-Host "Fast route: winget install -e --id Python.Python.3.12"
        Write-Host "Then re-run run_dev.bat. Yes, software dependency bootstrapping remains mankind's least elegant ritual."
        exit 20
    }
    Write-Step "Using Python $($py.Version) via: $($py.Exe) $($py.Args -join ' ')"

    if ((Test-Path $Venv) -and !(Test-VenvPython39Plus)) {
        Write-Warn "Existing backend .venv is broken or too old. Rebuilding it automatically."
        Remove-Item -Recurse -Force $Venv
    }

    if (!(Test-Path $VenvPython)) {
        Write-Step "Creating backend virtual environment..."
        & $py.Exe @($py.Args + @('-m','venv',$Venv))
        if ($LASTEXITCODE -ne 0) { throw "Failed to create virtual environment." }
    }

    Write-Step "Upgrading pip inside backend .venv..."
    & $VenvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed." }

    Write-Step "Installing backend package in editable mode..."
    $Editable = "$Backend[dev]"
    & $VenvPython -m pip install -e $Editable
    if ($LASTEXITCODE -ne 0) { throw "Backend dependency install failed. Stopping instead of pretending the server started." }
}

function Run-Doctor() {
    Write-Step "Root: $Root"
    Write-Step "Backend: $Backend"
    $py = Find-Python39Plus
    if ($py) { Write-Step "Python OK: $($py.Version) via $($py.Exe) $($py.Args -join ' ')" } else { Write-Bad "Python 3.9+ not found" }
    if (Test-VenvPython39Plus) {
        $ver = & $VenvPython -c 'import sys; print(sys.version)'
        Write-Step "Venv OK: $ver"
    } else {
        Write-Warn "Venv missing, broken, or older than Python 3.9. run_dev.bat will rebuild it."
    }
    if (Test-Path (Join-Path $Backend 'app\main.py')) { Write-Step "Backend app found." } else { Write-Bad "backend\app\main.py missing." }
    if (Test-Path (Join-Path $Root 'local_test_harness.html')) { Write-Step "Local test harness found." }
}

try {
    if ($Mode -eq 'doctor') { Run-Doctor; exit 0 }
    Ensure-Venv
    if ($Mode -eq 'tests') {
        Write-Step "Running backend tests..."
        & $VenvPython -m pytest (Join-Path $Backend 'tests') -q
        exit $LASTEXITCODE
    }
    Write-Host ""
    Write-Step "Starting NuVision ArrayLab backend"
    Write-Host "API docs: http://127.0.0.1:8000/docs"
    Write-Host "Local test harness: open local_test_harness.html after the backend starts."
    Write-Host ""
    & $VenvPython -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 --app-dir $Backend
    exit $LASTEXITCODE
} catch {
    Write-Bad $_.Exception.Message
    Write-Host "Run doctor.bat for a quick environment report."
    exit 1
}
