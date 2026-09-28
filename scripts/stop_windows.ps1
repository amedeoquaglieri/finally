<#
.SYNOPSIS
    Stop FinAlly (Windows PowerShell). Removes the container but keeps the finally-data
    volume, so portfolio data persists. Safe to run repeatedly.
#>
# 'Continue' so stderr from native docker calls isn't turned into terminating errors
# (Windows PowerShell 5.1); failures are detected via $LASTEXITCODE instead.
$ErrorActionPreference = 'Continue'

$Container = 'finally'

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error 'docker is not installed or not on PATH.'
    exit 1
}

docker container inspect $Container *> $null
if ($LASTEXITCODE -eq 0) {
    docker rm -f $Container *> $null
    Write-Host "FinAlly stopped (data kept in volume 'finally-data')."
} else {
    Write-Host 'FinAlly is not running.'
}
