# 시연 시작: DB → API → 웹 → 시그널링 → UE5 → 터널
# 실행: powershell -ExecutionPolicy Bypass -File scripts\start_demo.ps1 [-NoTunnel] [-NoWeb]
param(
    [switch]$NoTunnel,
    [switch]$NoWeb
)

# 5.1에서는 Stop으로 두면 외부 프로그램의 stderr 출력만으로 중단되므로 각 단계에서 직접 확인
. (Join-Path $PSScriptRoot 'common.ps1')

$cfg = Read-DemoEnv
$port = $cfg.API_PORT
# localhost는 IPv6(::1)를 먼저 시도해 느려질 수 있어 127.0.0.1을 씀
$apiLocal = "http://127.0.0.1:$port"
$pids = Load-Pids
if ($pids.Count -gt 0) {
    Write-Bad "이미 실행 중인 시연이 있습니다. 먼저 scripts\stop_demo.ps1을 실행하세요."
    exit 1
}

# ---------- 1. DB ----------
Write-Step 'PostgreSQL (Docker)'
$docker = Get-Docker
if (-not $docker) { Write-Bad 'Docker가 없습니다. Docker Desktop을 설치하세요.'; exit 1 }

& $docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host '  Docker Desktop을 켜는 중...'
    Start-Process "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe"
    $ready = $false
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep 3
        & $docker info *> $null
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
    }
    if (-not $ready) { Write-Bad 'Docker 엔진이 3분 안에 켜지지 않았습니다.'; exit 1 }
}
& $docker compose -f (Join-Path $ServerDir 'docker-compose.yml') up -d --wait
if ($LASTEXITCODE -ne 0) { Write-Bad 'DB 실행 실패'; exit 1 }
Write-Ok 'DB 실행'

# ---------- 2. API ----------
Write-Step "API (FastAPI :$port)"
$python = Join-Path $ServerDir '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { Write-Bad "가상환경이 없습니다: server\README.md의 '처음 한 번'을 먼저 하세요."; exit 1 }
$api = Start-Process $python -WorkingDirectory $ServerDir -PassThru `
    -ArgumentList '-m', 'uvicorn', 'app.main:app', '--host', '0.0.0.0', '--port', $port
$pids['api'] = $api.Id
Save-Pids $pids

$health = $null
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep 1
    try { $health = Invoke-RestMethod "$apiLocal/health" -TimeoutSec 2; break } catch {}
    if ($api.HasExited) { break }
}
if ($null -eq $health) { Write-Bad 'API가 응답하지 않습니다. API 창의 오류를 확인하세요.'; exit 1 }
Write-Ok "API 실행 (db=$($health.db), detector=$($health.detector))"

# ---------- 3. 웹 ----------
Write-Step '웹 (Next.js :3000)'
if ($NoWeb) {
    Write-Skip '-NoWeb: 건너뜀'
} elseif (-not (Test-Path (Join-Path $WebDir 'package.json'))) {
    Write-Skip 'web 폴더가 아직 없습니다: 건너뜀'
} elseif (-not (Test-Path (Join-Path $WebDir '.next'))) {
    Write-Bad '웹 빌드가 없습니다. web 폴더에서 npm run build를 먼저 하세요: 건너뜀'
} else {
    $web = Start-Process 'cmd.exe' -WorkingDirectory $WebDir -PassThru -ArgumentList '/k', 'npm run start'
    $pids['web'] = $web.Id
    Save-Pids $pids
    Write-Ok '웹 실행: http://localhost:3000'
}

# ---------- 4. 시그널링 ----------
Write-Step 'Pixel Streaming 시그널링 서버'
if (-not $cfg.SIGNALING_CMD) {
    Write-Skip 'scripts\.env의 SIGNALING_CMD가 비어 있음: 건너뜀'
} else {
    $sig = Start-Process 'cmd.exe' -PassThru -WorkingDirectory (Split-Path $cfg.SIGNALING_CMD) `
        -ArgumentList '/k', "`"$($cfg.SIGNALING_CMD)`""
    $pids['signaling'] = $sig.Id
    Save-Pids $pids
    Write-Ok '시그널링 실행'
    Start-Sleep 3
}

# ---------- 5. UE5 ----------
Write-Step 'UE5 빌드'
if (-not $cfg.UE_EXE) {
    Write-Skip 'scripts\.env의 UE_EXE가 비어 있음: 건너뜀'
} else {
    if ($cfg.UE_ARGS) {
        $ue = Start-Process $cfg.UE_EXE -PassThru -ArgumentList $cfg.UE_ARGS
    } else {
        $ue = Start-Process $cfg.UE_EXE -PassThru
    }
    $pids['ue5'] = $ue.Id
    Save-Pids $pids
    Write-Ok 'UE5 실행'
}

# ---------- 6. 터널 ----------
Write-Step 'Cloudflare 터널'
if ($NoTunnel) {
    Write-Skip '-NoTunnel: 건너뜀'
} else {
    $cf = Get-Cloudflared
    if (-not $cf) {
        Write-Bad 'cloudflared가 없습니다 (winget install Cloudflare.cloudflared): 건너뜀'
    } else {
        Remove-Item $TunnelLog -ErrorAction SilentlyContinue
        $tun = Start-Process $cf -PassThru -WindowStyle Hidden -RedirectStandardError $TunnelLog `
            -ArgumentList 'tunnel', '--url', $apiLocal
        $pids['tunnel'] = $tun.Id
        Save-Pids $pids

        $url = $null
        for ($i = 0; $i -lt 30 -and -not $url; $i++) {
            Start-Sleep 1
            if (Test-Path $TunnelLog) {
                $m = Select-String -Path $TunnelLog -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' | Select-Object -First 1
                if ($m) { $url = $m.Matches[0].Value }
            }
        }
        if ($url) {
            Write-Ok "터널 주소: $url"
            Write-Host "    → Vercel의 NEXT_PUBLIC_API_URL을 이 주소로 바꾸고 vercel --prod (주소가 바뀌었을 때만)" -ForegroundColor Yellow
        } else {
            Write-Bad "터널 주소를 30초 안에 받지 못했습니다. 로그: $TunnelLog"
        }
    }
}

Write-Host "`n시연 준비 완료. 종료: powershell -ExecutionPolicy Bypass -File scripts\stop_demo.ps1" -ForegroundColor Green
