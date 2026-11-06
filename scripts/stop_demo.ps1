# 시연 종료: start_demo.ps1이 켠 창을 닫고 DB를 내립니다 (DB 데이터는 유지)
# 실행: powershell -ExecutionPolicy Bypass -File scripts\stop_demo.ps1
. (Join-Path $PSScriptRoot 'common.ps1')

Write-Step '실행한 프로그램 종료'
$pids = Load-Pids
if ($pids.Count -eq 0) { Write-Skip '기록된 프로그램 없음' }
foreach ($name in @('tunnel', 'ue5', 'signaling', 'web', 'api')) {
    if (-not $pids.ContainsKey($name)) { continue }
    $id = $pids[$name]
    if (Get-Process -Id $id -ErrorAction SilentlyContinue) {
        & taskkill.exe /PID $id /T /F *> $null   # 자식 프로세스(node 등)까지
        Write-Ok "$name 종료 (PID $id)"
    } else {
        Write-Skip "$name 은(는) 이미 종료됨"
    }
}
Remove-Item $PidFile, $TunnelLog -ErrorAction SilentlyContinue

Write-Step 'PostgreSQL 종료'
$docker = Get-Docker
if (-not $docker) {
    Write-Skip 'Docker 없음'
} else {
    & $docker compose -f (Join-Path $ServerDir 'docker-compose.yml') down
    if ($LASTEXITCODE -eq 0) { Write-Ok 'DB 종료 (데이터는 유지)' } else { Write-Bad 'DB 종료 실패 (Docker Desktop이 켜져 있는지 확인)' }
}
