$ErrorActionPreference = "Stop"

$rootDir = Resolve-Path (Join-Path $PSScriptRoot "..")
$backendDir = Join-Path $rootDir "backend"
$frontendDir = Join-Path $rootDir "frontend"
# Python 解释器：优先读环境变量 PYTHON_EXE，没设就用 PATH 里的 python。
# 这里不写死某个 conda 环境的绝对路径 —— 那是某台机器专属的，别人 clone 下来跑不了。
$pythonExe = if ($env:PYTHON_EXE) { $env:PYTHON_EXE } else { "python" }
$backendHealthUrl = "http://127.0.0.1:8001/api/health"
$backendLogDir = Join-Path $backendDir "logs"
$backendOutLog = Join-Path $backendLogDir "dev-backend.out.log"
$backendErrLog = Join-Path $backendLogDir "dev-backend.err.log"
$startedBackend = $false
$backendProcess = $null

function Test-BackendHealthy {
    try {
        $response = Invoke-RestMethod -Uri $backendHealthUrl -TimeoutSec 2
        return $response.status -eq "ok"
    }
    catch {
        return $false
    }
}

function Wait-BackendHealthy {
    param([int] $TimeoutSeconds = 40)

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-BackendHealthy) {
            return $true
        }
        Start-Sleep -Milliseconds 700
    }
    return $false
}

# 解析成完整路径：Start-Process 用相对命令名不可靠
$pythonCmd = Get-Command $pythonExe -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    Write-Error "找不到 Python（$pythonExe）。可用 PYTHON_EXE 指定解释器，例如：`$env:PYTHON_EXE='C:\path\to\python.exe'"
}
$pythonExe = $pythonCmd.Source

if (Test-BackendHealthy) {
    Write-Host "Backend is already running on 8001. Starting Vite..."
}
else {
    New-Item -ItemType Directory -Force -Path $backendLogDir | Out-Null
    Write-Host "Backend is not running on 8001. Starting it with $pythonExe ..."
    $backendProcess = Start-Process `
        -FilePath $pythonExe `
        -ArgumentList @("-m", "uvicorn", "app.main:app", "--port", "8001") `
        -WorkingDirectory $backendDir `
        -PassThru `
        -WindowStyle Hidden `
        -RedirectStandardOutput $backendOutLog `
        -RedirectStandardError $backendErrLog
    $startedBackend = $true

    if (-not (Wait-BackendHealthy)) {
        if ($backendProcess -and -not $backendProcess.HasExited) {
            Stop-Process -Id $backendProcess.Id -Force
        }
        Write-Host "Backend failed to start. Check logs:"
        Write-Host "  $backendOutLog"
        Write-Host "  $backendErrLog"
        exit 1
    }
    Write-Host "Backend started: http://127.0.0.1:8001"
}

try {
    Set-Location $frontendDir
    & (Join-Path $frontendDir "node_modules\.bin\vite.cmd")
}
finally {
    if ($startedBackend -and $backendProcess -and -not $backendProcess.HasExited) {
        Write-Host "Stopping backend started by npm run dev..."
        Stop-Process -Id $backendProcess.Id -Force
    }
}
