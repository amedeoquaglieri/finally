<#
.SYNOPSIS
    Start FinAlly in Docker (Windows PowerShell). Safe to run repeatedly.
.PARAMETER Build
    Rebuild the image even if it already exists.
.PARAMETER NoOpen
    Don't open the browser.
.EXAMPLE
    .\scripts\start_windows.ps1 -Build
#>
[CmdletBinding()]
param(
    [switch]$Build,
    [switch]$NoOpen
)

# 'Continue' so stderr from native docker calls isn't turned into terminating errors
# (Windows PowerShell 5.1); failures are detected via $LASTEXITCODE instead.
$ErrorActionPreference = 'Continue'

$Image = 'finally'
$Container = 'finally'
$Volume = 'finally-data'
$Port = if ($env:FINALLY_PORT) { $env:FINALLY_PORT } else { '8000' }
$Url = "http://localhost:$Port"

$RootDir = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $RootDir '.env'

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error 'docker is not installed or not on PATH.'
    exit 1
}

docker image inspect $Image *> $null
if ($Build -or $LASTEXITCODE -ne 0) {
    Write-Host "Building image '$Image'..."
    docker build -t $Image $RootDir
    if ($LASTEXITCODE -ne 0) { Write-Error 'docker build failed.'; exit 1 }
}

# Replace any existing container (running or stopped); data lives in the volume.
docker container inspect $Container *> $null
if ($LASTEXITCODE -eq 0) {
    Write-Host "Removing existing container '$Container'..."
    docker rm -f $Container *> $null
}

$RunArgs = @('run', '-d', '--name', $Container, '-p', "${Port}:8000", '-v', "${Volume}:/app/db")
if (Test-Path $EnvFile) {
    $RunArgs += @('--env-file', $EnvFile)
} else {
    Write-Warning "$EnvFile not found; running without it (AI chat needs OPENROUTER_API_KEY)."
    Write-Warning 'Copy .env.example to .env and add your keys.'
}

Write-Host "Starting container '$Container'..."
docker @RunArgs | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Error 'docker run failed.'; exit 1 }

# Wait for the health endpoint (up to ~30 s).
$Ready = $false
for ($i = 0; $i -lt 30; $i++) {
    try {
        $Resp = Invoke-WebRequest -Uri "$Url/api/health" -UseBasicParsing -TimeoutSec 2
        if ($Resp.StatusCode -eq 200) { $Ready = $true; break }
    } catch { }
    $Running = docker inspect -f '{{.State.Running}}' $Container 2>$null
    if ($Running -ne 'true') {
        Write-Host 'Container exited during startup. Logs:'
        docker logs $Container
        exit 1
    }
    Start-Sleep -Seconds 1
}

if ($Ready) {
    Write-Host "FinAlly is running at $Url"
} else {
    Write-Host "FinAlly is starting at $Url (not healthy yet; check: docker logs $Container)"
}

if (-not $NoOpen) {
    try { Start-Process $Url } catch { }
}
