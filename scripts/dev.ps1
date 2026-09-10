$ErrorActionPreference = "Stop"

$rootDir = Resolve-Path (Join-Path $PSScriptRoot "..")
$backendDir = Join-Path $rootDir "backend"
$frontendDir = Join-Path $rootDir "frontend"
$pythonExe = "D:\development\miniconda3\envs\langchain1.2\python.exe"
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

if (-not (Test-Path $pythonExe)) {
    Write-Error "Python environment not found: $pythonExe. Please check the langchain1.2 path."
}

if (Test-BackendHealthy) {
    Write-Host "Backend is already running on 8001. Starting Vite..."
}
else {
    New-Item -ItemType Directory -Force -Path $backendLogDir | Out-Null
    Write-Host "Backend is not running on 8001. Starting it with the existing langchain1.2 environment..."
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
