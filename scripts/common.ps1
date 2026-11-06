# start_demo.ps1 / stop_demo.ps1 공용 함수 (Windows PowerShell 5.1 호환)

$Root = Split-Path -Parent $PSScriptRoot
$ServerDir = Join-Path $Root 'server'
$WebDir = Join-Path $Root 'web'
$PidFile = Join-Path $PSScriptRoot '.demo_pids.json'
$TunnelLog = Join-Path $PSScriptRoot '.tunnel.log'

function Write-Step($msg) { Write-Host "`n▶ $msg" -ForegroundColor Cyan }
function Write-Ok($msg) { Write-Host "  ✔ $msg" -ForegroundColor Green }
function Write-Skip($msg) { Write-Host "  - $msg" -ForegroundColor DarkGray }
function Write-Bad($msg) { Write-Host "  ✘ $msg" -ForegroundColor Red }

# scripts\.env 읽기 (KEY=VALUE, # 주석)
function Read-DemoEnv {
    $cfg = @{ SIGNALING_CMD = ''; UE_EXE = ''; UE_ARGS = ''; API_PORT = '8000' }
    $file = Join-Path $PSScriptRoot '.env'
    if (Test-Path $file) {
        foreach ($line in Get-Content $file -Encoding UTF8) {
            if ($line -match '^\s*([A-Z_]+)\s*=\s*(.*?)\s*$') { $cfg[$Matches[1]] = $Matches[2] }
        }
    }
    return $cfg
}

# PATH에 없으면 기본 설치 위치에서 찾기
function Find-Exe($name, [string[]]$candidates) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($c in $candidates) { if (Test-Path $c) { return $c } }
    return $null
}

function Get-Docker {
    Find-Exe 'docker' @("$env:ProgramFiles\Docker\Docker\resources\bin\docker.exe")
}

function Get-Cloudflared {
    Find-Exe 'cloudflared' @(
        "${env:ProgramFiles(x86)}\cloudflared\cloudflared.exe",
        "$env:ProgramFiles\cloudflared\cloudflared.exe"
    )
}

function Save-Pids($pids) {
    $pids | ConvertTo-Json | Set-Content $PidFile -Encoding UTF8
}

function Load-Pids {
    if (-not (Test-Path $PidFile)) { return @{} }
    $obj = Get-Content $PidFile -Raw -Encoding UTF8 | ConvertFrom-Json
    $h = @{}
    foreach ($p in $obj.PSObject.Properties) { $h[$p.Name] = [int]$p.Value }
    return $h
}
